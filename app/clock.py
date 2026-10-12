"""The app's wall clock. Normally the real time; during a replay, the recorded day's time,
optionally running faster (`speed`).

Everything that asks "what time is it in the market?" uses this: sessions, "today", alert
cooldowns, reaction horizons, the recap schedule. Timers that only measure real elapsed time
(rate limits, caches, data-source health) keep using time.monotonic.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import date, datetime, tzinfo


class Clock:
    def __init__(self):
        self.speed = 1.0
        self._virtual_start: float | None = None  # epoch seconds the replay starts at
        self._real_start = 0.0                     # time.monotonic() when it started

    @property
    def replaying(self) -> bool:
        return self._virtual_start is not None

    def start_replay(self, at: float, speed: float = 1.0) -> None:
        self._virtual_start, self._real_start, self.speed = at, _time.monotonic(), speed

    def freeze(self, at: float) -> None:
        """Stop the clock at `at` (tests that step through a day)."""
        self._virtual_start, self._real_start, self.speed = at, _time.monotonic(), 0.0

    def reset(self) -> None:
        self._virtual_start, self.speed = None, 1.0

    def time(self) -> float:
        if self._virtual_start is None:
            return _time.time()
        return self._virtual_start + (_time.monotonic() - self._real_start) * self.speed

    def now(self, tz: tzinfo | None = None) -> datetime:
        return datetime.fromtimestamp(self.time(), tz)

    def today(self, tz: tzinfo | None = None) -> date:
        return self.now(tz).date()

    def real_seconds(self, seconds: float) -> float:
        """How long `seconds` of clock time takes in real time (a frozen clock: no wait)."""
        return seconds / self.speed if self.speed else 0.0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, self.real_seconds(seconds)))

    async def sleep_until(self, epoch: float) -> None:
        await self.sleep(epoch - self.time())


clock = Clock()
