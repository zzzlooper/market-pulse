"""Health of each data source: when it last delivered, and what went wrong if it didn't.

A source is judged by the age of its last good update, not only by errors. A yfinance call
that hangs never raises, so its polling loop just stops; data older than a few polling
intervals counts as down, whatever the reason.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

STALE_INTERVALS = 3      # no good update for this many polling intervals = down
MIN_STALE_SECONDS = 60


class SourceError(Exception):
    """A source answered but gave nothing usable. Logged without a traceback."""


def describe(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__)[:200]


@dataclass
class _Source:
    label: str
    stale_after: float
    since: float                    # when tracking started; "starting" until the first result
    last_ok: float | None = None
    error: str | None = None        # the last attempt's error; None if it succeeded
    note: str | None = None         # partial success, e.g. "no data for XYZ"
    down_since: float | None = None
    alerted: bool = False           # a "down" alert went out and recovery hasn't been sent


class SourceHealth:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._sources: dict[str, _Source] = {}

    def now(self) -> float:
        return self._clock()

    def __contains__(self, name: str) -> bool:
        return name in self._sources

    def register(self, name: str, label: str, interval: float) -> None:
        stale_after = max(interval * STALE_INTERVALS, MIN_STALE_SECONDS)
        self._sources[name] = _Source(label, stale_after, self._clock())

    def ok(self, name: str, note: str | None = None) -> None:
        src = self._sources[name]
        src.last_ok, src.error, src.note = self._clock(), None, note

    def fail(self, name: str, error: str) -> None:
        self._sources[name].error = error[:200] or "failed"

    def state(self, name: str) -> str:
        """starting | ok | degraded (partial data, or retrying after a recent success) | down"""
        src = self._sources[name]
        if self._clock() - (src.last_ok or src.since) > src.stale_after:
            return "down"
        if src.last_ok is None:
            return "down" if src.error else "starting"
        return "degraded" if src.error or src.note else "ok"

    def status(self) -> list[dict]:
        out = []
        for name, src in self._sources.items():
            state = self.state(name)
            reason = src.error or src.note
            if state == "down" and not src.error:
                reason = f"no update in over {src.stale_after:.0f}s"
            out.append({"name": name, "label": src.label, "state": state,
                        "last_ok": src.last_ok, "reason": reason})
        return out

    def outages(self, alert_after: float) -> list[dict]:
        """Sources that have now been down for alert_after seconds, and ones that came back
        after such an alert. Each outage is reported once; call this periodically.
        "minutes" is the time without data for a down event, the outage length for a recovery."""
        now, events = self._clock(), []
        for name, src in self._sources.items():
            if self.state(name) == "down":
                src.down_since = src.down_since or now
                if not src.alerted and now - src.down_since >= alert_after:
                    src.alerted = True
                    events.append({"kind": "down", "label": src.label,
                                   "reason": self.status_of(name)["reason"],
                                   "minutes": round((now - (src.last_ok or src.since)) / 60)})
            else:
                if src.alerted:
                    events.append({"kind": "recovered", "label": src.label, "reason": None,
                                   "minutes": round((now - src.down_since) / 60)})
                src.down_since, src.alerted = None, False
        return events

    def status_of(self, name: str) -> dict:
        return next(s for s in self.status() if s["name"] == name)
