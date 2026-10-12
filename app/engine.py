"""Background polling loops and the live snapshot they maintain."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from . import market
from .alerts import AlertMonitor, new_rule
from .config import Settings, UserState
from .dilution import DilutionProfiles
from .dilution import summary as risk_summary
from .filings import FilingsFeed
from .finnhub import FinnhubFeed
from .halts import HaltFeed, halt_messages
from .health import SourceError, SourceHealth, describe
from .news import NewsFeed
from .notify import PhoneNotifier, from_alert, from_filing, from_news, from_outage
from .sec import SecClient

log = logging.getLogger(__name__)

TICK_FLUSH_SECONDS = 0.5  # streamed trades are batched into at most 2 broadcasts per second
HEALTH_CHECK_SECONDS = 5.0
RISK_INTERVAL = 60.0      # how often the risk loop looks for symbols without a fresh profile
RISK_RETRY = 900.0        # after a failed profile, wait this long before trying the symbol again
RISK_ALERT_WAIT = 8.0     # a filing alert waits at most this long for the stock's risk level


class Hub:
    """Connected browsers; every update is pushed to all of them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, ws: WebSocket, msg: dict) -> None:
        await ws.send_text(json.dumps(msg, default=str))

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await self.send(ws, msg)
            except Exception:
                self.clients.discard(ws)


