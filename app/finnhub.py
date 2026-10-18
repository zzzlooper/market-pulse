"""Real-time US stock prices from Finnhub (free tier: WebSocket trades + REST quotes).

Finnhub supplies the last price and previous close. Volume and RVOL still come from Yahoo:
summing streamed trades would only count volume since the app started.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from websockets.asyncio.client import connect

log = logging.getLogger(__name__)

WS_URL = "wss://ws.finnhub.io"
REST_URL = "https://finnhub.io/api/v1"
MAX_SYMBOLS = 50  # free-tier WebSocket subscription limit


def to_finnhub(symbol: str) -> str | None:
    """Yahoo-style symbol -> Finnhub symbol, or None if the US trades feed doesn't carry it."""
    if any(c in symbol for c in "^="):  # indices, futures, FX
        return None
    return symbol.replace("-", ".")  # BRK-B -> BRK.B


@dataclass
class LivePrice:
    price: float | None = None
    prev_close: float | None = None
    time: float = 0.0  # epoch seconds of the latest trade or quote


class FinnhubFeed:
    def __init__(self, api_key: str, on_update: Callable[[], None],
                 ws_url: str = WS_URL, rest_url: str = REST_URL):
        self.api_key = api_key
        self.ws_url = ws_url
        self.prices: dict[str, LivePrice] = {}   # keyed by our (Yahoo-style) symbol
        self._wanted: dict[str, str] = {}        # Finnhub symbol -> our symbol
        self._on_update = on_update
        self._ws = None
        self.connected = False
        self.error: str | None = None  # why the stream is down, or a problem it or REST reported
        self._client = httpx.AsyncClient(base_url=rest_url, timeout=10, headers={"X-Finnhub-Token": api_key})

    def _redact(self, text: object) -> str:
        """The connect URL carries the key, so keep it out of every log line and UI message."""
        text = str(text)
        return text.replace(self.api_key, "…") if self.api_key else text

    async def aclose(self) -> None:
        await self._client.aclose()

    async def set_symbols(self, symbols: list[str]) -> None:
        wanted = {}
        for sym in symbols:
            if (fsym := to_finnhub(sym)) and len(wanted) < MAX_SYMBOLS:
                wanted[fsym] = sym
        if len([s for s in symbols if to_finnhub(s)]) > MAX_SYMBOLS:
            log.warning("Finnhub streams at most %d symbols; the rest stay on delayed Yahoo prices", MAX_SYMBOLS)

        added = wanted.keys() - self._wanted.keys()
        removed = self._wanted.keys() - wanted.keys()
        for fsym in removed:
            self.prices.pop(self._wanted[fsym], None)
        self._wanted = wanted
        if self._ws is not None:
            for fsym in removed:
                await self._send("unsubscribe", fsym)
            for fsym in added:
                await self._send("subscribe", fsym)

    def overlay(self, row: dict) -> dict:
        """A Yahoo quote row with Finnhub's price (and change vs. previous close) swapped in."""
        live = self.prices.get(row["symbol"])
        if live is None or live.price is None:
            return {**row, "live": False}
        prev = live.prev_close or row.get("prev_close")
        change = live.price - prev if prev else None
        return {
            **row,
            "price": live.price,
            "change": change,
            "change_pct": change / prev * 100 if prev else None,
            "live": True,
            "price_time": live.time,
        }

    async def run(self) -> None:
        """Stream trades forever, reconnecting with backoff."""
        delay = 1
        while True:
            try:
                async with connect(f"{self.ws_url}?token={self.api_key}", ping_interval=20) as ws:
                    self._ws = ws
                    self.connected, self.error = True, None
                    log.info("Finnhub stream connected")
                    delay = 1
                    for fsym in list(self._wanted):
                        await self._send("subscribe", fsym)
                    async for raw in ws:
                        self.handle(json.loads(raw))
            except Exception as exc:
                log.warning("Finnhub stream disconnected (%s); retrying in %ds", self._redact(exc), delay)
                self.error = self._redact(f"stream disconnected: {exc}")[:200]
            finally:
                self._ws = None
                self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "error":
            log.error("Finnhub: %s", self._redact(msg.get("msg")))
            self.error = self._redact(msg.get("msg"))[:200]
        if kind != "trade":
            return  # also "ping"
        changed = False
        for trade in msg.get("data") or []:
            sym = self._wanted.get(trade.get("s"))
            if sym is None:
                continue
            ts = trade["t"] / 1000
            live = self.prices.setdefault(sym, LivePrice())
            if ts >= live.time:  # trades in a batch can arrive out of order
                live.price, live.time = trade["p"], ts
                changed = True
        if changed:
            self._on_update()

    async def refresh_quotes(self) -> None:
        """REST snapshot for every symbol: fills prices before the first trade (e.g. market
        closed) and keeps the previous close current across days. ~1 call per symbol."""
        for fsym, sym in list(self._wanted.items()):
            resp = await self._client.get("/quote", params={"symbol": fsym})
            if resp.status_code == 429:
                log.warning("Finnhub rate limit hit; quote refresh paused until next cycle")
                break
            if resp.status_code in (401, 403):
                log.error("Finnhub rejected the API key (HTTP %d); check finnhub_api_key", resp.status_code)
                self.error = f"API key rejected (HTTP {resp.status_code}); check finnhub_api_key"
                return
            resp.raise_for_status()
            q = resp.json()
            if not q.get("c") or self._wanted.get(fsym) != sym:  # unknown symbol (all zeros) or removed meanwhile
                continue
            live = self.prices.setdefault(sym, LivePrice())
            live.prev_close = q.get("pc") or live.prev_close
            if q.get("t", 0) >= live.time:
                live.price, live.time = q["c"], q["t"]
        self._on_update()

    async def _send(self, kind: str, fsym: str) -> None:
        try:
            await self._ws.send(json.dumps({"type": kind, "symbol": fsym}))
        except Exception as exc:  # the run loop notices the dead socket and resubscribes
            log.debug("Finnhub %s %s failed: %s", kind, fsym, self._redact(exc))
