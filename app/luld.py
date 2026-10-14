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

import json
import os
import statistics
from collections import deque
from datetime import datetime, time
from pathlib import Path

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


CHECKS_KEPT = 500


class Checks:
    """How the estimates compare with the official bands: each LULD pause in Nasdaq's halt feed
    carries the price that triggered it, compared with the band estimated just before. Kept in
    data/luld_checks.json, the last CHECKS_KEPT: the day, the side, the error and whether a
    warning came first. No tickers."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self._rows: list[dict] = json.loads(path.read_text())
        except (OSError, ValueError):
            self._rows = []

    def add(self, day: str, side: str, estimate: float, official: float, warned: bool) -> dict:
        row = {"day": day, "side": side, "error_pct": round((estimate / official - 1) * 100, 2), "warned": warned}
        self._rows = [*self._rows, row][-CHECKS_KEPT:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        return row

    def summary(self) -> dict:
        errors = [abs(r["error_pct"]) for r in self._rows]
        if not errors:
            return {"n": 0}
        return {"n": len(errors), "median_error_pct": round(statistics.median(errors), 2),
                "within_1_pct": round(sum(e <= 1 for e in errors) / len(errors), 2),
                "within_3_pct": round(sum(e <= 3 for e in errors) / len(errors), 2),
                "warned_share": round(sum(r["warned"] for r in self._rows) / len(errors), 2),
                "worst_error_pct": max(errors)}

    def clear(self) -> int:
        n, self._rows = len(self._rows), []
        self.path.unlink(missing_ok=True)
        return n
