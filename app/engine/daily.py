"""The end-of-day recap: building it, and sending it on weekdays after the close. The message
itself is built in app/recap.py."""
from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime

from .. import market
from ..clock import clock
from ..recap import build_recap, next_run

log = logging.getLogger(__name__)


class DailyMixin:
    """Holds no state of its own: reads quotes, halts, health and reactions."""

    async def recap_note(self, measure_first: bool = True) -> dict:
        today = clock.today(market.ET)
        if measure_first:
            await self._update_reactions()  # closes measured at the last moment make it in
        start = int(datetime.combine(today, dtime(0), market.ET).timestamp())
        halts = [h for h in self.halts.latest() if h["halted_at"][:10] == today.isoformat()]
        problems = [s["label"] for s in self.health.status() if s["state"] == "down"]
        return build_recap(today, list(self.quotes), halts, self.reactions.since(start),
                           set(self.state.watchlist), problems, self.settings.public_url)

    async def _traded_today(self) -> bool:
        bars = (await self._call("fetch_chart", market.fetch_chart, "SPY", "1d"))["bars"]
        today = clock.today(market.ET)
        return any(market.is_regular_bar(b["t"]) and datetime.fromtimestamp(b["t"], market.ET).date() == today
                   for b in bars)

    async def _recap_loop(self) -> None:
        while True:
            run = next_run(clock.now(market.ET), self.settings.recap_time)
            await clock.sleep_until(run.timestamp())
            try:
                if await self._traded_today():
                    self.phone.enqueue([await self.recap_note()], self.state.phone_alerts)
                    log.info("end-of-day recap queued")  # its contents stay out of the log
                else:
                    log.info("no regular session today; recap skipped")
            except Exception:
                log.exception("end-of-day recap failed")
            await clock.sleep(60)
