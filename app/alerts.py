"""Price, move and volume alerts on watchlist quotes, plus new unusual-volume names.

Rules fire when their condition is *crossed*, not while it merely holds: the first
reading for a rule/symbol only records a baseline. So restarting the app or adding a
rule never floods you with alerts for conditions that were already true, and a cooldown
stops a price hovering around a level from alerting on every tick.
"""
from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime

from .clock import clock as app_clock
from .market import ET

COOLDOWN_SECONDS = 30 * 60
HISTORY = 100

# kind -> (label, value from a quote row)
KINDS: dict[str, tuple[str, Callable[[dict], float | None]]] = {
    "above": ("Price above", lambda q: q.get("price")),
    "below": ("Price below", lambda q: q.get("price")),
    "move": ("Moves ±%", lambda q: abs(q["change_pct"]) if q.get("change_pct") is not None else None),
    "rvol": ("RVOL ≥", lambda q: q.get("rvol")),
}


def new_rule(symbol: str, kind: str, value: float) -> dict:
    """symbol "" means every watchlist symbol."""
    return {"id": uuid.uuid4().hex[:8], "symbol": symbol, "kind": kind, "value": value}


def _describe(kind: str, q: dict, value: float) -> str:
    sym = q["symbol"]
    if kind == "above":
        return f"{sym} crossed above {value:g} · last {q['price']:.2f}"
    if kind == "below":
        return f"{sym} crossed below {value:g} · last {q['price']:.2f}"
    if kind == "move":
        direction = "up" if q["change_pct"] > 0 else "down"
        return f"{sym} {direction} {abs(q['change_pct']):.1f}% today · last {q['price']:.2f}"
    return f"{sym} volume {q['rvol']:.1f}× normal for this time of day"


class AlertMonitor:
    def __init__(self, clock: Callable[[], datetime] = lambda: app_clock.now(ET)):
        self._clock = clock
        self._hit: dict[tuple[str, str], bool] = {}     # (rule id, symbol) -> condition at last check
        self._fired_at: dict[tuple[str, str], float] = {}
        self._unusual_seen: set[str] | None = None      # None until the first list primes it
        self._unusual_day = None
        self.recent: deque[dict] = deque(maxlen=HISTORY)  # newest first

    def check_quotes(self, quotes: list[dict], rules: list[dict]) -> list[dict]:
        now = self._clock()
        by_symbol = {q["symbol"]: q for q in quotes}
        fired = []
        for rule in rules:
            kind, threshold = rule["kind"], rule["value"]
            if rule["symbol"]:
                targets = [by_symbol[rule["symbol"]]] if rule["symbol"] in by_symbol else []
            else:
                targets = quotes
            for q in targets:
                metric = KINDS[kind][1](q)
                if metric is None:
                    continue  # no data yet; don't record a baseline either
                hit = metric <= threshold if kind == "below" else metric >= threshold
                key = (rule["id"], q["symbol"])
                crossed = hit and self._hit.get(key) is False
                self._hit[key] = hit
                if crossed and now.timestamp() - self._fired_at.get(key, 0) >= COOLDOWN_SECONDS:
                    self._fired_at[key] = now.timestamp()
                    fired.append(self.event(q, kind, _describe(kind, q, threshold)))
        return fired

    def check_unusual(self, rows: list[dict], enabled: bool) -> list[dict]:
        """Alert once per symbol per day when it first shows up in the unusual-volume list."""
        now = self._clock()
        symbols = {r["symbol"] for r in rows}
        if self._unusual_seen is None or self._unusual_day != now.date():
            # first list, or a new day: whatever is already there is the baseline
            self._unusual_seen, self._unusual_day = symbols, now.date()
            return []
        fresh = [r for r in rows if r["symbol"] not in self._unusual_seen]
        self._unusual_seen |= symbols  # tracked even when disabled, so enabling doesn't flood
        if not enabled:
            return []
        return [
            self.event(r, "unusual",
                        f"{r['symbol']} unusual volume {r['rvol']:.1f}× · "
                        f"{r['change_pct']:+.1f}% · last {r['price']:.2f}"
                        if r.get("change_pct") is not None and r.get("price") is not None
                        else f"{r['symbol']} unusual volume {r['rvol']:.1f}×")
            for r in fresh
        ]

    def forget_rule(self, rule_id: str) -> None:
        for store in (self._hit, self._fired_at):
            for key in [k for k in store if k[0] == rule_id]:
                del store[key]

    def event(self, row: dict, kind: str, message: str) -> dict:
        """An alert about row["symbol"]; price fields are copied when the row has them."""
        return {
            "id": uuid.uuid4().hex[:12],
            "time": self._clock().isoformat(),
            "symbol": row["symbol"],
            "kind": kind,
            "message": message,
            "price": row.get("price"),
            "change_pct": row.get("change_pct"),
            "rvol": row.get("rvol"),
        }

    def record(self, fired: list[dict]) -> list[dict]:
        """Add events to the log (newest first) and return them. The engine records what it
        shows, after muting and snoozing (engine/core.py _publish_alerts)."""
        for event in fired:
            self.recent.appendleft(event)
        return fired
