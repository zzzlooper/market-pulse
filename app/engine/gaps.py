"""The pre-market gap scanner and its 09:15 brief. The gap rules are in app/gaps.py."""
from __future__ import annotations

import asyncio
import logging

from .. import market
from ..clock import clock
from ..gaps import build_brief, candidates, scanning, select
from ..recap import next_run

log = logging.getLogger(__name__)

GAP_INTERVAL = 60.0


class GapsMixin:
    """Owns gaps (today's gappers), the day they belong to, and whether anything traded."""

    def _init_gaps(self) -> None:
        self.gaps: list[dict] = []
        self._gaps_day = None
        self._gaps_traded = False  # any pre-market trade today: no brief on a holiday
        self._wake_gaps = asyncio.Event()  # new catalysts or movers: rescan now, not in a minute

    async def _update_gaps(self) -> str | None:
        now = clock.now(market.ET)
        if not scanning(now):
            return None  # idle outside 04:00–10:00 ET on weekdays; the list stays up for the day
        if self._gaps_day != now.date():
            self.gaps, self._gaps_day, self._gaps_traded = [], now.date(), False
        s = self.settings
        context = candidates(self.state.watchlist, self._latest_news(), self.movers, now, s.gap_max_symbols)
        # No rows is normal at 04:01 (nothing has traded yet) and all day on a holiday.
        rows = await self._call("fetch_premarket", market.fetch_premarket, list(context))
        self._gaps_traded = self._gaps_traded or bool(rows)
        self.gaps = select(rows, context, s.gap_min_pct, s.gap_min_minutes)
        await self.hub.broadcast({"type": "gaps", "data": self.gaps})
        return None

    def gap_brief(self) -> dict:
        return build_brief(clock.today(market.ET), self.gaps, self.risk, self.settings.gap_min_pct,
                           self.settings.public_url, earnings=self._earnings_brief())

    async def _gap_brief_loop(self) -> None:
        while True:
            run = next_run(clock.now(market.ET), self.settings.gap_brief_time)
            await clock.sleep_until(run.timestamp())
            try:
                await self._send_gap_brief()
            except Exception:
                log.exception("pre-market brief failed")
            await clock.sleep(60)

    async def _send_gap_brief(self) -> bool:
        """Queue the brief for the phone, unless nothing traded this morning (a holiday)."""
        await self._update_gaps()
        if self._gaps_day != clock.today(market.ET) or not self._gaps_traded:
            log.info("no pre-market trading today; brief skipped")
            return False
        self.phone.enqueue([self.gap_brief()], self.state.phone_alerts)
        log.info("pre-market brief queued")  # its contents stay out of the log
        return True
