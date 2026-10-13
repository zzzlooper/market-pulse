"""Catalyst reaction tracking: how a stock moved after a filing, an 8-K event, a halt or an
alerting headline, 5 and 30 minutes later and at the close of the next regular session.

Over a few weeks the per-type medians show which catalysts actually move stocks, and whether
the dilution risk level means anything (offerings grouped by the risk the stock had when the
filing arrived).

Prices come from Yahoo's intraday bars, read after the fact: 1-minute bars with pre/post-market
for today, 5-minute regular-session bars for the days before. Yahoo delays them, so each
horizon is measured GRACE seconds after it's due.

Storage: data/reactions.db (SQLite), readable by you only. It holds market data only: ticker,
event type, the headline shown in the feed, time and prices. No personal information.
"""
from __future__ import annotations

import os
import sqlite3
import statistics
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from .clock import clock
from .market import CLOSE, ET, is_regular_bar

GRACE = 20 * 60           # Yahoo's bars arrive ~15 minutes late
EXPIRE = 6 * 86400        # give up on a horizon this long after the event (5d bars run out)
MAX_BASE_AGE = 16 * 3600  # an event at 7 AM is priced off yesterday's post-market or close
HORIZONS = {"5m": 300, "30m": 1800}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,      -- dilution, insider_buy, event, report, stake, halt, headline...
    label TEXT NOT NULL,     -- what stats group by: "offering from shelf", "delisting notice"...
    title TEXT NOT NULL,
    context TEXT NOT NULL,   -- watchlist, mover or other, when the event arrived
    risk TEXT,               -- the stock's dilution risk level then, if known
    at INTEGER NOT NULL,     -- event time, epoch seconds
    base REAL, p5m REAL, p30m REAL, pclose REAL, close_day TEXT,
    m5m INTEGER NOT NULL DEFAULT 0, m30m INTEGER NOT NULL DEFAULT 0,  -- horizon measured (price may be NULL: no trading)
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS events_pending ON events(done, at);
"""


def price_at(bars: list[dict], t: int, max_age: int) -> float | None:
    """Close of the last bar starting at or before t, if it's no older than max_age."""
    found = None
    for bar in bars:
        if bar["t"] > t:
            break
        found = bar
    return found["c"] if found and t - found["t"] <= max_age else None


def close_after(bars: list[dict], t: int, now: int) -> tuple[str, float] | None:
    """(ET date, closing price) of the first regular session that ends after t, once that
    session is over and its bars have had time to arrive. None if not available yet."""
    days: dict[date, list[dict]] = {}
    for bar in bars:
        if is_regular_bar(bar["t"]):
            days.setdefault(datetime.fromtimestamp(bar["t"], ET).date(), []).append(bar)
    for day in sorted(days):
        session_end = int(datetime.combine(day, CLOSE, ET).timestamp())
        if session_end <= t:
            continue
        if now < session_end + GRACE:
            return None
        return day.isoformat(), days[day][-1]["c"]
    return None


def measure(event: dict, bars: list[dict], now: int) -> dict:
    """The fields of event that can be filled from bars now. Horizons that are due but have no
    bar near them (an event overnight) are marked measured with no price."""
    at = event["at"]
    out: dict = {}
    # The minute the event arrived, or the last trade before it.
    if event["base"] is None and (base := price_at(bars, at, MAX_BASE_AGE)) is not None:
        out["base"] = base
    if (event["base"] if event["base"] is not None else out.get("base")) is None:
        return out  # nothing to compare against yet
    for name, seconds in HORIZONS.items():
        if not event[f"m{name}"] and now >= at + seconds + GRACE:
            # Only a bar from within the window counts: no trading then means no reading.
            out[f"p{name}"] = price_at(bars, at + seconds, max_age=seconds)
            out[f"m{name}"] = 1
    if event["pclose"] is None and (closing := close_after(bars, at, now)):
        out["close_day"], out["pclose"] = closing
    return out


