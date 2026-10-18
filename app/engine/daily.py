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

RECAP_CATCH_UP = 60  # seconds after a start past recap_time before sending the missed recap


class DailyMixin:
    """Reads quotes, halts, health and reactions; its one piece of state, the day the last
    recap went out, is kept in the user state (state.recap_sent)."""

    async def recap_note(self, measure_first: bool = True) -> dict:
        today = clock.today(market.ET)
        if measure_first:
            await self._update_reactions()  # closes measured at the last moment make it in
        start = int(datetime.combine(today, dtime(0), market.ET).timestamp())
        halts = [h for h in self.halts.latest() if h["halted_at"][:10] == today.isoformat()]
        problems = [s["label"] for s in self.health.status() if s["state"] == "down"]
        return build_recap(today, list(self.quotes), halts, self.reactions.since(start),
                           set(self.state.watchlist), problems, self.settings.public_url,
                           earnings=self._earnings_recap())

    async def _traded_today(self) -> bool:
        bars = (await self._call("fetch_chart", market.fetch_chart, "SPY", "1d"))["bars"]
        today = clock.today(market.ET)
        return any(market.is_regular_bar(b["t"]) and datetime.fromtimestamp(b["t"], market.ET).date() == today
                   for b in bars)

    def _recap_missed(self) -> bool:
        """Today is a weekday, its recap time has passed and its recap hasn't gone out: the app
        was down at recap time (or its recap failed)."""
        now = clock.now(market.ET)
        hour, minute = (int(x) for x in self.settings.recap_time.split(":"))
        due = datetime.combine(now.date(), dtime(hour, minute), market.ET)
        return now.weekday() < 5 and now >= due and self.state.recap_sent != now.date().isoformat()

    async def _send_recap(self) -> None:
        day = clock.today(market.ET).isoformat()
        try:
            if await self._traded_today():
                self.phone.enqueue([await self.recap_note()], self.state.phone_alerts)
                log.info("end-of-day recap queued")  # its contents stay out of the log
            else:
                log.info("no regular session today; recap skipped")
        except Exception:
            log.exception("end-of-day recap failed")
            return  # not marked as sent: a restart today tries again
        self.state.recap_sent = day
        self.state.save()

    async def _recap_loop(self) -> None:
        if self._recap_missed():
            await clock.sleep(RECAP_CATCH_UP)  # the first quotes and halts come in first
            await self._send_recap()
        while True:
            run = next_run(clock.now(market.ET), self.settings.recap_time)
            await clock.sleep_until(run.timestamp())
            await self._send_recap()
            await clock.sleep(60)
