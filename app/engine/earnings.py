"""Earnings dates for the watchlist: the ER badges, and lines for the brief and the recap. The
rules are in app/earnings.py."""
from __future__ import annotations

import asyncio
import logging

from .. import market
from ..clock import clock
from ..earnings import brief_lines, recap_lines

log = logging.getLogger(__name__)

EARNINGS_INTERVAL = 600.0   # how often the loop checks whether a refresh is due
EARNINGS_REFRESH = 6 * 3600  # dates move rarely; a few lookups a day are plenty


class EarningsMixin:
    """Owns earnings (symbol -> next report) and when it was last fetched, for which symbols."""

    def _init_earnings(self) -> None:
        self.earnings: dict[str, dict] = {}
        self._earnings_fetched: tuple[float, frozenset] | None = None
        self._wake_earnings = asyncio.Event()

    def _earnings_symbols(self) -> list[str]:
        return [s for s in self.state.watchlist if not s.startswith("^") and "=" not in s]

    async def _update_earnings(self) -> None:
        symbols = self._earnings_symbols()
        now = clock.time()
        if self._earnings_fetched:
            at, fetched_for = self._earnings_fetched
            if fetched_for == frozenset(symbols) and now - at < EARNINGS_REFRESH:
                return
        rows = await self._call("fetch_earnings", market.fetch_earnings, symbols)
        self._earnings_fetched = (now, frozenset(symbols))
        earnings = {r["symbol"]: r for r in rows}
        if earnings != self.earnings:
            self.earnings = earnings
            await self.hub.broadcast({"type": "earnings", "data": self.earnings})

    def _earnings_brief(self) -> list[str]:
        return brief_lines(self.earnings, self.state.watchlist, clock.today(market.ET))

    def _earnings_recap(self) -> list[str]:
        return recap_lines(self.earnings, self.state.watchlist, clock.today(market.ET))
