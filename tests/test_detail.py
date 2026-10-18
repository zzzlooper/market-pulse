import asyncio

from app import market
from app.config import Settings, UserState
from app.engine import Engine
from app.market import _cached, next_earnings


class Hub:
    async def broadcast(self, msg):
        pass


def test_next_earnings_picks_earliest_future_date():
    now = 1_000
    assert next_earnings({"earningsTimestamp": 900, "earningsTimestampStart": 1_500}, now) == 1_500
    assert next_earnings({"earningsTimestamp": 1_200, "earningsTimestampStart": 1_500}, now) == 1_200
    assert next_earnings({"earningsTimestamp": 900}, now) is None
    assert next_earnings({}, now) is None


def test_next_earnings_goes_by_the_market_clock_so_a_replay_sees_that_days_reports():
    from app.clock import clock
    replayed = 1_791_898_200  # a weekday morning in the past (2026-10-13 09:30 ET)
    clock.freeze(replayed)
    try:
        report = replayed + 7 * 3600  # after that day's close: still ahead in the replay
        assert next_earnings({"earningsTimestamp": report}) == report
        assert next_earnings({"earningsTimestamp": replayed - 60}) is None  # already behind it
    finally:
        clock.reset()


def test_cached_reuses_value_within_ttl():
    calls = []
    load = lambda: calls.append(1) or len(calls)
    assert _cached(("test", "x"), 60, load) == 1
    assert _cached(("test", "x"), 60, load) == 1
    assert _cached(("test", "y"), 60, load) == 2
    assert _cached(("test", "z"), -1, load) == 3  # already expired: next call reloads
    assert _cached(("test", "z"), 60, load) == 4


def run_detail(tmp_path, monkeypatch, profile, bars, symbol="ACME"):
    def fake_profile(sym):
        if isinstance(profile, Exception):
            raise profile
        return profile

    monkeypatch.setattr(market, "fetch_profile", fake_profile)
    monkeypatch.setattr(market, "fetch_chart", lambda sym, r: {"range": r, "interval": "1m", "bars": bars})

    async def scenario():
        state = UserState(tmp_path / "state.json")
        state.watchlist = ["ACME"]
        engine = Engine(Settings(), state, Hub())
        engine.yahoo_quotes = [{"symbol": "ACME", "price": 9.5, "change_pct": 3.0}]
        detail = await engine.ticker_detail(symbol, "1d")
        await engine.news.aclose()
        await engine.halts.aclose()
        return detail

    return asyncio.run(scenario())


BAR = {"t": 1_791_532_980, "o": 9, "h": 10, "l": 9, "c": 9.5, "v": 1000}


def test_detail_combines_profile_chart_and_quote(tmp_path, monkeypatch):
    d = run_detail(tmp_path, monkeypatch, {"name": "Acme Corp"}, [BAR])
    assert d["profile"]["name"] == "Acme Corp" and d["chart"]["bars"] == [BAR]
    assert d["quote"]["price"] == 9.5
    assert d["errors"] == []


def test_detail_without_streamed_quote_builds_one_from_bars(tmp_path, monkeypatch):
    d = run_detail(tmp_path, monkeypatch, {"name": "Other", "prev_close": 10.0}, [BAR], symbol="OTHER")
    assert d["quote"]["symbol"] == "OTHER" and d["quote"]["price"] == 9.5
    assert round(d["quote"]["change_pct"], 2) == -5.0


def test_quote_from_bars_counts_todays_regular_session_only():
    from datetime import datetime

    from app.market import ET, quote_from_bars

    now = datetime(2026, 10, 9, 12, 45, tzinfo=ET)  # half the session elapsed
    at = lambda h, m, day=9: int(datetime(2026, 10, day, h, m, tzinfo=ET).timestamp())
    bars = [
        {"t": at(15, 0, day=8), "c": 9.0, "v": 999_999},  # yesterday
        {"t": at(8, 0), "c": 9.5, "v": 50_000},           # pre-market
        {"t": at(9, 30), "c": 10.0, "v": 400_000},
        {"t": at(12, 44), "c": 11.0, "v": 100_000},
    ]
    q = quote_from_bars("ACME", bars, {"prev_close": 10.0, "avg_volume_10d": 1_000_000}, now)
    assert q["price"] == 11.0 and round(q["change_pct"], 2) == 10.0
    assert q["volume"] == 500_000
    assert q["rvol"] == 1.0  # 500k in half a session vs 1M a day
    assert quote_from_bars("ACME", [], {}, now) is None


def test_detail_unknown_symbol_is_none(tmp_path, monkeypatch):
    assert run_detail(tmp_path, monkeypatch, {}, [], symbol="NOPE") is None


def test_detail_survives_profile_failure(tmp_path, monkeypatch):
    d = run_detail(tmp_path, monkeypatch, RuntimeError("yahoo down"), [BAR])
    assert d["profile"] == {} and d["errors"] == ["profile"] and d["chart"]["bars"] == [BAR]
