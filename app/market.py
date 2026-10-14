"""Prices, top movers and relative volume via Yahoo Finance (yfinance).

Yahoo quotes are delayed (~15 min on most US exchanges) and yfinance is an unofficial
client, so this is a personal tool, not a trading-grade feed.
"""
from __future__ import annotations

import logging
import math
import threading
import time as clock
from collections.abc import Callable, Iterable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from .clock import clock as app_clock

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)
SESSION_MINUTES = 390

# Yahoo predefined screeners. small_caps widens the pool scanned for unusual volume.
SCREENS = {
    "gainers": "day_gainers",
    "losers": "day_losers",
    "active": "most_actives",
    "small_caps": "small_cap_gainers",
}


def is_regular_bar(t: int) -> bool:
    """Whether a bar starting at epoch t falls in the 9:30–16:00 ET weekday session."""
    dt = datetime.fromtimestamp(t, ET)
    return dt.weekday() < 5 and OPEN <= dt.time() < CLOSE


def fetch_premarket(symbols: list[str]) -> list[dict]:
    """Pre-market gap rows (see gaps.premarket_row) from one batched download of 2 days of
    1-minute bars with pre/post-market."""
    from .gaps import premarket_row

    if not symbols:
        return []
    df = yf.download(symbols, period="2d", interval="1m", prepost=True, group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    today = app_clock.today(ET)
    rows = []
    for sym in symbols:
        frame = _bars_for(df, sym)
        if frame is None or frame.empty:
            continue
        bars = [{"t": int(ts.timestamp()), "o": _num(r["Open"]), "h": _num(r["High"]), "c": _num(r["Close"]),
                 "v": int(_num(r["Volume"]) or 0)} for ts, r in frame.iterrows()]
        if row := premarket_row(sym, bars, today):
            rows.append(row)
    return rows


def session_fraction(now: datetime | None = None) -> float:
    """Share of today's regular session elapsed, used to pace intraday volume.

    Outside regular hours the latest volume covers a full session, so 1.0. Floored at
    0.05 so the first minutes after the open don't produce absurd ratios.
    """
    now = (now or app_clock.now(ET)).astimezone(ET)
    if now.weekday() >= 5 or not (OPEN <= now.time() < CLOSE):
        return 1.0
    minutes = (now.hour * 60 + now.minute) - (OPEN.hour * 60 + OPEN.minute)
    return max(minutes / SESSION_MINUTES, 0.05)


def pace(session_date: date | None, now: datetime | None = None) -> float:
    """session_fraction, but only when the volume figure belongs to today's session."""
    now = (now or app_clock.now(ET)).astimezone(ET)
    if session_date != now.date():
        return 1.0
    return session_fraction(now)


def relative_volume(volume: float, avg_volume: float, fraction: float = 1.0) -> float | None:
    """Volume vs. the average for the same share of a session (RVOL)."""
    if not volume or not avg_volume:
        return None
    return volume / (avg_volume * fraction)


def find_unusual(rows: Iterable[dict], threshold: float, min_volume: int, limit: int = 25) -> list[dict]:
    """Rows with RVOL >= threshold and enough absolute volume to matter, highest first."""
    hits: dict[str, dict] = {}
    for row in rows:
        rvol = row.get("rvol")
        if rvol and rvol >= threshold and (row.get("volume") or 0) >= min_volume:
            hits.setdefault(row["symbol"], row)
    return sorted(hits.values(), key=lambda r: r["rvol"], reverse=True)[:limit]


def fetch_movers() -> dict[str, list[dict]]:
    """Every screener that succeeded, keyed like SCREENS."""
    movers = {}
    for key, screen in SCREENS.items():
        try:
            result = yf.screen(screen, count=25)
            movers[key] = [_screen_row(q) for q in result.get("quotes", []) if q.get("symbol")]
        except Exception:
            log.exception("screener %s failed", screen)
    return movers


def _screen_row(q: dict) -> dict:
    ts = q.get("regularMarketTime")
    session_date = datetime.fromtimestamp(ts, ET).date() if ts else None
    volume = q.get("regularMarketVolume") or 0
    avg = q.get("averageDailyVolume10Day") or q.get("averageDailyVolume3Month") or 0
    return {
        "symbol": q["symbol"],
        "name": q.get("shortName") or q.get("longName") or "",
        "price": q.get("regularMarketPrice"),
        "change": q.get("regularMarketChange"),
        "change_pct": q.get("regularMarketChangePercent"),
        "volume": volume,
        "avg_volume": avg,
        "rvol": relative_volume(volume, avg, pace(session_date)),
        "market_cap": q.get("marketCap"),
        # For the short sale restriction: the regular session's low against the last close.
        "low": q.get("regularMarketDayLow"),
        "high": q.get("regularMarketDayHigh"),
        "prev_close": q.get("regularMarketPreviousClose"),
        "session": session_date.isoformat() if session_date else None,
    }


def fetch_quotes(symbols: list[str]) -> list[dict]:
    """Latest price and volume per symbol, from daily bars (today's bar updates intraday)."""
    if not symbols:
        return []
    df = yf.download(
        symbols, period="1mo", interval="1d", group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )
    rows = []
    for sym in symbols:
        bars = _bars_for(df, sym)
        if bars is None or len(bars) < 2:
            continue
        last, prev = bars.iloc[-1], bars.iloc[-2]
        price, prev_close = _num(last["Close"]), _num(prev["Close"])
        volume = int(_num(last["Volume"]) or 0)
        avg = _num(bars["Volume"].iloc[-11:-1].mean()) or 0.0
        rows.append({
            "symbol": sym,
            "price": price,
            "prev_close": prev_close,
            "change": price - prev_close if prev_close else None,
            "change_pct": (price / prev_close - 1) * 100 if prev_close else None,
            "volume": volume,
            "avg_volume": avg,
            "rvol": relative_volume(volume, avg, pace(bars.index[-1].date())),
            "high": _num(last["High"]),
            "low": _num(last["Low"]),
            "session": bars.index[-1].date().isoformat(),
        })
    return rows


def fetch_daily(symbols: list[str]) -> list[dict]:
    """The last few sessions' lows and closes per symbol (regular hours), oldest first: whether
    yesterday's drop put a short sale restriction on today (see app/ssr.py)."""
    if not symbols:
        return []
    df = yf.download(symbols, period="5d", interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    rows = []
    for sym in symbols:
        bars = _bars_for(df, sym)
        if bars is None or bars.empty:
            continue
        rows.append({"symbol": sym, "days": [
            {"date": t.date().isoformat(), "low": _num(b["Low"]), "close": _num(b["Close"])}
            for t, b in bars.iterrows()]})
    return rows


def fetch_floats(symbols: list[str]) -> list[dict]:
    """Shares in the public float, the market cap and the industry per symbol, from Yahoo's
    company data (None when unknown). The market cap guesses a stock's LULD tier; the industry
    finds sympathy movers."""
    from concurrent.futures import ThreadPoolExecutor

    def one(symbol: str) -> dict | None:
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception as exc:  # one unknown symbol shouldn't sink the rest
            log.info("float for %s unavailable: %s", symbol, exc)
            return None
        return {"symbol": symbol, "float_shares": info.get("floatShares"), "market_cap": info.get("marketCap"),
                "industry": info.get("industry")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [row for row in pool.map(one, symbols) if row]


def _bars_for(df: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if sym not in df.columns.get_level_values(0):
            return None
        df = df[sym]
    return df.dropna(subset=["Close"])


def _num(value) -> float | None:
    """Plain float, with NaN mapped to None so it serialises as valid JSON."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


# --- ticker detail -------------------------------------------------------------------

PROFILE_TTL = 600   # company facts barely change intraday
CHART_TTL = 30      # bars are delayed anyway; this just absorbs repeated opens/refreshes
CHART_RANGES = {
    # range -> (yfinance period, interval, include pre/post-market)
    "1d": ("1d", "1m", True),
    "5d": ("5d", "5m", False),
}

_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, ttl: float, load: Callable[[], object]):
    """Memoise load() for ttl seconds. Called from worker threads, hence the lock."""
    now = clock.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = load()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def next_earnings(info: dict, now: float | None = None) -> int | None:
    """Earliest future earnings timestamp. Yahoo's earningsTimestamp can still hold the last
    report while earningsTimestampStart already points at the next one, or vice versa."""
    now = now if now is not None else clock.time()
    candidates = [info.get(k) for k in ("earningsTimestampStart", "earningsTimestamp")]
    future = [int(t) for t in candidates if t and t > now]
    return min(future) if future else None


def fetch_earnings(symbols: list[str]) -> list[dict]:
    """Next earnings report per symbol (see earnings.earnings_row), from Yahoo company data."""
    from concurrent.futures import ThreadPoolExecutor

    from .earnings import earnings_row

    def one(symbol: str) -> dict | None:
        try:
            return earnings_row(symbol, yf.Ticker(symbol).info or {}, app_clock.time())
        except Exception as exc:  # one unknown symbol shouldn't sink the rest
            log.info("earnings for %s unavailable: %s", symbol, exc)
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [row for row in pool.map(one, symbols) if row]


INDUSTRY_TTL = 120   # an industry's movers, shared by every panel open on one of its stocks
US_EXCHANGES = ("NMS", "NCM", "NGM", "NYQ", "ASE")  # Nasdaq tiers, NYSE, NYSE American: no OTC


def fetch_industry_movers(industry: str, count: int = 10) -> list[dict]:
    """Today's biggest gainers in a Yahoo industry ("Biotechnology"), US-listed and $1 or more."""
    def load() -> list[dict]:
        query = yf.EquityQuery("and", [
            yf.EquityQuery("eq", ["industry", industry]), yf.EquityQuery("eq", ["region", "us"]),
            yf.EquityQuery("gte", ["intradayprice", 1]), yf.EquityQuery("is-in", ["exchange", *US_EXCHANGES])])
        result = yf.screen(query, sortField="percentchange", sortAsc=False, size=count)
        return [{"symbol": q["symbol"], "name": q.get("shortName") or "", "price": q.get("regularMarketPrice"),
                 "change_pct": q.get("regularMarketChangePercent"), "market_cap": q.get("marketCap")}
                for q in result.get("quotes", []) if q.get("symbol")]
    return _cached(("industry", industry, count), INDUSTRY_TTL, load)


def fetch_profile(symbol: str) -> dict:
    """Company facts and today's stats. Empty dict when Yahoo doesn't know the symbol."""
    def load() -> dict:
        info = yf.Ticker(symbol).info or {}
        if not info.get("quoteType"):
            return {}
        prev, open_ = info.get("previousClose"), info.get("open")
        return {
            "name": info.get("longName") or info.get("shortName") or "",
            "exchange": info.get("fullExchangeName") or info.get("exchange") or "",
            "quote_type": info.get("quoteType"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "website": info.get("website"),
            "market_cap": info.get("marketCap"),
            "float_shares": info.get("floatShares"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "short_ratio": info.get("shortRatio"),
            "avg_volume": info.get("averageVolume"),
            "avg_volume_10d": info.get("averageVolume10days"),
            "week52_low": info.get("fiftyTwoWeekLow"),
            "week52_high": info.get("fiftyTwoWeekHigh"),
            "prev_close": prev,
            "open": open_,
            "gap_pct": (open_ / prev - 1) * 100 if open_ and prev else None,
            "day_low": info.get("dayLow"),
            "day_high": info.get("dayHigh"),
            "pe": info.get("trailingPE"),
            "beta": info.get("beta"),
            "next_earnings": next_earnings(info),
        }
    return _cached(("profile", symbol), PROFILE_TTL, load)


def quote_from_bars(symbol: str, bars: list[dict], profile: dict, now: datetime | None = None) -> dict | None:
    """A quote row (price, change, volume, RVOL) built from 1-day bars, for symbols that are in
    neither the watchlist nor the screeners. Volume counts only today's regular session, to
    compare like-for-like with the daily average."""
    if not bars:
        return None
    now = (now or app_clock.now(ET)).astimezone(ET)
    session = [
        b for b in bars
        if (t := datetime.fromtimestamp(b["t"], ET)).date() == now.date() and OPEN <= t.time() < CLOSE
    ]
    price, prev = bars[-1]["c"], profile.get("prev_close")
    volume = sum(b["v"] for b in session)
    return {
        "symbol": symbol,
        "price": price,
        "change_pct": (price / prev - 1) * 100 if price and prev else None,
        "volume": volume,
        "rvol": relative_volume(volume, profile.get("avg_volume_10d") or profile.get("avg_volume") or 0,
                                session_fraction(now)) if session else None,
    }


def fetch_chart(symbol: str, range_: str = "1d") -> dict:
    """Intraday OHLCV bars, t = epoch seconds. 1d includes pre/post-market (from 04:00 ET)."""
    period, interval, prepost = CHART_RANGES[range_]

    def load() -> dict:
        df = yf.Ticker(symbol).history(period=period, interval=interval, prepost=prepost, auto_adjust=False)
        df = df.dropna(subset=["Close"])
        bars = [
            {
                "t": int(ts.timestamp()),
                "o": _num(row["Open"]), "h": _num(row["High"]), "l": _num(row["Low"]),
                "c": _num(row["Close"]), "v": int(_num(row["Volume"]) or 0),
            }
            for ts, row in df.iterrows()
        ]
        return {"range": range_, "interval": interval, "bars": bars}
    return _cached(("chart", symbol, range_), CHART_TTL, load)
