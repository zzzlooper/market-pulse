"""Halt resumptions: an alert about a minute before a halted stock resumes trading, and another
once it has, with the first trade against the last one before the halt. Which halts count is the
halt-alert setting (watchlist, all stocks or off). The rules are in app/halts.py."""
from __future__ import annotations

import logging
from datetime import datetime

from .. import market
from ..clock import clock
from ..halts import relevant, resumed_message, resuming_message, resumption
from ..health import describe

log = logging.getLogger(__name__)

RESUME_TICK = 5.0        # how often the clock is checked against the resumption times
RESUME_WARN = 60.0       # the heads-up comes this long before trading resumes
RESUME_SETTLE = 60.0     # wait for the first minute's bar before looking for it
RESUME_RETRY = 30.0      # between chart downloads while the bar hasn't arrived
RESUME_GIVE_UP = 1800.0  # Yahoo's bars can come late, but not this late


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class ResumptionsMixin:
    """Owns halt_info (halt id -> how it resumed) and which halts were warned about or reported."""

    def _init_resumptions(self) -> None:
        self.halt_info: dict[str, dict] = {}
        self._resume_ahead: set[str] = set()     # halts seen with their resumption still to come
        self._resume_warned: set[str] = set()
        self._resume_done: set[str] = set()
        self._resume_next_try: dict[str, float] = {}

    async def _update_resumptions(self) -> None:
        now, fired = clock.time(), []
        for halt in self.halts.latest():
            hid = halt["id"]
            if not halt["resume_trade_at"] or hid in self._resume_done:
                continue
            if not relevant(halt, self.state.watchlist, self.state.halt_alerts):
                continue
            resumes = _ts(halt["resume_trade_at"])
            if now < resumes:
                self._resume_ahead.add(hid)  # a resumption that already happened at startup isn't news
                if resumes - now <= RESUME_WARN and hid not in self._resume_warned:
                    self._resume_warned.add(hid)
                    fired.append(self.alerts.event(halt, "resuming", resuming_message(halt)))
                continue
            if hid not in self._resume_ahead or now < resumes + RESUME_SETTLE:
                continue
            if now > resumes + RESUME_GIVE_UP:
                self._resume_done.add(hid)
                continue
            if now < self._resume_next_try.get(hid, 0):
                continue
            self._resume_next_try[hid] = now + RESUME_RETRY
            symbol = halt["symbol"].replace(".", "-")  # Nasdaq BRK.B is Yahoo BRK-B
            try:
                bars = (await self._call("fetch_chart", market.fetch_chart, symbol, "1d"))["bars"]
            except Exception as exc:  # tried again in RESUME_RETRY
                log.info("resumption of %s unknown yet: %s", halt["symbol"], describe(exc))
                continue
            if (info := resumption(halt, bars)) is None:
                continue
            self._resume_done.add(hid)
            self.halt_info[hid] = info
            await self.hub.broadcast({"type": "halt_info", "data": dict(self.halt_info)})
            fired.append(self.alerts.event({**halt, "price": info["open"], "change_pct": None}, "resumed",
                                           resumed_message(halt, info)))
        if fired:
            await self._publish_alerts(self.alerts.record(fired))
