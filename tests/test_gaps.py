import asyncio
from datetime import date, datetime

import pytest
from scenario_kit import Day, Run

from app.config import load_settings
from app.gaps import build_brief, candidates, premarket_row, scanning, select
from app.market import ET

TODAY = date(2026, 10, 13)  # Tuesday


def t(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return int(datetime(2026, 10, day, h, m, tzinfo=ET).timestamp())


def bar(day, hhmm, price, volume=0):
    return {"t": t(day, hhmm), "o": price, "c": price, "v": volume}


YESTERDAY = [bar(12, "15:58", 9.9), bar(12, "15:59", 10.0), bar(12, "17:30", 10.4)]  # last close 10.00


# --- the gap itself ------------------------------------------------------------------------------

def test_before_the_open_the_gap_is_to_the_latest_premarket_trade():
    bars = YESTERDAY + [bar(13, "07:00", 11.0), bar(13, "07:05", 11.5), bar(13, "08:10", 12.0)]
    row = premarket_row("ACME", bars, TODAY)
    assert row["prev_close"] == 10.0 and row["price"] == 12.0 and row["gap_pct"] == pytest.approx(20.0)
    assert row["pre_minutes"] == 3 and row["at_open"] is False
    assert row["last_trade"] == t(13, "08:10")


def test_after_the_open_the_gap_is_to_the_opening_price():
    bars = YESTERDAY + [bar(13, "09:00", 12.0), {"t": t(13, "09:30"), "o": 11.0, "c": 11.4, "v": 0},
                        bar(13, "09:31", 11.8)]
    row = premarket_row("ACME", bars, TODAY)
    assert row["price"] == 11.0 and row["gap_pct"] == pytest.approx(10.0) and row["at_open"] is True


def test_no_row_without_a_previous_close_or_a_trade_today():
    assert premarket_row("ACME", [bar(13, "07:00", 11.0)], TODAY) is None   # no history
    assert premarket_row("ACME", YESTERDAY, TODAY) is None                  # nothing yet today / holiday


@pytest.mark.parametrize(("when", "expected"), [
    (datetime(2026, 10, 13, 3, 59, tzinfo=ET), False), (datetime(2026, 10, 13, 4, 0, tzinfo=ET), True),
    (datetime(2026, 10, 13, 9, 59, tzinfo=ET), True), (datetime(2026, 10, 13, 10, 0, tzinfo=ET), False),
    (datetime(2026, 10, 17, 7, 0, tzinfo=ET), False),  # Saturday
])
def test_the_scanner_runs_4_to_10_on_weekdays(when, expected):
    assert scanning(when) is expected


# --- what gets scanned and shown -------------------------------------------------------------------

NOW = datetime(2026, 10, 13, 7, 30, tzinfo=ET)


def news(ticker, title, hours_ago, filing=None):
    published = datetime.fromtimestamp(NOW.timestamp() - hours_ago * 3600, ET).isoformat()
    return {"tickers": [ticker] if ticker else [], "title": title, "published": published,
            **({"filing": {"label": filing}} if filing else {})}


def test_candidates_put_the_watchlist_then_fresh_catalysts_then_movers_first():
    feed = [news("ACME", "Acme Rocket Corp: offering from shelf (424B5)", 1, filing="offering from shelf"),
            news("CRUX", "Crux reports topline data", 2), news("OLD", "Old news", 30),
            news(None, "Market wrap", 1), news("BOLT", "Bolt news", 1)]
    movers = {"gainers": [{"symbol": "DYNA"}, {"symbol": "ACME"}], "small_caps": [{"symbol": "^VIX"}]}
    picked = candidates(["BOLT"], feed, movers, NOW, limit=10)
    assert list(picked) == ["BOLT", "ACME", "CRUX", "DYNA"]
    assert picked["BOLT"]["why"] == "watchlist" and picked["BOLT"]["catalyst"]["text"] == "Bolt news"
    assert picked["ACME"] == {"why": "catalyst", "catalyst": {
        "text": "offering from shelf", "at": feed[0]["published"], "filing": True}}
    assert picked["DYNA"] == {"why": "mover", "catalyst": None}
    assert list(candidates(["BOLT"], feed, movers, NOW, limit=2)) == ["BOLT", "ACME"]


def test_select_keeps_real_gaps_biggest_first():
    rows = [{"symbol": s, "gap_pct": g, "pre_minutes": m, "at_open": o}
            for s, g, m, o in (("A", 4.0, 10, False), ("B", -25.0, 30, False), ("C", 2.0, 30, False),
                               ("D", 15.0, 1, False), ("E", 15.0, 1, True))]
    shown = select(rows, {"B": {"why": "catalyst", "catalyst": {"text": "offering"}}}, min_pct=3, min_minutes=5)
    assert [r["symbol"] for r in shown] == ["B", "E", "A"]  # D: one stale print; C: too small
    assert shown[0]["why"] == "catalyst" and shown[2]["why"] == "mover"


def test_brief_names_the_gap_its_cause_and_elevated_risk():
    gaps = [{"symbol": "ACME", "gap_pct": -22.0, "why": "catalyst", "catalyst": {"text": "offering from shelf"}},
            {"symbol": "BOLT", "gap_pct": 4.5, "why": "watchlist", "catalyst": None},
            {"symbol": "DYNA", "gap_pct": 6.0, "why": "mover", "catalyst": None}]
    note = build_brief(TODAY, gaps, {"ACME": {"level": "high"}, "DYNA": {"level": "low"}}, 3.0,
                       "http://192.168.1.20:8000")
    assert note["category"] == "gaps" and note["title"] == "Market Pulse · pre-market Tue Oct 13"
    assert note["body"].splitlines() == ["ACME -22.0% · offering from shelf, dilution risk HIGH",
                                         "BOLT +4.5% · watchlist", "DYNA +6.0%"]
    assert note["url"] == "http://192.168.1.20:8000/#gaps"
    assert build_brief(TODAY, [], {}, 3.0)["body"] == "No gaps of 3% or more among the stocks scanned."


def test_brief_time_must_be_hh_mm(tmp_path):
    (tmp_path / "c.toml").write_text('gap_brief_time = "9am"')
    with pytest.raises(ValueError, match="gap_brief_time"):
        load_settings(tmp_path / "c.toml")


# --- scenarios ---------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path, start="04:00")


def test_a_premarket_morning_shows_real_gappers_and_briefs_the_phone(day):
    day.filing("06:45", "424B5", "ACME")
    day.profile("ACME", "high")
    day.premarket("07:00", ACME=(-22.0, 40), BOLT=(4.5, 12), FERN=(3.5, 2), DYNA=(1.0, 50))
    run = Run(day, watchlist=["BOLT"]).steps("04:05", "06:50", "07:05")
    try:
        gaps = run.engine.gaps
        assert [(g["symbol"], g["why"]) for g in gaps] == [("ACME", "catalyst"), ("BOLT", "watchlist")]
        assert gaps[0]["catalyst"]["text"] == "offering from shelf" and gaps[0]["catalyst"]["filing"]
        assert run.browser("gaps")[-1]["symbol"] == "BOLT"  # sent to the browser too

        assert run.brief("09:15") is True
        brief = next(n for n in run.phone if n["category"] == "gaps")
        assert brief["body"].splitlines() == ["ACME -22.0% · offering from shelf, dilution risk HIGH",
                                              "BOLT +4.5% · watchlist"]
        assert brief["at"] == "09:15"
    finally:
        run.close()


def test_no_brief_on_a_morning_without_premarket_trading(day):
    run = Run(day, watchlist=["BOLT"]).steps("04:05", "07:05")
    try:
        assert run.brief("09:15") is False
        assert run.phone_titles("gaps") == []
        assert run.engine.gaps == []
    finally:
        run.close()


def test_a_restart_after_brief_time_sends_the_missed_brief_before_the_open_once(day):
    from scenario_kit import ts

    from app.clock import clock
    from app.config import UserState
    day.premarket("07:00", BOLT=(4.5, 12))
    run = Run(day, watchlist=["BOLT"]).steps("07:05")
    e = run.engine
    try:
        clock.freeze(ts("09:00"))
        assert not e._brief_missed()  # not due yet
        clock.freeze(ts("09:20"))  # started after gap_brief_time, which nothing sent
        assert e._brief_missed()
        assert run.brief("09:20") is True and run.phone_titles("gaps") == ["Market Pulse · pre-market Tue Oct 13"]
        assert UserState(e.state.path).brief_sent == "2026-10-13"  # survives the next restart
        assert not e._brief_missed()  # so that one won't repeat it
        e.state.brief_sent = None
        clock.freeze(ts("09:30"))
        assert not e._brief_missed()  # after the open a pre-market brief is old news
    finally:
        run.close()


def test_the_brief_loop_catches_up_only_while_the_market_is_still_closed(day, monkeypatch):
    from scenario_kit import ts

    from app.clock import clock
    from app.engine.gaps import BRIEF_CATCH_UP

    class Stop(Exception):
        pass
    run = Run(day, watchlist=["BOLT"])
    e = run.engine
    sleeps, sent = [], []

    async def sleep(seconds):
        sleeps.append(round(seconds))
        clock.freeze(clock.time() + seconds)  # time passes while it waits

    async def send():
        sent.append(clock.now(ET).strftime("%H:%M"))
        raise Stop
    monkeypatch.setattr(clock, "sleep", sleep)
    monkeypatch.setattr(e, "_try_gap_brief", send)
    try:
        clock.freeze(ts("09:20"))
        with pytest.raises(Stop):
            run._runner.run(e._gap_brief_loop())
        assert sleeps == [BRIEF_CATCH_UP] and sent == ["09:21"]  # the missed one, after the first scan
        sleeps.clear()
        sent.clear()
        clock.freeze(ts("09:29"))  # by the end of the wait the market has opened: tomorrow's instead
        with pytest.raises(Stop):
            run._runner.run(e._gap_brief_loop())
        assert sleeps[0] == BRIEF_CATCH_UP and sent == ["09:15"]
        assert clock.today(ET) == date(2026, 10, 14)
    finally:
        monkeypatch.undo()
        run.close()


def test_the_scanner_is_idle_after_ten_and_keeps_the_morning_list(day):
    day.premarket("07:00", BOLT=(6.0, 20))
    run = Run(day, watchlist=["BOLT"]).steps("07:05")
    calls = []
    original = run.player.call

    async def counting(name, fn, *args):
        calls.append(name)
        return await original(name, fn, *args)
    run.player.call = counting
    try:
        run.steps("10:30")
        assert "fetch_premarket" not in calls
        assert [g["symbol"] for g in run.engine.gaps] == ["BOLT"]
        assert run.engine.health.status_of("gaps")["state"] == "ok"
    finally:
        run.close()


def test_replay_serves_premarket_rows_for_whichever_symbols_are_asked(tmp_path):
    from app.clock import clock
    from app.replay import Player
    day = Day(tmp_path, start="04:00")
    day.premarket("07:00", ACME=(-22.0, 40), BOLT=(4.5, 12))
    path = day.build(["BOLT"])
    player = Player(path)
    clock.freeze(datetime(2026, 10, 13, 7, 1, tzinfo=ET).timestamp())
    try:
        rows = asyncio.run(player.call("fetch_premarket", None, ["BOLT", "ZZZZ"]))
        assert [r["symbol"] for r in rows] == ["BOLT"]
    finally:
        clock.reset()


def test_new_catalysts_and_movers_wake_the_gap_scan(day):
    day.filing("06:45", "424B5", "ACME")
    day.movers("06:46", gainers={"DYNA": 12.0})
    run = Run(day, watchlist=["BOLT"])
    try:
        run.engine._wake_gaps.clear()
        run.steps("06:50")
        assert run.engine._wake_gaps.is_set()  # the gap loop rescans now instead of within a minute
    finally:
        run.close()


def test_a_filing_on_a_premarket_gapper_alerts_with_the_gap_as_the_reason(day):
    day.headline("06:00", "Acme Rocket Corp (NASDAQ: ACME) misses a launch window")  # makes it a candidate
    day.premarket("07:00", ACME=(-22.0, 40))
    day.filing("07:30", "424B5", "ACME")
    day.profile("ACME", "high")
    run = Run(day, watchlist=["BOLT"]).steps("04:05", "07:05", "07:31")
    try:
        assert run.phone_titles("filings") == ["ACME · offering from shelf"]
        assert run.phone[0]["body"].endswith("(gapping -22% pre-market, dilution risk HIGH)")
        assert run.engine.reactions_report(1)["recent"][0]["context"] == "mover"
    finally:
        run.close()


def test_the_gap_beats_yesterdays_movers_list_as_the_reason(day):
    day.movers("04:00", small_caps={"ACME": 30.0})  # yesterday's session
    day.premarket("07:00", ACME=(-22.0, 40))
    day.filing("07:30", "8-K", "ACME", items=[("3.02", "Unregistered Sales of Equity Securities")])
    run = Run(day, watchlist=["BOLT"]).steps("04:05", "07:05", "07:31")
    try:
        assert run.phone[0]["body"].endswith("(gapping -22% pre-market)")
    finally:
        run.close()


def test_gappers_stop_counting_as_movers_after_the_scan_window(day):
    day.headline("06:00", "Acme Rocket Corp (NASDAQ: ACME) misses a launch window")
    day.premarket("07:00", ACME=(-22.0, 40))
    day.filing("10:30", "424B5", "ACME")
    run = Run(day, watchlist=["BOLT"]).steps("04:05", "07:05", "10:31")
    try:
        assert run.phone_titles("filings") == []  # by 10:30 the movers lists are today's
    finally:
        run.close()
