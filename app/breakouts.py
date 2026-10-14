"""Breakouts: the moments momentum traders act on.

- Pre-market high: after the open, the price goes through the highest pre-market trade. The last
  pre-market price is the baseline, so a stock that opens straight through it counts too. Once
  a day per stock.
- New high of day after a pullback: the price makes a new regular-session high after falling
  at least a pullback (PULLBACK by default) below the previous one. Without it, a runner would alert on every
  tick up; with it, this is the classic "first pullback, then new high". At most once every
  HOD_COOLDOWN seconds per stock.

Only prices of today's session count; the first regular-hours reading of a stock is a baseline.
Prices come at the rate the app polls them (seconds with Finnhub, a minute or so with Yahoo), so
"a new high" means a new high among the readings, or Yahoo's own day high when it's higher.
"""
from __future__ import annotations

from dataclasses import dataclass

PULLBACK = 0.05      # how far below the high of day the price must have been before a new high counts
HOD_COOLDOWN = 900   # seconds between new-high alerts for one stock


def money(price: float) -> str:
    return f"${price:,.2f}" if price >= 1 else f"${price:.4f}"


@dataclass
class _Stock:
    last: float                 # the last price seen today, pre-market or regular
    hod: float | None = None    # regular-session high so far (None until the first regular reading)
    low: float | None = None    # the lowest price since that high
    pmh_done: bool = False
    hod_at: float = float("-inf")


class Breakouts:
    def __init__(self) -> None:
        self._day: str | None = None
        self._stocks: dict[str, _Stock] = {}

    def update(self, symbol: str, price: float, *, day: str, now: float, regular: bool,
               high: float | None = None, pre_high: float | None = None,
               min_pullback: float = PULLBACK) -> list[tuple[str, str]]:
        """A new price for symbol; returns the breakouts it makes as (kind, message)."""
        if day != self._day:
            self._day, self._stocks = day, {}
        s = self._stocks.get(symbol)
        if s is None:
            self._stocks[symbol] = s = _Stock(last=price)
            if regular:
                s.hod, s.low = max(price, high or price), price
            return []
        fired: list[tuple[str, str]] = []
        before, s.last = s.last, price
        if regular and pre_high and not s.pmh_done and before < pre_high <= price:
            s.pmh_done = True
            fired.append(("pmh", f"{symbol} broke its pre-market high of {money(pre_high)} (now {money(price)})"))
        if not regular:
            return fired
        if s.hod is None:  # first reading after the open
            s.hod, s.low = max(price, high or price), price
            return fired
        if price > s.hod:
            pullback = 1 - s.low / s.hod
            if pullback >= min_pullback and now - s.hod_at >= HOD_COOLDOWN and not fired:
                s.hod_at = now
                fired.append(("hod", (f"{symbol} made a new high of day at {money(price)}, after pulling back "
                                      f"{pullback * 100:.0f}% from {money(s.hod)} to {money(s.low)}")))
            s.hod, s.low = price, price
        else:
            s.low = min(s.low, price)
        if high and high > s.hod:  # a high came and went between two readings
            s.hod, s.low = high, price
        return fired
