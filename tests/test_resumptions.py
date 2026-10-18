"""Halt resumptions: the heads-up before, and how trading resumed. Invented tickers and prices."""
import pytest
from scenario_kit import Day, Run, iso

from app.halts import relevant, resumed_message, resuming_message, resumption

HALT = {"id": "ACME|10/13/2026|10:00:00", "symbol": "ACME", "reason": "Volatility pause (limit up/down)",
        "halted_at": iso("10:00"), "resume_trade_at": iso("10:05"), "pause_price": 12.4}


def bar(hhmm, o, h=None, low=None, c=None):
    from scenario_kit import ts
    return {"t": int(ts(hhmm)), "o": o, "h": h or o, "l": low or o, "c": c or o}


# --- the rules ------------------------------------------------------------------------------------------

def test_how_trading_resumed_from_the_first_minute_against_the_last_trade_before():
    bars = [bar("09:58", 11.8), bar("09:59", 12.0, c=12.3), bar("10:05", 13.5, h=14.1, low=13.2), bar("10:06", 13.0)]
    info = resumption(HALT, bars)
    assert info == {"resumed_at": iso("10:05"), "open": 13.5, "high": 14.1, "low": 13.2,
                    "halt_price": 12.3, "change_pct": 9.76}
    assert resumed_message(HALT, info) == ("ACME resumed at 10:05 ET: first trade $13.50, +9.8% from $12.30 "
                                           "before the halt (first minute $13.20–$14.10)")


def test_no_answer_until_the_first_minute_after_resuming_is_there():
    assert resumption(HALT, [bar("09:59", 12.0)]) is None   # Yahoo's bar hasn't arrived yet
    only_after = resumption(HALT, [bar("10:07", 13.0)])     # nothing before the halt on the chart
    assert only_after["halt_price"] == 12.4 and only_after["resumed_at"] == iso("10:07")  # the pause price instead


def test_the_heads_up_says_when_and_why():
    assert resuming_message(HALT) == ("ACME resumes trading in about a minute, at 10:05 ET "
                                      "(halted 10:00 · Volatility pause (limit up/down), pause price $12.40)")


def test_which_halts_count_follows_the_halt_alert_setting():
    assert relevant(HALT, ["ACME"], "watchlist") and not relevant(HALT, ["BOLT"], "watchlist")
    assert relevant(HALT, [], "all") and not relevant(HALT, ["ACME"], "off")
    assert relevant({**HALT, "symbol": "BRK.B"}, ["BRK-B"], "watchlist")  # Yahoo's spelling on the watchlist


# --- in the engine -------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path)


def prices(hhmm):
    return 4.0 if hhmm < "10:00" else 4.6  # halted at 4.00, resumes 15% higher


def test_a_watchlist_halt_warns_a_minute_before_and_reports_how_it_resumed(day):
    day.halt("10:00", "BOLT", "LUDP", resumes="10:05", pause_price=4.2)
    day.chart("BOLT", prices)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:01", "10:03", "10:04", "10:05", "10:06", "10:07")
    try:
        notes = [(n["at"], n["title"], n["priority"]) for n in run.phone if n["category"] == "halts"]
        assert notes == [("10:01", "BOLT · trading halted", "urgent"),
                         ("10:04", "BOLT · resumes in a minute", "high"),   # not at 10:03: two minutes out
                         ("10:06", "BOLT · resumed", "high")]               # the first minute's bar is in
        resumed = next(n for n in run.phone if n["title"] == "BOLT · resumed")
        assert resumed["body"] == ("BOLT resumed at 10:05 ET: first trade $4.60, +15.0% from $4.00 before the halt "
                                   "(first minute $4.60–$4.60)")
        info = run.engine.halt_info["BOLT|10/13/2026|10:00:00"]
        assert info["change_pct"] == 15.0
        assert {"BOLT|10/13/2026|10:00:00": info} in [m["data"] for m in run.hub.messages if m["type"] == "halt_info"]
    finally:
        run.close()


def test_a_resumption_before_startup_and_halts_off_the_watchlist_stay_quiet(day):
    day.halt("09:00", "BOLT", "LUDP", halted="08:50", resumes="08:55")  # over before the app started
    day.halt("10:00", "CRUX", "LUDP", resumes="10:05")                  # not on the watchlist
    day.chart("BOLT", prices)
    day.chart("CRUX", prices)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:01", "10:04", "10:06")
    try:
        assert [n["title"] for n in run.phone if n["category"] == "halts"] == []
        assert run.engine.halt_info == {}
    finally:
        run.close()


def test_a_resumption_minutes_before_startup_isnt_reported_either(day):
    day.halt("09:20", "BOLT", "LUDP", resumes="09:25")  # resumed 6 minutes before the app started
    day.chart("BOLT", prices)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32", "09:40")
    try:
        assert [n["title"] for n in run.phone if n["category"] == "halts"] == []
        assert run.engine.halt_info == {}
    finally:
        run.close()


def test_a_resumption_under_a_minute_away_at_startup_still_gets_its_heads_up(day):
    day.halt("10:00", "BOLT", "LUDP", resumes="10:05")
    day.chart("BOLT", prices)
    run = Run(day, watchlist=["BOLT"]).steps("10:04:30")  # the halt itself is the baseline
    try:
        assert [(n["at"], n["title"]) for n in run.phone if n["category"] == "halts"] == [
            ("10:04:30", "BOLT · resumes in a minute")]
    finally:
        run.close()


def test_the_heads_up_comes_once_however_often_the_clock_is_checked(day):
    day.halt("10:00", "BOLT", "LUDP", resumes="10:05")
    day.chart("BOLT", prices)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:01", "10:04:10", "10:04:30", "10:04:50")
    try:
        assert [(n["at"], n["title"]) for n in run.phone if n["title"] == "BOLT · resumes in a minute"] == [
            ("10:04:10", "BOLT · resumes in a minute")]
    finally:
        run.close()


def test_with_halt_alerts_for_all_stocks_any_resumption_counts(day):
    day.halt("10:00", "CRUX", "LUDP", resumes="10:05")
    day.chart("CRUX", prices)
    run = Run(day, watchlist=["BOLT"])
    run.engine.state.halt_alerts = "all"
    run.steps("09:31", "10:01", "10:04", "10:06")
    try:
        assert [n["title"] for n in run.phone if n["category"] == "halts"] == [
            "CRUX · trading halted", "CRUX · resumes in a minute", "CRUX · resumed"]
    finally:
        run.close()
