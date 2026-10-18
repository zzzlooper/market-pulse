"""Catalyst reaction tracking: recording filings, halts and alerting headlines as they arrive,
and the app's own alerts as they fire, measuring how the stock moved afterwards, and the
Reactions tab's report. The measuring rules themselves are in app/reactions.py."""
from __future__ import annotations

import asyncio
import json
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
from .risk import RISK_RETRY

log = logging.getLogger(__name__)


def gap_groups(row: dict) -> list[str]:
    """A tracked gapper counts in "gap up" and in "gap up · <each of its flags>"."""
    return [row["label"], *(f"{row['label']} · {tag}" for tag in json.loads(row["flags"] or "[]"))]

REACTIONS_INTERVAL = 120.0  # how often pending catalyst reactions are measured
REACTION_FETCHES = 20       # chart downloads per round, so Yahoo isn't hammered after a burst
REACTION_BACKOFF = 1800.0   # an event with nothing new to measure waits this long
TRACK_MAX_AGE = 5 * 86400   # older events can't be measured (5-day bars are the furthest back)

RECORD_DAYS = 30        # an alert's track record covers this many days
RECORD_REFRESH = 600.0  # seconds the track records are reused before being recomputed

# The alerts whose follow-through is measured, and the group each is reported under: did the
# stock keep going after a breakout, a level crossing, a float rotation, a resumption?
TRACKED_ALERTS = {
    "pmh": "broke pre-market high",
    "hod": "new high of day after a pullback",
    "warrant": "crossed up through a warrant price",
    "offering_below": "fell below an offering price",
    "offering_above": "back above an offering price",
    "rotation": "traded its whole float",
    "unusual": "entered unusual volume",
    "resumed": "resumed after a halt",
    "luld_up": "near the limit-up band",
    "luld_down": "near the limit-down band",
    "sympathy": "moving in sympathy",
    "above": "your rules: price above",
    "below": "your rules: price below",
    "move": "your rules: move ±%",
    "rvol": "your rules: RVOL",
}


