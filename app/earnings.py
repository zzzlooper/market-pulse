"""Earnings awareness: when each watchlist stock next reports, for the ER badge, the 09:15
brief and the end-of-day recap.

Yahoo's company data gives the next report's date and often a time. The date is reliable; the
time often isn't (many large caps show a placeholder like 15:00). So the time is only used when
it's clearly outside the session: before 09:30 is "before the open", 16:00 or later "after the
close", and anything else says the time isn't confirmed. Market data only.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .market import CLOSE, ET, OPEN

BADGE_DAYS = 5  # the ER badge shows for reports within this many days


def when(at: datetime) -> str:
    """'before the open', 'after the close' or '' (time not confirmed)."""
    t = at.astimezone(ET).time()
    if t < OPEN:
        return "before the open"
    if t >= CLOSE:
        return "after the close"
    return ""


def earnings_row(symbol: str, info: dict, now: float) -> dict | None:
    """The next report from Yahoo's company data, or None if none is scheduled ahead. Yahoo can
    keep the last report in one field while the other already points at the next."""
    candidates = [info.get(k) for k in ("earningsTimestampStart", "earningsTimestamp")]
    future = [int(t) for t in candidates if isinstance(t, (int, float)) and t > now]
    if not future:
        return None
    at = datetime.fromtimestamp(min(future), ET)
    return {"symbol": symbol, "at": int(at.timestamp()), "date": at.date().isoformat(),
            "when": when(at), "estimate": bool(info.get("isEarningsDateEstimate"))}


def next_trading_day(day: date) -> date:
    """The next weekday (market holidays aren't known here)."""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _day_name(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if day - today < timedelta(days=7):
        return f"{day:%A}"           # this coming week: "Thursday"
    return f"{day:%a %b} {day.day}"  # further out: "Mon Nov 2"


def describe(row: dict, today: date) -> str:
    """'reports before the open today', 'reports Thursday (estimated)'…"""
    day = date.fromisoformat(row["date"])
    text = f"reports {row['when'] + ' ' if row['when'] else ''}{_day_name(day, today)}"
    if not row["when"]:
        text += ", time not confirmed"
    if row["estimate"]:
        text += " (estimated date)"
    return text


def upcoming(earnings: dict[str, dict], symbols: list[str], today: date, days: int = BADGE_DAYS) -> list[dict]:
    """Reports from today through `days` ahead for these symbols, soonest first."""
    end = today + timedelta(days=days)
    rows = [earnings[s] for s in symbols if s in earnings and today <= date.fromisoformat(earnings[s]["date"]) <= end]
    return sorted(rows, key=lambda r: (r["date"], r["at"], r["symbol"]))


def brief_lines(earnings: dict[str, dict], watchlist: list[str], today: date) -> list[str]:
    """For the 09:15 brief: watchlist stocks reporting today."""
    rows = [r for r in upcoming(earnings, watchlist, today, days=0)]
    return [f"{r['symbol']} {describe(r, today)}" for r in rows]


def recap_lines(earnings: dict[str, dict], watchlist: list[str], today: date) -> list[str]:
    """For the after-close recap: tonight's reports and the next trading day's."""
    following = next_trading_day(today)
    rows = [r for r in upcoming(earnings, watchlist, today, days=(following - today).days)
            if r["date"] == following.isoformat() or (r["date"] == today.isoformat() and r["when"] == "after the close")]
    return [f"{r['symbol']} {describe(r, today)}" for r in rows]
