"""The short sale restriction (SEC Rule 201, "SSR").

When a stock falls 10% or more below the previous close during regular hours, short sales may
not be made at or below the best bid for the rest of that day and all of the next trading day.
Shorts can't press the price down, so the stock tends to trade differently, and a squeeze
becomes likelier.

Worked out from prices, as an approximation: the rule is triggered by the national best bid,
seen here as the session's lowest trade. Only regular-hours prices count, as in the rule;
Yahoo's daily lows are regular hours. "The next trading day" is simply the next daily bar, so
weekends and market holidays need no calendar.
"""
from __future__ import annotations

DROP = 0.10


def triggered(low: float | None, prev_close: float | None) -> bool:
    """Did a session trading down to `low` trip the 10% circuit breaker?"""
    if low is None or not prev_close:
        return False
    return low <= round(prev_close * (1 - DROP), 6)


def carried_over(days: list[dict], today: str) -> str | None:
    """From daily bars ({"date", "low", "close"}, oldest first): the date of the last session
    before `today` if that session triggered it, so the restriction holds all of today."""
    past = [d for d in days if d["date"] < today]
    if len(past) < 2:
        return None
    before, last = past[-2], past[-1]
    return last["date"] if triggered(last["low"], before["close"]) else None
