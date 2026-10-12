"""Watchlist quotes (Yahoo, with Finnhub real-time prices when configured), top movers,
unusual volume, and the price/volume alert rules they feed."""
from __future__ import annotations

import asyncio

from .. import market
from ..health import SourceError

TICK_FLUSH_SECONDS = 0.5  # streamed trades are batched into at most 2 broadcasts per second
MOVER_NAMES = {"gainers": "top gainer", "losers": "top loser", "active": "most active",
               "small_caps": "small-cap gainer"}


class PricesMixin:
    """Owns yahoo_quotes, movers, unusual and the Finnhub tick signal."""

    def _init_prices(self) -> None:
        self.yahoo_quotes: list[dict] = []
        self.movers: dict[str, list[dict]] = {key: [] for key in market.SCREENS}
        self.unusual: list[dict] = []
        self._ticks = asyncio.Event()  # set by the Finnhub feed whenever a price changes
        self._wake_quotes = asyncio.Event()
        self._wake_finnhub = asyncio.Event()

    @property
    def quotes(self) -> list[dict]:
        """Watchlist rows: Yahoo volume/RVOL, with Finnhub real-time prices when available."""
        if self.finnhub is None:
            return self.yahoo_quotes
        by_symbol = {q["symbol"]: q for q in self.yahoo_quotes}
        return [self.finnhub.overlay(by_symbol.get(sym, {"symbol": sym})) for sym in self.state.watchlist]

    async def _update_quotes(self) -> str | None:
        symbols = list(self.state.watchlist)
        rows = await self._call("fetch_quotes", market.fetch_quotes, symbols)
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

    async def _update_movers(self) -> str | None:
        fetched = await self._call("fetch_movers", market.fetch_movers)
        if not fetched:
            raise SourceError("every Yahoo screener failed")
        self.movers.update(fetched)  # a failed screener keeps its previous list
        self._wake_risk.set()  # new movers may need risk profiles
        self._wake_gaps.set()  # and are candidates for the gap scan
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

    def _movers_by_symbol(self) -> dict[str, str]:
        """Symbols on today's movers lists, with why: a filing on one of them is worth an alert."""
        hot: dict[str, str] = {}
        for key, rows in self.movers.items():
            for row in rows:
                pct = row.get("change_pct")
                hot.setdefault(row["symbol"], MOVER_NAMES.get(key, key) + (f" {pct:+.0f}%" if pct is not None else ""))
        for row in self.unusual:
            hot.setdefault(row["symbol"], f"unusual volume {row['rvol']:.1f}×" if row.get("rvol") else "unusual volume")
        return hot
