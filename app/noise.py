"""Alert noise control: which alerts you see and which reach your phone.

- Each kind of alert can be muted in the browser and on the phone separately.
- A stock can be snoozed for the rest of the day: no alerts about it, anywhere.
- The phone can be capped at a number of alerts per kind per day (phone_cap_per_kind; 0 = no
  cap). Past the cap they still show in the browser.

Muted and snoozed alerts are still counted and their follow-through still measured (see
engine/catalysts.py), so the panel can show what you'd be missing before you unmute.

Counts per kind and day are kept in data/alert_counts.json for the last COUNT_DAYS days. They
hold alert kinds and numbers only: no tickers, nothing about you.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

COUNT_DAYS = 8  # today and the 7 days before

# kind -> (label, group), in the order the panel lists them.
KINDS: dict[str, tuple[str, str]] = {
    "above": ("Price above (your rules)", "Your rules"),
    "below": ("Price below (your rules)", "Your rules"),
    "move": ("Moves ±% (your rules)", "Your rules"),
    "rvol": ("RVOL (your rules)", "Your rules"),
    "unusual": ("Entered unusual volume", "Volume"),
    "rotation": ("Traded its whole float", "Volume"),
    "pmh": ("Broke pre-market high", "Breakouts"),
    "hod": ("New high of day after a pullback", "Breakouts"),
    "sympathy": ("Moving in sympathy", "Breakouts"),
    "warrant": ("Crossed up through a warrant price", "Levels from filings"),
    "offering_below": ("Fell below an offering price", "Levels from filings"),
    "offering_above": ("Back above an offering price", "Levels from filings"),
    "halt": ("Trading halted", "Halts"),
    "resume": ("Resumption time set", "Halts"),
    "resuming": ("Resumes in a minute", "Halts"),
    "resumed": ("Resumed after a halt", "Halts"),
    "luld_up": ("Near the limit-up band", "Halts"),
    "luld_down": ("Near the limit-down band", "Halts"),
}


def muted(mutes: dict, kind: str, where: str) -> bool:
    """where: "browser" or "phone"."""
    return bool(mutes.get(kind, {}).get(where))


def snoozed(snoozes: dict[str, str], symbol: str, today: str) -> bool:
    """Snoozed for the rest of the day it was snoozed on."""
    return snoozes.get(symbol) == today


class AlertCounts:
    """How many alerts of each kind fired, and how many went to the phone, per day."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self._days: dict[str, dict[str, dict[str, int]]] = json.loads(path.read_text())
        except (OSError, ValueError):
            self._days = {}

    def add(self, kind: str, day: str, phoned: bool) -> None:
        counts = self._days.setdefault(day, {}).setdefault(kind, {"fired": 0, "phoned": 0})
        counts["fired"] += 1
        counts["phoned"] += int(phoned)

    def phoned(self, kind: str, day: str) -> int:
        return self._days.get(day, {}).get(kind, {}).get("phoned", 0)

    def summary(self, today: date) -> dict[str, dict[str, int]]:
        """kind -> {"today", "week", "phoned_today"}: week is the 7 days before today plus today."""
        days = [(today - timedelta(days=i)).isoformat() for i in range(COUNT_DAYS)]
        out: dict[str, dict[str, int]] = {}
        for i, day in enumerate(days):
            for kind, c in self._days.get(day, {}).items():
                row = out.setdefault(kind, {"today": 0, "week": 0, "phoned_today": 0})
                row["week"] += c["fired"]
                if i == 0:
                    row["today"], row["phoned_today"] = c["fired"], c["phoned"]
        return out

    def save(self, today: date) -> None:
        """Write the file (readable by you only), dropping days older than COUNT_DAYS."""
        oldest = (today - timedelta(days=COUNT_DAYS - 1)).isoformat()
        self._days = {d: v for d, v in self._days.items() if d >= oldest}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._days))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def clear(self) -> int:
        n = sum(c["fired"] for kinds in self._days.values() for c in kinds.values())
        self._days = {}
        self.path.unlink(missing_ok=True)
        return n
