"""Pre-market gap scanner: which stocks are opening well away from yesterday's close, and why.

Yahoo has no pre-market screener, so the scan covers the stocks most likely to gap: the
watchlist, anything with a filing or headline since the last close, and the last session's
movers. It runs 04:00–10:00 ET on weekdays; after 09:30 the gap is measured at the open.

Yahoo's pre-market bars carry prices but no volume (it's always 0), so activity is measured
in minutes with at least one trade: a bar only exists for such a minute.

Rows hold market data only. The 09:15 brief names your watchlist tickers, like the recap does.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime

from .market import CLOSE, ET, OPEN

WINDOW = (dtime(4, 0), dtime(10, 0))   # when the scanner polls (ET, weekdays)
CATALYST_HOURS = 18                    # news or filings this recent can explain a gap
MAX_BRIEF_ROWS = 8


def scanning(now: datetime) -> bool:
    now = now.astimezone(ET)
    return now.weekday() < 5 and WINDOW[0] <= now.time() < WINDOW[1]


def premarket_row(symbol: str, bars: list[dict], today: date) -> dict | None:
    """Gap from yesterday's close, from 1-minute bars (t, o, h, c, v) covering yesterday and
    today with pre/post-market. Before 09:30 the gap is to the latest pre-market trade, after
    it to today's open. None without a previous close or any trade today. Also the pre-market
    high, which a breakout after the open goes through."""
    def et(bar):
        return datetime.fromtimestamp(bar["t"], ET)

    def regular(bar):
        return OPEN <= et(bar).time() < CLOSE

    before = [b for b in bars if et(b).date() < today and regular(b)]
    todays = [b for b in bars if et(b).date() == today]
    if not before or not todays:
        return None
    prev_close = before[-1]["c"]
    pre = [b for b in todays if et(b).time() < OPEN]
    opened = [b for b in todays if regular(b)]
    if opened:
        price, at_open = opened[0]["o"], True
    elif pre:
        price, at_open = pre[-1]["c"], False
    else:
        return None
    if not prev_close or price is None:
        return None
    return {
        "symbol": symbol,
        "prev_close": prev_close,
        "price": price,
        "gap_pct": (price / prev_close - 1) * 100,
        "pre_minutes": len(pre),  # minutes with pre-market trades (Yahoo reports no pre-market volume)
        "pre_high": max((b.get("h") or b["c"] for b in pre if (b.get("h") or b["c"]) is not None), default=None),
        "last_trade": opened[0]["t"] if opened else pre[-1]["t"],
        "at_open": at_open,
    }


def candidates(watchlist: list[str], news: list[dict], movers: dict[str, list[dict]], now: datetime,
               limit: int) -> dict[str, dict]:
    """Symbols to scan, most likely gappers first: watchlist, fresh catalysts, then movers.
    symbol -> {"why": ..., "catalyst": ... or None}."""
    out: dict[str, dict] = {}
    for s in watchlist:
        out.setdefault(s, {"why": "watchlist", "catalyst": None})
    since = now - timedelta(hours=CATALYST_HOURS)
    for item in news:  # newest first
        if not item["tickers"]:
            continue
        try:
            published = datetime.fromisoformat(item["published"])
        except ValueError:
            continue
        if published < since:
            continue
        what = item["filing"]["label"] if item.get("filing") else item["title"][:90]
        entry = out.setdefault(item["tickers"][0], {"why": "catalyst", "catalyst": None})
        entry["catalyst"] = entry["catalyst"] or {"text": what, "at": item["published"],
                                                  "filing": bool(item.get("filing"))}
    for rows in movers.values():
        for row in rows:
            out.setdefault(row["symbol"], {"why": "mover", "catalyst": None})
    stocks = {s: v for s, v in out.items() if not s.startswith("^") and "=" not in s}
    return dict(list(stocks.items())[:limit])


def select(rows: list[dict], context: dict[str, dict], min_pct: float, min_minutes: int) -> list[dict]:
    """Gappers worth showing, biggest first. Before the open a gap needs trades in a few
    different minutes to be real; one stale print isn't a gap."""
    out = []
    for row in rows:
        if abs(row["gap_pct"]) < min_pct:
            continue
        if not row["at_open"] and row["pre_minutes"] < min_minutes:
            continue
        out.append({**row, **context.get(row["symbol"], {"why": "mover", "catalyst": None})})
    return sorted(out, key=lambda r: -abs(r["gap_pct"]))


def build_brief(day: date, gaps: list[dict], risk: dict[str, dict | None], min_pct: float,
                public_url: str = "", earnings: list[str] = (), day2: dict[str, dict] | None = None,
                extras: dict[str, list[str]] | None = None) -> dict:
    """The 09:15 phone note: the biggest gappers with what's behind them, and today's reverse
    splits among the stocks with a risk profile (watchlist and movers)."""
    lines = []
    for g in gaps[:MAX_BRIEF_ROWS]:
        why = []
        if g.get("catalyst"):
            why.append(g["catalyst"]["text"])
        elif g["why"] == "watchlist":
            why.append("watchlist")
        if run := (day2 or {}).get(g["symbol"]):
            why.append(f"day 2 ({run['change_pct']:+.0f}% yesterday)")
        why += (extras or {}).get(g["symbol"], [])
        level = (risk.get(g["symbol"]) or {}).get("level")
        if level in ("high", "medium"):
            why.append(f"dilution risk {level.upper()}")
        lines.append(f"{g['symbol']} {g['gap_pct']:+.1f}%" + (f" · {', '.join(why)}" if why else ""))
    if len(gaps) > MAX_BRIEF_ROWS:
        lines.append(f"+{len(gaps) - MAX_BRIEF_ROWS} more in the Gaps tab")
    if not lines:
        lines.append(f"No gaps of {min_pct:g}% or more among the stocks scanned.")
    if earnings:  # watchlist stocks reporting today
        lines += ["Earnings:", *(f"  {line}" for line in earnings)]
    splits = sorted(f"{symbol} 1-for-{rs['ratio']}" for symbol, summary in risk.items()
                    if summary and (rs := summary.get("reverse_split")) and rs["effective"] == day.isoformat())
    if splits:
        lines.append(f"Reverse splits today: {', '.join(splits)}")
    return {
        "category": "gaps",
        "title": f"Market Pulse · pre-market {day:%a %b} {day.day}",
        "body": "\n".join(lines),
        "url": f"{public_url.rstrip('/')}/#gaps" if public_url else None,
        "priority": "default",
        "tag": "sunrise",
    }