class Engine:
    def __init__(self, settings: Settings, state: UserState, hub: Hub, health: SourceHealth | None = None):
        self.settings = settings
        self.state = state
        self.hub = hub
        self.sec = SecClient(settings.sec_user_agent)  # the only client that carries the contact address
        self.news = NewsFeed(settings, self.sec)
        self.filings = FilingsFeed(settings, self.sec) if settings.sec_filings else None
        self.dilution = DilutionProfiles(self.sec)
        self.risk: dict[str, dict | None] = {}  # symbol -> dilution summary; None = not an SEC filer
        self._risk_failed: dict[str, float] = {}  # symbol -> monotonic time of the last failure
        self._wake_risk = asyncio.Event()
        self.halts = HaltFeed()
        self.phone = PhoneNotifier(settings, on_status=self._phone_status)
        self._ticks = asyncio.Event()  # set by the Finnhub feed whenever a price changes
        self.finnhub = FinnhubFeed(settings.finnhub_api_key, self._ticks.set) if settings.finnhub_api_key else None
        self.yahoo_quotes: list[dict] = []
        self.movers: dict[str, list[dict]] = {key: [] for key in market.SCREENS}
        self.unusual: list[dict] = []
        self.alerts = AlertMonitor()
        self.health = health or SourceHealth()
        self.health.register("quotes", "Yahoo prices", settings.quotes_interval)
        self.health.register("movers", "Yahoo movers", settings.movers_interval)
        self.health.register("news", "News feeds", settings.news_interval)
        self.health.register("halts", "Nasdaq halts", settings.halts_interval)
        if self.filings:
            self.health.register("filings", "SEC filings", settings.filings_interval)
        if self.finnhub:
            self.health.register("finnhub", "Finnhub stream", HEALTH_CHECK_SECONDS)
        self._health_seen: list = []  # what the browsers were last told
        self._wake_quotes = asyncio.Event()
        self._wake_news = asyncio.Event()
        self._wake_finnhub = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    @property
    def quotes(self) -> list[dict]:
        """Watchlist rows: Yahoo volume/RVOL, with Finnhub real-time prices when available."""
        if self.finnhub is None:
            return self.yahoo_quotes
        by_symbol = {q["symbol"]: q for q in self.yahoo_quotes}
        return [self.finnhub.overlay(by_symbol.get(sym, {"symbol": sym})) for sym in self.state.watchlist]

    def snapshot(self) -> dict:
        return {
            **self._config(),
            "quotes": self.quotes,
            "movers": self.movers,
            "unusual": self.unusual,
            "news": self._latest_news(),
            "alerts": list(self.alerts.recent),
            "halts": self.halts.latest(),
            "phone": self.phone.status(),
            "rvol_threshold": self.settings.rvol_threshold,
            "price_source": "finnhub" if self.finnhub else "yahoo",
            "health": self._health(),
            "risk": self.risk,
        }

    async def start(self) -> None:
        s = self.settings
        self._tasks = [
            asyncio.create_task(self._loop("quotes", s.quotes_interval, self._update_quotes, self._wake_quotes)),
            asyncio.create_task(self._loop("movers", s.movers_interval, self._update_movers)),
            asyncio.create_task(self._loop("news", s.news_interval, self._update_news, self._wake_news)),
            asyncio.create_task(self._loop("halts", s.halts_interval, self._update_halts)),
            asyncio.create_task(self.phone.run()),
            asyncio.create_task(self._watch_health()),
            asyncio.create_task(self._loop("risk", RISK_INTERVAL, self._update_risk, self._wake_risk)),
        ]
        if self.filings:
            self._tasks.append(asyncio.create_task(
                self._loop("filings", s.filings_interval, self._update_filings)))
        if self.finnhub:
            await self.finnhub.set_symbols(self.state.watchlist)
            self._tasks += [
                asyncio.create_task(self.finnhub.run()),
                asyncio.create_task(self._loop("finnhub quotes", s.finnhub_quotes_interval,
                                               self.finnhub.refresh_quotes, self._wake_finnhub)),
                asyncio.create_task(self._flush_ticks()),
            ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.news.aclose()
        await self.sec.aclose()
        await self.halts.aclose()
        await self.phone.aclose()
        if self.finnhub:
            await self.finnhub.aclose()

    # --- watchlist / keywords -------------------------------------------------------

    async def add_symbol(self, symbol: str) -> bool:
        if symbol in self.state.watchlist:
            return False
        self.state.watchlist.append(symbol)
        await self._config_changed()
        await self._watchlist_changed()
        self._wake_news.set()
        return True

    async def remove_symbol(self, symbol: str) -> bool:
        if symbol not in self.state.watchlist:
            return False
        self.state.watchlist.remove(symbol)
        self.yahoo_quotes = [q for q in self.yahoo_quotes if q["symbol"] != symbol]
        await self._config_changed()
        await self._watchlist_changed()
        return True

    async def add_keyword(self, keyword: str) -> bool:
        if keyword.lower() in (k.lower() for k in self.state.keywords):
            return False
        self.state.keywords.append(keyword)
        await self._config_changed()
        return True

    async def remove_keyword(self, keyword: str) -> bool:
        match = next((k for k in self.state.keywords if k.lower() == keyword.lower()), None)
        if match is None:
            return False
        self.state.keywords.remove(match)
        await self._config_changed()
        return True

    # --- ticker detail ----------------------------------------------------------------

    async def ticker_detail(self, symbol: str, range_: str = "1d") -> dict | None:
        """Yahoo profile + intraday bars + latest known quote for one symbol. News, halts and
        alerts already stream to the browser, which filters them itself so they stay live.
        None if Yahoo has neither a profile nor bars for the symbol."""
        profile, chart = await asyncio.gather(
            asyncio.to_thread(market.fetch_profile, symbol),
            asyncio.to_thread(market.fetch_chart, symbol, range_),
            return_exceptions=True,
        )
        errors = []
        if isinstance(profile, BaseException):
            log.warning("profile for %s failed: %s", symbol, profile)
            profile, errors = {}, errors + ["profile"]
        if isinstance(chart, BaseException):
            log.warning("chart for %s failed: %s", symbol, chart)
            chart, errors = {"range": range_, "bars": []}, errors + ["chart"]
        if not errors and not profile and not chart["bars"]:
            return None

        rows = [*self.quotes, *(r for rows in self.movers.values() for r in rows)]
        quote = next((r for r in rows if r["symbol"] == symbol and r.get("price") is not None), None)
        if quote is None and range_ == "1d":
            quote = market.quote_from_bars(symbol, chart["bars"], profile)
        return {"symbol": symbol, "profile": profile, "chart": chart, "quote": quote, "errors": errors}

    async def add_rule(self, symbol: str, kind: str, value: float) -> dict:
        rule = new_rule(symbol, kind, value)
        self.state.alert_rules.append(rule)
        await self._config_changed()
        return rule

    async def remove_rule(self, rule_id: str) -> bool:
        rule = next((r for r in self.state.alert_rules if r["id"] == rule_id), None)
        if rule is None:
            return False
        self.state.alert_rules.remove(rule)
        self.alerts.forget_rule(rule_id)
        await self._config_changed()
        return True

    async def set_unusual_alerts(self, enabled: bool) -> None:
        self.state.unusual_alerts = enabled
        await self._config_changed()

    async def set_halt_alerts(self, mode: str) -> None:
        self.state.halt_alerts = mode
        await self._config_changed()

    async def set_phone_alerts(self, categories: dict[str, bool]) -> None:
        self.state.phone_alerts.update(categories)
        await self._config_changed()

    async def _phone_status(self, status: dict) -> None:
        await self.hub.broadcast({"type": "phone", "data": status})

    def _config(self) -> dict:
        return self.state.to_dict()

    async def _config_changed(self) -> None:
        self.state.save()
        await self.hub.broadcast({"type": "config", "data": self._config()})

    async def _watchlist_changed(self) -> None:
        self._wake_quotes.set()
        self._wake_risk.set()
        if self.finnhub:
            await self.finnhub.set_symbols(self.state.watchlist)
            self._wake_finnhub.set()

    # --- polling ----------------------------------------------------------------------

    async def _loop(self, name: str, interval: float, step: Callable[[], Awaitable[str | None]],
                    wake: asyncio.Event | None = None) -> None:
        """Run step every interval (or sooner when woken). If name is a tracked source, its
        health records the outcome: step returns a note on partial success, raises on failure."""
        while True:
            await self._run_step(name, step)
            if wake is None:
                await asyncio.sleep(interval)
                continue
            try:
                await asyncio.wait_for(wake.wait(), interval)
            except TimeoutError:
                pass
            wake.clear()

    async def _run_step(self, name: str, step: Callable[[], Awaitable[str | None]]) -> None:
        try:
            note = await step()
        except Exception as exc:
            if isinstance(exc, SourceError):
                log.warning("%s update failed: %s", name, exc)
            else:
                log.exception("%s update failed", name)
            if name in self.health:
                self.health.fail(name, describe(exc))
        else:
            if name in self.health:
                self.health.ok(name, note)

    def _health(self) -> dict:
        # "now" lets the browser show ages without trusting its own clock.
        return {"now": self.health.now(), "sources": self.health.status()}

    async def _check_health(self) -> None:
        if self.finnhub:
            if self.finnhub.connected:
                self.health.ok("finnhub", self.finnhub.error)
            else:
                self.health.fail("finnhub", self.finnhub.error or "not connected yet")
        status = self.health.status()
        seen = [(s["name"], s["state"], s["reason"]) for s in status]
        if seen != self._health_seen:
            self._health_seen = seen
            await self.hub.broadcast({"type": "health", "data": self._health()})
        if self.settings.source_alert_minutes > 0:
            events = self.health.outages(self.settings.source_alert_minutes * 60)
            for e in events:
                log.warning("source %s %s: %s", e["label"], e["kind"], e["reason"] or "")
            self.phone.enqueue([from_outage(e) for e in events], self.state.phone_alerts)

    async def _watch_health(self) -> None:
        while True:
            try:
                await self._check_health()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(HEALTH_CHECK_SECONDS)

    async def _update_quotes(self) -> str | None:
        symbols = list(self.state.watchlist)
        rows = await asyncio.to_thread(market.fetch_quotes, symbols)
        if symbols and not rows:
            # yfinance reports most failures by returning nothing. Keep the last good rows on
            # screen (the health status marks them stale) rather than blanking the watchlist.
            raise SourceError("Yahoo returned no prices")
        got = {r["symbol"] for r in rows}
        missing = [sym for sym in symbols if sym not in got]
        previous = {q["symbol"]: q for q in self.yahoo_quotes}
        self.yahoo_quotes = rows + [{**previous[sym], "stale": True} for sym in missing if sym in previous]
        await self._publish_quotes()
        await self._update_unusual()
        return f"no data for {', '.join(missing)}" if missing else None

    async def _flush_ticks(self) -> None:
        while True:
            await self._ticks.wait()
            self._ticks.clear()
            await self._publish_quotes()
            await asyncio.sleep(TICK_FLUSH_SECONDS)

    async def _publish_quotes(self) -> None:
        quotes = self.quotes
        await self.hub.broadcast({"type": "quotes", "data": quotes})
        await self._publish_alerts(self.alerts.check_quotes(quotes, self.state.alert_rules))

    async def _publish_alerts(self, fired: list[dict]) -> None:
        if fired:
            log.info("alerts: %s", "; ".join(e["message"] for e in fired))
            await self.hub.broadcast({"type": "alerts", "data": fired})
            self.phone.enqueue([from_alert(e, self.settings.public_url) for e in fired], self.state.phone_alerts)

    async def _update_movers(self) -> str | None:
        fetched = await asyncio.to_thread(market.fetch_movers)
        if not fetched:
            raise SourceError("every Yahoo screener failed")
        self.movers.update(fetched)  # a failed screener keeps its previous list
        self._wake_risk.set()  # new movers may need risk profiles
        await self.hub.broadcast({"type": "movers", "data": self.movers})
        await self._update_unusual()
        failed = [key for key in market.SCREENS if key not in fetched]
        return f"screener failing: {', '.join(failed)}" if failed else None

    async def _update_unusual(self) -> None:
        pool = [*self.quotes, *(row for rows in self.movers.values() for row in rows)]
        self.unusual = market.find_unusual(pool, self.settings.rvol_threshold, self.settings.min_volume)
        await self.hub.broadcast({"type": "unusual", "data": self.unusual})
        # Until the screeners load, the list only covers the watchlist; using that as the
        # baseline would make every mover look new a few seconds after startup.
        if any(self.movers.values()):
            await self._publish_alerts(self.alerts.check_unusual(self.unusual, self.state.unusual_alerts))

    async def _update_halts(self) -> None:
        changed, new, resumption_set = await self.halts.poll()
        await self._apply_halts(changed, new, resumption_set)

    async def _apply_halts(self, changed: bool, new: list[dict], resumption_set: list[dict]) -> None:
        if changed:
            await self.hub.broadcast({"type": "halts", "data": self.halts.latest()})
        messages = halt_messages(new, resumption_set, self.state.watchlist, self.state.halt_alerts)
        await self._publish_alerts(self.alerts.record(
            [self.alerts.event(halt, kind, message) for halt, kind, message in messages]))

    async def _update_news(self) -> str | None:
        fresh = await self.news.poll(list(self.state.watchlist), list(self.state.keywords))
        if fresh:
            await self.hub.broadcast({"type": "news", "data": fresh})
            self.phone.enqueue([from_news(n) for n in fresh if n["alert"]], self.state.phone_alerts)
        total, failing = self.news.source_count, self.news.failing
        if total and len(self.news.failed) == total:
            raise SourceError(f"all {total} feeds failed")
        return f"{len(failing)} of {total} feeds failing: {', '.join(failing)}" if failing else None

    def _latest_news(self, limit: int = 300) -> list[dict]:
        items = self.news.latest(limit) + (self.filings.latest() if self.filings else [])
        return sorted(items, key=lambda n: n["published"], reverse=True)[:limit]

    def _movers_by_symbol(self) -> dict[str, str]:
        """Symbols on today's movers lists, with why: a filing on one of them is worth an alert."""
        names = {"gainers": "top gainer", "losers": "top loser", "active": "most active",
                 "small_caps": "small-cap gainer"}
        hot: dict[str, str] = {}
        for key, rows in self.movers.items():
            for row in rows:
                pct = row.get("change_pct")
                hot.setdefault(row["symbol"], names.get(key, key) + (f" {pct:+.0f}%" if pct is not None else ""))
        for row in self.unusual:
            hot.setdefault(row["symbol"], f"unusual volume {row['rvol']:.1f}×" if row.get("rvol") else "unusual volume")
        return hot

    async def _update_filings(self) -> str | None:
        fresh = await self.filings.poll(list(self.state.watchlist), self._movers_by_symbol())
        alerting = list(dict.fromkeys(n["tickers"][0] for n in fresh if n["alert"]))
        if alerting:
            # An alert is more useful with the risk level, but shouldn't wait long for it.
            levels = await asyncio.gather(*(self._risk_level(sym) for sym in alerting))
            by_symbol = dict(zip(alerting, levels))
            for n in fresh:
                n["filing"]["risk"] = by_symbol.get(n["tickers"][0])
        if fresh:
            await self.hub.broadcast({"type": "news", "data": fresh})
            self.phone.enqueue([from_filing(n, self.settings.public_url) for n in fresh if n["alert"]],
                               self.state.phone_alerts)
        failing = self.filings.failing
        return f"EDGAR feeds failing: {', '.join(failing)}" if failing else None

    # --- dilution risk ------------------------------------------------------------------

    def _risk_symbols(self) -> list[str]:
        """Watchlist first, then the movers lists where dilution matters most."""
        symbols = list(self.state.watchlist)
        for key in self.settings.risk_movers:
            symbols += [row["symbol"] for row in self.movers.get(key, [])]
        stocks = [s for s in dict.fromkeys(symbols) if not s.startswith("^") and "=" not in s]  # no indices/futures
        return stocks[:self.settings.risk_max_symbols]

    async def _risk_profile(self, symbol: str) -> dict | None:
        """Fetch (or reuse) a profile and publish its summary if it changed. Raises on failure."""
        try:
            profile = await self.dilution.get(symbol)
        except SourceError:
            raise  # SEC is unavailable, not this symbol's fault
        except Exception:
            self._risk_failed[symbol] = asyncio.get_running_loop().time()
            raise
        self._risk_failed.pop(symbol, None)
        summary = risk_summary(profile)
        if symbol not in self.risk or self.risk[symbol] != summary:
            self.risk[symbol] = summary
            await self.hub.broadcast({"type": "risk", "data": {symbol: summary}})
        return summary

    async def _risk_level(self, symbol: str) -> str | None:
        try:
            summary = await asyncio.wait_for(self._risk_profile(symbol), RISK_ALERT_WAIT)
        except Exception as exc:  # the alert goes out without it
            log.info("no dilution risk for %s alert: %s", symbol, describe(exc))
            return None
        return summary["level"] if summary else None

    async def _update_risk(self) -> None:
        """Keep risk summaries fresh for the watched and moving stocks, one symbol at a time
        so SEC requests for news and filings aren't held up."""
        now = asyncio.get_running_loop().time()
        for symbol in self._risk_symbols():
            if self.dilution.is_fresh(symbol) and symbol in self.risk:
                continue
            if now - self._risk_failed.get(symbol, -RISK_RETRY) < RISK_RETRY:
                continue
            try:
                await self._risk_profile(symbol)
            except SourceError as exc:
                log.warning("dilution risk paused: %s", exc)
                return  # SEC itself is unavailable; the next round tries again
            except Exception as exc:
                log.info("dilution risk for %s failed: %s", symbol, describe(exc))
