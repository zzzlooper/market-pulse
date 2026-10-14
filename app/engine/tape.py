"""Today's tape for the watched and moving stocks: the short sale restriction (SSR badges, rules
in app/ssr.py), float rotation (ROT badges, and an alert when a stock trades its whole float) and
breakouts (alerts, rules in app/breakouts.py)."""
from __future__ import annotations

import asyncio

from .. import market
from ..breakouts import Breakouts
from ..clock import clock
from ..ssr import carried_over, triggered
from ..warrants import shares_text

TAPE_INTERVAL = 60.0    # how often new symbols get their daily bars and float looked up
TAPE_MAX_SYMBOLS = 150  # watchlist, gappers and movers
FLOAT_BATCH = 20        # float lookups per round: one Yahoo request each
TAPE_TRIES = 3          # a symbol Yahoo has nothing on is given up on for the day after this
ROTATION = 1.0          # volume / float that counts as having traded the whole float
DAY2_MIN = 0.30         # the last session's close-to-close gain that makes today day 2


def day2_move(days: list[dict], today: str) -> dict | None:
    """From daily bars ({"date", "low", "close"}, oldest first): the last session's gain if it was
    a big one, so today is its day 2."""
    past = [d for d in days if d["date"] < today and d["close"]]
    if len(past) < 2:
        return None
    before, last = past[-2], past[-1]
    gain = last["close"] / before["close"] - 1
    return {"date": last["date"], "change_pct": round(gain * 100, 1)} if gain >= DAY2_MIN else None


class TapeMixin:
    """Owns ssr (symbol -> {"since", "today"}), floats (symbol -> shares), day2 (yesterday's big
    gainers) and the rotation and breakout alerts."""

    def _init_tape(self) -> None:
        self.ssr: dict[str, dict] = {}            # in effect today; "today": triggered today, so tomorrow too
        self.floats: dict[str, int] = {}          # symbol -> shares in the public float, when Yahoo knows it
        self.day2: dict[str, dict] = {}           # symbol -> {"date", "change_pct"}: a big gainer last session
        self._tape_day: str | None = None
        self._tape_tries: dict[tuple[str, str], int] = {}  # (what, symbol) -> lookups today
        self._tape_done: set[tuple[str, str]] = set()      # (what, symbol) looked up today
        self._rotation_last: dict[str, float] = {}         # symbol -> last volume / float seen
        self._rotation_fired: set[str] = set()             # today
        self.breakouts = Breakouts()
        self._wake_tape = asyncio.Event()

    def _tape_today(self) -> str:
        """Today's date (ET); a new day starts over: yesterday's SSR is looked up again from the bars."""
        today = clock.today(market.ET).isoformat()
        if today != self._tape_day:
            self._tape_day = today
            self.ssr, self.day2, self._tape_tries, self._tape_done = {}, {}, {}, set()
            self._rotation_last, self._rotation_fired = {}, set()
        return today

    def _tape_symbols(self) -> list[str]:
        symbols = [*self.state.watchlist, *(g["symbol"] for g in self.gaps)]
        for rows in self.movers.values():
            symbols += [row["symbol"] for row in rows]
        stocks = [s for s in dict.fromkeys(symbols) if not s.startswith("^") and "=" not in s]  # no indices/futures
        return stocks[:TAPE_MAX_SYMBOLS]

    def _tape_todo(self, what: str, symbols: list[str]) -> list[str]:
        return [s for s in symbols if (what, s) not in self._tape_done]

    def _tape_got(self, what: str, asked: list[str], got: set[str]) -> None:
        for s in asked:
            self._tape_tries[(what, s)] = tries = self._tape_tries.get((what, s), 0) + 1
            if s in got or tries >= TAPE_TRIES:
                self._tape_done.add((what, s))

    async def _update_tape(self) -> None:
        """Look up what's missing for today's symbols: the last sessions' bars (an SSR carried
        over from yesterday) and the float."""
        today = self._tape_today()
        symbols = self._tape_symbols()
        if todo := self._tape_todo("daily", symbols):
            rows = await self._call("fetch_daily", market.fetch_daily, todo)
            self._tape_got("daily", todo, {r["symbol"] for r in rows})
            carried = {r["symbol"]: since for r in rows if (since := carried_over(r["days"], today))}
            new = {s: {"since": since, "today": False} for s, since in carried.items() if s not in self.ssr}
            if new:
                self.ssr.update(new)
                await self.hub.broadcast({"type": "ssr", "data": dict(self.ssr)})
            if runners := {r["symbol"]: move for r in rows if (move := day2_move(r["days"], today))}:
                self.day2.update(runners)
                await self.hub.broadcast({"type": "day2", "data": dict(self.day2)})
        if todo := self._tape_todo("float", symbols)[:FLOAT_BATCH]:
            rows = await self._call("fetch_floats", market.fetch_floats, todo)
            self._tape_got("float", todo, {r["symbol"] for r in rows})
            new = {r["symbol"]: int(r["float_shares"]) for r in rows
                   if r.get("float_shares") and self.floats.get(r["symbol"]) != int(r["float_shares"])}
            if new:
                self.floats.update(new)
                await self.hub.broadcast({"type": "floats", "data": dict(self.floats)})

    async def _check_tape(self, rows: list[dict]) -> None:
        """New prices: has a stock tripped SSR today, or traded its whole float?"""
        today = self._tape_today()
        regular = market.is_regular_bar(int(clock.time()))
        ssr_changed, fired = False, []
        for row in rows:
            symbol = row.get("symbol")
            if not symbol or row.get("session") not in (None, today):
                continue  # before the open, a quote still describes the last session
            if row.get("session") == today and not self.ssr.get(symbol, {}).get("today") and (
                    triggered(row.get("low"), row.get("prev_close"))
                    or (regular and triggered(row.get("price"), row.get("prev_close")))):
                self.ssr[symbol] = {"since": today, "today": True}
                ssr_changed = True
            float_shares, volume = self.floats.get(symbol), row.get("volume")
            if not float_shares or not volume:
                continue
            rotation = volume / float_shares
            before, self._rotation_last[symbol] = self._rotation_last.get(symbol), rotation
            if (before is not None and before < ROTATION <= rotation and symbol not in self._rotation_fired
                    and self.state.rotation_alerts):
                self._rotation_fired.add(symbol)
                fired.append(self.alerts.event(row, "rotation", (
                    f"{symbol} has traded its whole float: {shares_text(volume)} shares today, "
                    f"{rotation:.1f}× the {shares_text(float_shares)}-share float")))
        if ssr_changed:
            await self.hub.broadcast({"type": "ssr", "data": dict(self.ssr)})
        if fired:
            await self._publish_alerts(self.alerts.record(fired))
        await self._check_breakouts([r for r in rows if r.get("session") in (None, today)], regular=regular)

    async def _check_breakouts(self, rows: list[dict], regular: bool) -> None:
        """Feed today's prices to the breakout rules; alert on what they find, unless switched off."""
        today, now, fired = self._tape_today(), clock.time(), []
        for row in rows:
            symbol, price = row.get("symbol"), row.get("price")
            if not symbol or price is None or symbol.startswith("^") or "=" in symbol:
                continue
            found = self.breakouts.update(symbol, price, day=today, now=now, regular=regular,
                                          high=row.get("high") if regular else None,
                                          pre_high=self.pre_highs.get(symbol))
            if self.state.breakout_alerts:
                fired += [self.alerts.event(row, kind, message) for kind, message in found]
        if fired:
            await self._publish_alerts(self.alerts.record(fired))
