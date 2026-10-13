"""The short sale restriction (SSR) and float rotation, on made-up days. No people, invented tickers."""
from datetime import date

import pytest
from scenario_kit import Day, Run

from app.ssr import carried_over, triggered

# --- the rule ------------------------------------------------------------------------------------------

def test_a_10_percent_drop_from_the_last_close_triggers_it():
    assert triggered(9.0, 10.0) and triggered(8.2, 10.0)
    assert not triggered(9.01, 10.0)
    assert not triggered(None, 10.0) and not triggered(9.0, None)
    assert triggered(0.9, 1.0)  # no rounding trap at exactly 10%


def test_yesterdays_drop_carries_over_to_today_but_not_further():
    days = [{"date": "2026-10-08", "low": 10.0, "close": 10.0},
            {"date": "2026-10-09", "low": 8.9, "close": 9.0},   # Friday: triggered
            {"date": "2026-10-12", "low": 8.8, "close": 8.9}]   # Monday: in effect, not re-triggered
    assert carried_over(days[:2], "2026-10-12") == "2026-10-09"  # over the weekend
    assert carried_over(days, "2026-10-13") is None              # Monday's 1% dip doesn't extend it
    assert carried_over(days, "2026-10-12") == "2026-10-09"      # today's own bar is ignored
    assert carried_over(days[:1], "2026-10-13") is None          # not enough history


# --- in the engine -------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path)


def test_ssr_carried_over_for_a_mover_and_triggered_today_for_a_watchlist_stock(day):
    day.movers("09:30", gainers={"ACME": 30.0})
    day.daily("09:00", ACME=[("2026-10-09", 9.9, 10.0), ("2026-10-12", 8.0, 8.5)],   # fell 20% yesterday
                       BOLT=[("2026-10-09", 9.9, 10.0), ("2026-10-12", 9.8, 10.0)])
    day.quotes("09:30", BOLT=-5.0, low={"BOLT": 9.5})
    day.quotes("10:00", BOLT=-8.0, low={"BOLT": 8.95})  # 10.5% under the 10.00 close
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32", "10:01")
    try:
        assert run.engine.ssr == {"ACME": {"since": "2026-10-12", "today": False},
                                  "BOLT": {"since": "2026-10-13", "today": True}}
        sent = [m["data"] for m in run.hub.messages if m["type"] == "ssr"]
        assert sent[-1] == run.engine.ssr and "BOLT" not in sent[0]  # BOLT appears when it trips
        assert run.phone == []  # a badge, not an alert
    finally:
        run.close()


def test_before_the_open_yesterdays_quote_doesnt_trip_it_again(day):
    day.quotes("09:00", BOLT=-12.0, low={"BOLT": 8.8}, volume={"BOLT": 9_000_000}, session=date(2026, 10, 12))
    day.floats("09:00", BOLT=4_000_000)
    run = Run(day, watchlist=["BOLT"]).steps("09:01", "09:02", "09:03")
    try:
        assert run.engine.ssr == {}  # the quote still describes yesterday; the daily bars decide
        assert run.engine._rotation_last == {}  # and so does its volume
    finally:
        run.close()


def test_trading_the_whole_float_alerts_once_and_the_first_reading_is_a_baseline(day):
    day.floats("09:00", ACME=2_000_000, BOLT=4_000_000, CRUX=None)
    day.quotes("09:30", BOLT=2.0, volume={"BOLT": 3_000_000})
    day.quotes("09:45", BOLT=2.0, volume={"BOLT": 4_200_000})   # through 1×
    day.quotes("10:00", BOLT=2.0, volume={"BOLT": 3_900_000})   # a stale row from Yahoo: lower again
    day.quotes("10:15", BOLT=2.0, volume={"BOLT": 9_000_000})   # past 1× again, then 2×: no second alert
    day.movers("09:30", gainers={"ACME": 40.0, "CRUX": 50.0}, volume={"ACME": 2_500_000, "CRUX": 90_000_000})
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32", "09:46", "10:01", "10:16")
    try:
        assert run.engine.floats == {"ACME": 2_000_000, "BOLT": 4_000_000}  # CRUX: Yahoo has no float
        notes = [(n["at"], n["title"], n["body"], n["category"]) for n in run.phone if "float" in n["title"]]
        # Rotation alerts go to the phone under "Unusual volume", which is off by default.
        assert notes == []
        alerts = [(a["symbol"], a["message"]) for a in run.browser("alerts") if a["kind"] == "rotation"]
        assert alerts == [("BOLT", "BOLT has traded its whole float: 4.2M shares today, 1.1× the 4M-share float")]
        # ACME was already past its float at the first reading: a baseline, not a crossing.
    finally:
        run.close()


def rotating(folder):
    day = Day(folder)
    day.floats("09:00", BOLT=4_000_000)
    day.quotes("09:30", BOLT=2.0, volume={"BOLT": 3_000_000})
    day.quotes("09:45", BOLT=2.0, volume={"BOLT": 4_200_000})
    return day


def test_rotation_alerts_can_be_switched_off_and_reach_the_phone_when_unusual_volume_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    on = Run(rotating(tmp_path / "on"), watchlist=["BOLT"], phone={"unusual": True}).steps("09:31", "09:32", "09:46")
    try:
        assert [(n["title"], n["category"], n["priority"]) for n in on.phone] == [
            ("BOLT · float rotated", "unusual", "default")]
    finally:
        on.close()
    off = Run(rotating(tmp_path / "off"), watchlist=["BOLT"], phone={"unusual": True})
    off.engine.state.rotation_alerts = False
    off.steps("09:31", "09:32", "09:46")
    try:
        assert off.phone == [] and not [a for a in off.browser("alerts") if a["kind"] == "rotation"]
    finally:
        off.close()


def test_lookups_are_made_once_a_day_and_a_symbol_yahoo_doesnt_know_is_given_up_on(day):
    day.floats("09:00", BOLT=4_000_000)
    day.quotes("09:30", BOLT=2.0)
    run = Run(day, watchlist=["BOLT", "ZZZZ"])
    calls = []
    real = run.player.call

    async def counting(name, fn, *args):
        calls.append((name, tuple(args[0]) if args else ()))
        return await real(name, fn, *args)
    run.engine.io.call = counting
    run.steps(*(f"09:{m:02d}" for m in range(31, 37)))
    try:
        floats = [symbols for name, symbols in calls if name == "fetch_floats"]
        assert floats == [("BOLT", "ZZZZ"), ("ZZZZ",), ("ZZZZ",)]  # 3 tries for the unknown one, then no more
        assert len([1 for name, _ in calls if name == "fetch_daily"]) == 3
    finally:
        run.close()
