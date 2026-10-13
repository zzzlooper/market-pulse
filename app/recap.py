"""The end-of-day recap: one phone message after the close with the day's watchlist moves,
halts, filings and the biggest reactions the tracker measured.

It carries tickers, prices and counts only. Your watchlist is in it, though, so on public
ntfy.sh the topic name is what keeps it private (see README).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from datetime import time as dtime

from .market import ET
from .reactions import returns

MAX_WATCHLIST = 6
MAX_REACTIONS = 4
PLURAL = {"offering from shelf": "offerings from shelf", "insider buy": "insider buys",
          "insider sale": "insider sales", "delisting notice": "delisting notices"}


def _pct(v: float) -> str:
    return f"{v:+.1f}%"


def _count(label: str, n: int) -> str:
    return f"{n} {PLURAL.get(label, label) if n > 1 else label}"


def build_recap(day: date, quotes: list[dict], halts: list[dict], events: list[dict],
                watched: set[str], problems: list[str], public_url: str = "", earnings: list[str] = ()) -> dict:
    """A phone note (see notify.py). halts and events are today's; watched is the watchlist."""
    lines = []
    moves = sorted((q for q in quotes if q.get("change_pct") is not None), key=lambda q: -abs(q["change_pct"]))
    if moves:
        shown = ", ".join(f"{q['symbol']} {_pct(q['change_pct'])}" for q in moves[:MAX_WATCHLIST])
        more = f" (+{len(moves) - MAX_WATCHLIST} more)" if len(moves) > MAX_WATCHLIST else ""
        lines.append(f"Watchlist: {shown}{more}")

    if halts:
        mine = [h for h in halts if h["symbol"] in watched]
        text = f"Halts: {len(halts)} today"
        if mine:
            text += " · yours: " + ", ".join(f"{h['symbol']} ({h['reason']})" for h in mine[:4])
        lines.append(text)

    filings = [e for e in events if e["kind"] not in ("halt", "headline", "report")]
    if filings:
        counts = Counter(e["label"] for e in filings)
        lines.append("Filings: " + ", ".join(_count(label, n) for label, n in counts.most_common(5)))

    measured = [(e, r) for e in events if (r := returns(e)["close"]) is not None and e["close_day"] == day.isoformat()]
    if measured:
        lines.append("Biggest reactions by the close:")
        for e, r in sorted(measured, key=lambda er: -abs(er[1]))[:MAX_REACTIONS]:
            lines.append(f"  {e['symbol']} {e['label']}: {_pct(r)}")
    if events:
        lines.append(f"Tracked today: {len(events)} catalysts, {len(measured)} measured to the close")

    if earnings:  # tonight's and the next trading day's reports on the watchlist
        lines += ["Earnings:", *(f"  {line}" for line in earnings)]
    lines.append(f"Data: {', '.join(problems)} down" if problems else "Data: all sources OK")
    return {
        "category": "recap",
        "title": f"Market Pulse · close recap {day:%a %b} {day.day}",
        "body": "\n".join(lines),
        "url": f"{public_url.rstrip('/')}/#reactions" if public_url else None,
        "priority": "low",
        "tag": "bookmark_tabs",
    }


def next_run(now: datetime, at: str) -> datetime:
    """The next weekday at the ET time "HH:MM" after now. Holidays are checked when it fires."""
    hour, minute = (int(x) for x in at.split(":"))
    now = now.astimezone(ET)
    run = datetime.combine(now.date(), dtime(hour, minute), ET)
    if run <= now:
        run += timedelta(days=1)
    while run.weekday() >= 5:
        run += timedelta(days=1)
    return run
