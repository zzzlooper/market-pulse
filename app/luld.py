"""Limit up / limit down (LULD) bands, estimated: how close a stock is to a volatility pause.

During regular hours a stock may only trade within a band around its reference price, the
average price of the last 5 minutes. At a band it pauses: trading halts for 5 minutes if it
can't get back inside within 15 seconds (the LUDP halts in the Halts tab). The bands, under the
plan as amended in 2020:

    reference price   Tier 1 (S&P 500, Russell 1000)   Tier 2 (everything else)
    above $3.00       5%                               10%
    $0.75 – $3.00     20%                              20%
    below $0.75       the lesser of $0.15 or 75%       the lesser of $0.15 or 75%

doubled from 15:35 to 16:00 ET for Tier 1, and for Tier 2 at $3.00 or less. There is no doubling
at the open any more.

An estimate: the reference price here is the average of the prices this app saw in the last
5 minutes (a few readings with Yahoo, many with Finnhub), not of every trade, and Tier 1 is
guessed from the market cap. With Yahoo's delayed prices a warning can come after the pause.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time

from .market import ET

WINDOW = 300                 # seconds of prices averaged into the reference price
TIER1_CAP = 4e9              # about where the Russell 1000 ends: a guess at Tier 1 without the index lists
NEAR = 0.25                  # within this share of the band's width from a band counts as near it
CLOSE_DOUBLING = (time(15, 35), time(16, 0))


def band(ref: float, tier: int, at: datetime) -> float:
    """The band's half-width in dollars around reference price ref."""
    if ref > 3:
        width = ref * (0.05 if tier == 1 else 0.10)
    elif ref >= 0.75:
        width = ref * 0.20
    else:
        width = min(0.15, ref * 0.75)
    t = at.astimezone(ET).time()
    if CLOSE_DOUBLING[0] <= t < CLOSE_DOUBLING[1] and (tier == 1 or ref <= 3):
        width *= 2
    return width


def tier(market_cap: float | None) -> int:
    return 1 if market_cap and market_cap >= TIER1_CAP else 2


class Bands:
    """Per stock: the prices of the last 5 minutes, and the bands around their average."""

    def __init__(self) -> None:
        self._prices: dict[str, deque[tuple[float, float]]] = {}

    def update(self, symbol: str, price: float, now: float, tier_: int, near_share: float = NEAR) -> dict:
        """Add a regular-hours price; returns {"ref", "lower", "upper", "tier", "near", "away_pct"}.
        near is "up" or "down" within near_share of the band's width from a band (or past it), else
        None; away_pct is how far
        the price would have to move to reach the nearer band."""
        seen = self._prices.setdefault(symbol, deque())
        seen.append((now, price))
        while seen and seen[0][0] < now - WINDOW:
            seen.popleft()
        ref = sum(p for _, p in seen) / len(seen)
        width = band(ref, tier_, datetime.fromtimestamp(now, ET))
        lower, upper = max(ref - width, 0.0), ref + width
        to_upper, to_lower = upper - price, price - lower
        near = "up" if to_upper <= width * near_share else "down" if to_lower <= width * near_share else None
        away = min(to_upper, to_lower) / price * 100 if price else None
        return {"ref": round(ref, 4), "lower": round(lower, 4), "upper": round(upper, 4), "tier": tier_,
                "near": near, "away_pct": round(max(away, 0.0), 2) if away is not None else None}

    def forget(self) -> None:
        """A new session: yesterday's prices don't make today's reference."""
        self._prices.clear()
