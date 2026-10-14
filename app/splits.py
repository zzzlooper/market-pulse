"""Reverse stock splits, read from an 8-K's first pages: the ratio and the day it takes effect.

Companies announce them in an 8-K, usually under item 5.03 (the charter amendment) and 3.03
(the change to shareholders' rights): "a 1-for-20 reverse stock split … will become effective
at 12:01 a.m. Eastern Time on October 15, 2026, and the common stock will begin trading on a
split-adjusted basis at market open on October 15, 2026". Ratios come as "1-for-20",
"one-for-twenty (1:20)", "1:20" or old-for-new ("20-for-1"); a share consolidation is the same.

Not counted: a range a shareholder vote allows ("a ratio within the range of 1-for-5 to
1-for-30"), which only sets the ceiling, and forward splits.

Best effort, like the warrant reading: wording varies, so a miss means "not found on the first
pages". Only the ratio and the date come out of here; no text from the filing is kept.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from .warrants import text_of

WINDOW = 250          # characters around a "reverse split" mention searched for the ratio
DATE_WINDOW = 1200    # after the mention, where the effective date is looked for
MAX_RATIO = 10_000  # a 1-for-5,000 split exists: a cash-out of small holders

UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER = r"(\d{1,3}(?:,\d{3})+|\d{1,5}|(?:(?:one|two|three|four|five) hundred(?: and)? )?[a-z]+(?:[- ][a-z]+)?)"
RATIO = re.compile(rf"\b(1|one)[- ]for[- ]{NUMBER}\b|\b1\s?:\s?(\d{{1,4}})\b(?!\s?[ap]\.?m\b)"
                   rf"|\b(\d{{1,3}}(?:,\d{{3}})+|\d{{1,5}}|[a-z]+(?:-[a-z]+)?)[- ]for[- ](?:1|one)\b", re.IGNORECASE)
MENTION = re.compile(r"reverse (?:stock |share )?split|share consolidation|consolidation of (?:our|the) (?:common )?shares",
                     re.IGNORECASE)
RANGE = re.compile(r"range|up to|not (?:less|more) than|between|maximum|minimum|any whole number|discretion",
                   re.IGNORECASE)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b")
EFFECTIVE = re.compile(r"effective|split[- ]adjusted basis|begin(?:s)? trading|market open|effected", re.IGNORECASE)
DIGEST = re.compile(r"<!--market-pulse-split (\{.*?\})-->", re.DOTALL)


def number(word: str) -> int | None:
    """'20', 'twenty', 'twenty-five', 'one hundred fifty' -> int; None if it isn't a number."""
    word = word.lower().strip().replace(",", "")
    if word.isdigit():
        return int(word)
    total = 0
    for part in re.split(r"[- ]+", word.replace(" and ", " ")):
        if part in UNITS:
            total += UNITS.index(part)
        elif part in TENS:
            total += TENS[part]
        elif part == "hundred":
            total = (total or 1) * 100
        else:
            return None
    return total or None


def _ratio(match: re.Match) -> int | None:
    one_for, colon, for_one = match.group(2), match.group(3), match.group(4)
    n = number(one_for) if one_for else int(colon) if colon else number(for_one)
    return n if n and 2 <= n <= MAX_RATIO else None


def _effective(text: str, start: int, end: int) -> str | None:
    """The date the split takes effect: after an 'effective'/'split-adjusted' phrase following
    the mention, else a date just before it ("On October 15, 2026, the Company effected …")."""
    for key in EFFECTIVE.finditer(text, end, end + DATE_WINDOW):
        if m := DATE.search(text, key.end(), key.end() + 160):
            return _iso(m)
    before = list(DATE.finditer(text, max(0, start - 200), start))
    return _iso(before[-1]) if before else None


def _iso(m: re.Match) -> str | None:
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def detect(raw: str) -> dict | None:
    """{"ratio": 20, "effective": "2026-10-15" | None} for a definite reverse split, else None."""
    if m := DIGEST.search(raw):  # a recording keeps only these facts (see digest())
        return json.loads(m.group(1)) or None
    text = text_of(raw)
    for mention in MENTION.finditer(text):
        lo, hi = max(0, mention.start() - WINDOW), mention.end() + WINDOW
        ratios = sorted(RATIO.finditer(text, lo, hi), key=lambda r: abs(r.start() - mention.start()))
        for r in ratios:
            if RANGE.search(text, max(0, r.start() - 80), r.start()):
                break  # an authorized range, not a ratio the board has set
            if (n := _ratio(r)) is not None:
                return {"ratio": n, "effective": _effective(text, mention.start(), max(mention.end(), r.end()))}
    return None


def digest(raw: str) -> str:
    """What a recording keeps of a filing document for this: the split facts only."""
    return f"<!--market-pulse-split {json.dumps(detect(raw) or {})}-->"


def current(split: dict | None, filed: date, today: date, ahead: int = 30, after: int = 5) -> bool:
    """Worth a badge today: taking effect within `ahead` days or done in the last `after`; with no
    date found, announced within the last `ahead` days."""
    if not split:
        return False
    if split["effective"]:
        days = (date.fromisoformat(split["effective"]) - today).days
        return -after <= days <= ahead
    return (today - filed).days <= ahead