class CatalystsMixin:
    """Owns reactions (the store), the measuring back-off and the queue of offerings awaiting
    a risk level."""

    def _init_catalysts(self) -> None:
        # Next to the user state, in a file only the user can read.
        self.reactions = ReactionStore(self.state.path.parent / "reactions.db")
        self._reaction_retry: dict[str, float] = {}  # event id -> monotonic time to try again
        self._risk_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()  # offerings awaiting a risk level
        self._risk_queued: set[str] = set()         # their event ids, so one is never queued twice
        self._risk_retry: dict[str, float] = {}     # event id -> monotonic time to look it up again
        self._pruned_on = None  # the day old history was last deleted
        self._records: tuple[float, dict[str, dict]] | None = None  # (monotonic time, label -> stats)
        self._gap_stats: tuple[float, dict[str, dict]] | None = None  # the same, for gappers

    def _context(self, symbol: str) -> str:
        if symbol in self.state.watchlist:
            return "watchlist"
        return "mover" if symbol in self._movers_by_symbol() else "other"

    def _track(self, event_id: str, symbol: str, kind: str, label: str, title: str, when: str,
               risk: str | None = None, flags: list[str] | None = None) -> bool:
        """Start tracking an event; False if it's too old, not a stock, or already tracked."""
        try:
            at = int(datetime.fromisoformat(when).timestamp())
        except ValueError:
            return False
        if now_ts() - at > TRACK_MAX_AGE or symbol.startswith("^") or "=" in symbol:
            return False
        return self.reactions.add({"id": event_id, "symbol": symbol, "kind": kind, "label": label,
                                   "title": title[:200], "context": self._context(symbol), "risk": risk, "at": at,
                                   "flags": json.dumps(flags) if flags is not None else None})

    async def _fill_offering_risk(self) -> None:
        """Look up the dilution risk of each tracked offering, one at a time. The level is read
        just after the filing, so it already counts that filing (or later, for one queued again by
        _queue_missing_risk)."""
        while True:
            await self._fill_one_offering_risk(*await self._risk_queue.get())

    def _queue_offering_risk(self, event_id: str, symbol: str) -> None:
        if event_id not in self._risk_queued:
            self._risk_queued.add(event_id)
            self._risk_queue.put_nowait((event_id, symbol))

    def _queue_missing_risk(self) -> None:
        """Queue again the recent offerings still without a risk level: the queue lives in memory,
        so a restart loses what was waiting, and a lookup can fail. Those wait RISK_RETRY first."""
        mono, rows = time.monotonic(), self.reactions.missing_risk(now_ts() - TRACK_MAX_AGE)
        self._risk_retry = {r["id"]: self._risk_retry[r["id"]] for r in rows if r["id"] in self._risk_retry}
        for row in rows:
            if self._risk_retry.get(row["id"], 0) <= mono:
                self._queue_offering_risk(row["id"], row["symbol"])

    async def _fill_one_offering_risk(self, event_id: str, symbol: str) -> None:
        self._risk_queued.discard(event_id)
        try:
            summary = await self._risk_profile(symbol)
        except Exception as exc:
            log.info("no dilution risk for %s offering: %s", symbol, describe(exc))
            summary = None
        if summary:
            self.reactions.update(event_id, {"risk": summary["level"]})
            self._risk_retry.pop(event_id, None)
        else:  # failed, or no SEC profile: not again every round
            self._risk_retry[event_id] = time.monotonic() + RISK_RETRY

    def _prune_reactions(self) -> None:
        """Once a day, delete history older than reactions_keep_days (0 keeps everything)."""
        today = datetime.fromtimestamp(now_ts(), market.ET).date()
        if self._pruned_on == today:
            return
        self._pruned_on = today
        days = self.settings.reactions_keep_days
        if days and (deleted := self.reactions.prune(now_ts() - days * 86400)):
            log.info("deleted %d catalysts older than %d days from the reaction history", deleted, days)

    async def _update_reactions(self) -> None:
        """Measure what's due: one chart download per symbol covers all its events. Also queues
        the offerings still waiting for a risk level (at startup too: this loop runs first thing)."""
        self._prune_reactions()
        self._queue_missing_risk()
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

    def _gap_records(self) -> dict[str, dict]:
        """How gappers followed through over the last RECORD_DAYS, by "gap up · low float" etc."""
        mono = time.monotonic()
        if self._gap_stats is None or mono - self._gap_stats[0] > RECORD_REFRESH:
            rows = [r for r in self.reactions.since(now_ts() - RECORD_DAYS * 86400) if r["kind"] == "gap"]
            self._gap_stats = (mono, {g["group"]: g for g in summarize(rows, gap_groups)})
        return self._gap_stats[1]

    def _track_records(self) -> dict[str, dict]:
        """How each kind of alert followed through over the last RECORD_DAYS, by label."""
        mono = time.monotonic()
        if self._records is None or mono - self._records[0] > RECORD_REFRESH:
            rows = [r for r in self.reactions.since(now_ts() - RECORD_DAYS * 86400) if r["kind"] == "alert"]
            self._records = (mono, {g["group"]: g for g in summarize(rows, lambda r: r["label"])})
        return self._records[1]

    def _add_track_records(self, fired: list[dict]) -> None:
        """Quote, in each alert, how that kind of alert has followed through: is it worth acting on?
        Only once enough have been measured to the close (the record_min setting)."""
        if not any(e["kind"] in TRACKED_ALERTS for e in fired):
            return
        records = self._track_records()
        for e in fired:
            g = records.get(TRACKED_ALERTS.get(e["kind"], ""))
            if not g or g["measured"] < self.settings.record_min:
                continue
            parts = [f"{label} {v:+.1f}%" for label, v in (("30 min", g["median_30m"]), ("close", g["median_close"]))
                     if v is not None]
            if g["down_share"] is not None:
                parts.append(f"{g['down_share'] * 100:.0f}% closed lower")
            e["record"] = {k: g[k] for k in ("measured", "median_30m", "median_close", "down_share")}
            e["message"] += f" · track record: {', '.join(parts)} (median of {g['measured']} in {RECORD_DAYS} days)"

    def _track_alerts(self, fired: list[dict]) -> None:
        """Start measuring what the stock did after each alert that fired."""
        for e in fired:
            if label := TRACKED_ALERTS.get(e["kind"]):
                message = e["message"].split(" · track record: ")[0]  # what happened, not what usually does
                self._track(f"alert:{e['id']}", e["symbol"], "alert", label, message, e["time"])

    def reactions_report(self, days: int) -> dict:
        rows = self.reactions.since(now_ts() - days * 86400)
        keep = ("symbol", "kind", "label", "title", "context", "risk", "at", "close_day")
        catalysts = [r for r in rows if r["kind"] not in ("alert", "gap")]
        return {
            "days": days,
            "tracked": len(rows),
            "catalysts": len(catalysts),
            "by_type": summarize(catalysts, lambda r: r["label"]),
            "alerts": summarize([r for r in rows if r["kind"] == "alert"], lambda r: r["label"]),
            "gaps": summarize([r for r in rows if r["kind"] == "gap"], gap_groups),
            "luld": self.luld_checks.summary(),
            "offerings_by_risk": summarize([r for r in rows if r["kind"] == "dilution"],
                                           lambda r: r["risk"] or "unknown"),
            "by_context": summarize(catalysts, lambda r: r["context"]),
            "recent": [{**{k: r[k] for k in keep}, "returns": returns(r)} for r in rows[:60]],
        }
