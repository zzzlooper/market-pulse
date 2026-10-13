"""Catalyst reaction tracking: recording filings, halts and alerting headlines as they arrive,
measuring how the stock moved afterwards, and the Reactions tab's report. The measuring rules
themselves are in app/reactions.py."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from .. import market
from ..health import describe
from ..reactions import (
    MAX_BASE_AGE,
    ReactionStore,
    chart_range,
    due,
    finished,
    measure,
    now_ts,
    price_at,
    returns,
    summarize,
    to_csv,
)

log = logging.getLogger(__name__)

REACTIONS_INTERVAL = 120.0  # how often pending catalyst reactions are measured
REACTION_FETCHES = 20       # chart downloads per round, so Yahoo isn't hammered after a burst
REACTION_BACKOFF = 1800.0   # an event with nothing new to measure waits this long
TRACK_MAX_AGE = 5 * 86400   # older events can't be measured (5-day bars are the furthest back)


class CatalystsMixin:
    """Owns reactions (the store), the measuring back-off and the queue of offerings awaiting
    a risk level."""

    def _init_catalysts(self) -> None:
        # Next to the user state, in a file only the user can read.
        self.reactions = ReactionStore(self.state.path.parent / "reactions.db")
        self._reaction_retry: dict[str, float] = {}  # event id -> monotonic time to try again
        self._risk_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()  # offerings awaiting a risk level

    def _context(self, symbol: str) -> str:
        if symbol in self.state.watchlist:
            return "watchlist"
        return "mover" if symbol in self._movers_by_symbol() else "other"

    def _track(self, event_id: str, symbol: str, kind: str, label: str, title: str, when: str,
               risk: str | None = None) -> bool:
        """Start tracking an event; False if it's too old, not a stock, or already tracked."""
        try:
            at = int(datetime.fromisoformat(when).timestamp())
        except ValueError:
            return False
        if now_ts() - at > TRACK_MAX_AGE or symbol.startswith("^") or "=" in symbol:
            return False
        return self.reactions.add({"id": event_id, "symbol": symbol, "kind": kind, "label": label,
                                   "title": title[:200], "context": self._context(symbol), "risk": risk, "at": at})

    async def _fill_offering_risk(self) -> None:
        """Look up the dilution risk of each tracked offering, one at a time. The level is read
        just after the filing, so it already counts that filing."""
        while True:
            await self._fill_one_offering_risk(*await self._risk_queue.get())

    async def _fill_one_offering_risk(self, event_id: str, symbol: str) -> None:
        try:
            if summary := await self._risk_profile(symbol):
                self.reactions.update(event_id, {"risk": summary["level"]})
        except Exception as exc:
            log.info("no dilution risk for %s offering: %s", symbol, describe(exc))

    async def _update_reactions(self) -> None:
        """Measure what's due: one chart download per symbol covers all its events."""
        now, mono = now_ts(), time.monotonic()
        work: dict[tuple[str, str], list[dict]] = {}
        for event in self.reactions.pending():
            if finished(event, now):
                self.reactions.update(event["id"], {"done": 1})
            elif due(event, now) and self._reaction_retry.get(event["id"], 0) <= mono:
                work.setdefault((event["symbol"], chart_range(event, now)), []).append(event)
        for (symbol, range_), events in list(work.items())[:REACTION_FETCHES]:
            try:
                bars = (await self._call("fetch_chart", market.fetch_chart, symbol, range_))["bars"]
                if range_ == "1d" and any(e["base"] is None and price_at(bars, e["at"], MAX_BASE_AGE) is None
                                          for e in events):
                    # An event before today's first bar (4 AM): price it off the last session.
                    earlier = (await self._call("fetch_chart", market.fetch_chart, symbol, "5d"))["bars"]
                    bars = sorted({b["t"]: b for b in [*earlier, *bars]}.values(), key=lambda b: b["t"])
            except Exception as exc:
                log.info("reaction prices for %s unavailable: %s", symbol, describe(exc))
                for e in events:
                    self._reaction_retry[e["id"]] = mono + REACTION_BACKOFF
                continue
            for e in events:
                fields = measure(e, bars, now)
                if finished({**e, **fields}, now):
                    fields["done"] = 1
                if fields:
                    self.reactions.update(e["id"], fields)
                    self._reaction_retry.pop(e["id"], None)
                else:  # e.g. a weekend before the next close: don't download again every round
                    self._reaction_retry[e["id"]] = mono + REACTION_BACKOFF

    def reactions_csv(self, days: int) -> str:
        return to_csv(self.reactions.since(now_ts() - days * 86400))

    def reactions_report(self, days: int) -> dict:
        rows = self.reactions.since(now_ts() - days * 86400)
        keep = ("symbol", "kind", "label", "title", "context", "risk", "at", "close_day")
        return {
            "days": days,
            "tracked": len(rows),
            "by_type": summarize(rows, lambda r: r["label"]),
            "offerings_by_risk": summarize([r for r in rows if r["kind"] == "dilution"],
                                           lambda r: r["risk"] or "unknown"),
            "by_context": summarize(rows, lambda r: r["context"]),
            "recent": [{**{k: r[k] for k in keep}, "returns": returns(r)} for r in rows[:60]],
        }