def returns(row: dict) -> dict:
    """Percent change from base at each horizon (None where unknown)."""
    base = row["base"]

    def pct(price: float | None) -> float | None:
        return (price / base - 1) * 100 if base and price is not None else None
    return {"5m": pct(row["p5m"]), "30m": pct(row["p30m"]), "close": pct(row["pclose"])}


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def summarize(rows: list[dict], key) -> list[dict]:
    """Median reaction per group, most frequent first. key(row) -> group name or None."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if (name := key(row)) is not None:
            groups.setdefault(name, []).append(returns(row))
    out = []
    for name, rs in groups.items():
        closes = [r["close"] for r in rs if r["close"] is not None]
        out.append({
            "group": name,
            "n": len(rs),
            "measured": len(closes),
            "median_5m": _median([r["5m"] for r in rs if r["5m"] is not None]),
            "median_30m": _median([r["30m"] for r in rs if r["30m"] is not None]),
            "median_close": _median(closes),
            "down_share": round(sum(c < 0 for c in closes) / len(closes), 2) if closes else None,
        })
    return sorted(out, key=lambda g: (-g["measured"], -g["n"], g["group"]))


class ReactionStore:
    """The SQLite file of tracked events. The engine and request handlers on worker threads
    (the CSV download) share one connection, so every use of it holds the lock."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        if new:
            os.chmod(path, 0o600)  # yours only, whatever the umask

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, event: dict) -> bool:
        """Store a new event; False if this id is already tracked (e.g. after a restart)."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO events (id, symbol, kind, label, title, context, risk, at) "
                "VALUES (:id, :symbol, :kind, :label, :title, :context, :risk, :at)", event)
            self._db.commit()
            return cur.rowcount == 1

    def pending(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events WHERE done = 0 ORDER BY at LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def update(self, event_id: str, fields: dict) -> None:
        if not fields:
            return
        allowed = {"base", "p5m", "p30m", "pclose", "close_day", "m5m", "m30m", "done", "risk"}
        assert set(fields) <= allowed, fields
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE events SET {sets} WHERE id = :id", {**fields, "id": event_id})
            self._db.commit()

    def since(self, t: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events WHERE at >= ? ORDER BY at DESC", (t,))
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            count, oldest, newest = self._db.execute("SELECT COUNT(*), MIN(at), MAX(at) FROM events").fetchone()
        return {"count": count, "oldest": oldest, "newest": newest}

    def prune(self, before: int) -> int:
        """Delete events older than `before` (epoch seconds); returns how many."""
        with self._lock:
            deleted = self._db.execute("DELETE FROM events WHERE at < ?", (before,)).rowcount
            self._db.commit()
            if deleted:
                self._compact()
            return deleted

    def clear(self) -> int:
        with self._lock:
            deleted = self._db.execute("DELETE FROM events").rowcount
            self._db.commit()
            self._compact()
            return deleted

    def _compact(self) -> None:
        # SQLite only marks deleted rows free; VACUUM rewrites the file so they're really gone.
        with self._lock:
            self._db.execute("VACUUM")


def finished(event: dict, now: int) -> bool:
    measured = event["m5m"] and event["m30m"] and event["pclose"] is not None
    return bool(measured) or now - event["at"] > EXPIRE


def chart_range(event: dict, now: int) -> str:
    """1-minute bars cover today only; anything older needs the 5-day, 5-minute bars."""
    today = datetime.fromtimestamp(now, ET).date()
    return "1d" if datetime.fromtimestamp(event["at"], ET).date() == today else "5d"


def due(event: dict, now: int) -> bool:
    """Whether a pending event has anything that can be measured now."""
    if now < event["at"] + HORIZONS["5m"] + GRACE:
        return False
    if event["base"] is None or not (event["m5m"] and event["m30m"]):
        return True
    # Only the close is left: wait until a session close has passed since the event.
    at = datetime.fromtimestamp(event["at"], ET)
    session_end = datetime.combine(at.date(), CLOSE, ET)
    if at >= session_end:
        session_end += timedelta(days=1)
    return now >= session_end.timestamp() + GRACE


def now_ts() -> int:
    return int(clock.time())


CSV_COLUMNS = ("time_et", "symbol", "type", "catalyst", "context", "dilution_risk",
               "move_5m_pct", "move_30m_pct", "move_close_pct", "close_day", "headline")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _text_cell(value) -> str:
    """Text as a spreadsheet will show it. Headlines come from outside feeds, and a cell starting
    with = + - @ could run as a formula when the file is opened, so it gets a leading '."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def to_csv(rows: list[dict]) -> str:
    """Tracked catalysts and their moves, newest first. Market data only."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        r = returns(row)
        moves = [f"{r[h]:.2f}" if r[h] is not None else "" for h in ("5m", "30m", "close")]
        when = datetime.fromtimestamp(row["at"], ET).strftime("%Y-%m-%d %H:%M")
        writer.writerow([when, *(_text_cell(row[k]) for k in ("symbol", "kind", "label", "context", "risk")),
                         *moves, row["close_day"] or "", _text_cell(row["title"])])
    return out.getvalue()
