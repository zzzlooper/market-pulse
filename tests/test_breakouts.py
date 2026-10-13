"""Breakout alerts: through the pre-market high, and a new high of day after a pullback.
Invented tickers and prices only."""
from datetime import date

import pytest
from scenario_kit import Day, Run, ts

from app.breakouts import HOD_COOLDOWN, Breakouts
from app.gaps import premarket_row

DAY = "2026-10-13"


def feed(b, symbol, prices, regular=True, start=0.0, **kw):
    """Prices one minute apart; every breakout found, as (kind, message)."""
    out = []
    for i, p in enumerate(prices):
        out += b.update(symbol, p, day=DAY, now=start + 60 * i, regular=regular, **kw)
    return out


# --- the rules ------------------------------------------------------------------------------------------

def test_the_pre_market_high_counts_after_the_open_once_and_from_the_last_pre_market_price():
    b = Breakouts()
    # Before the open: never, even through a pre-market high the last scan reported.
    assert feed(b, "ACME", [5.0, 5.6, 5.2], regular=False, pre_high=5.5) == []
    found = feed(b, "ACME", [5.6], pre_high=5.5)  # opened straight through it
    assert found == [("pmh", "ACME broke its pre-market high of $5.50 (now $5.60)")]
    assert feed(b, "ACME", [5.45, 5.7], pre_high=5.5) == []  # once a day (and a 3% dip is no pullback)


def test_a_first_reading_after_the_open_is_only_a_baseline():
    b = Breakouts()
    assert feed(b, "ACME", [5.6, 5.8], pre_high=5.5) == []  # no pre-market price seen: no crossing seen


def test_a_new_high_of_day_counts_only_after_a_pullback():
    b = Breakouts()
    assert feed(b, "BOLT", [10.0, 10.5, 11.0, 11.2]) == []  # grinding up: every reading a new high, no pullback
    found = feed(b, "BOLT", [10.9, 10.5, 11.0, 11.3], start=600)  # 6% off the 11.20 high, then through it
    assert found == [("hod", "BOLT made a new high of day at $11.30, after pulling back 6% from $11.20 to $10.50")]
    assert feed(b, "BOLT", [11.0, 11.4], start=900) == []  # a 3% dip isn't a pullback


def test_new_highs_wait_out_the_cooldown_and_a_new_day_starts_over():
    b = Breakouts()
    assert len(feed(b, "BOLT", [10.0, 9.4, 10.1])) == 1                     # 6% pullback, then a new high
    assert feed(b, "BOLT", [9.4, 10.3], start=180) == []                    # 3 minutes later: cooling down
    assert len(feed(b, "BOLT", [9.5, 10.4], start=180 + HOD_COOLDOWN)) == 1  # 15 minutes on: fires again
    feed(b, "BOLT", [9.0], start=180 + 2 * HOD_COOLDOWN)                          # 13% off the high
    tomorrow = 86_400 + 180 + 2 * HOD_COOLDOWN
    assert b.update("BOLT", 12.0, day="2026-10-14", now=tomorrow, regular=True) == []  # a baseline, not a breakout


def test_a_high_made_between_two_readings_raises_the_bar():
    b = Breakouts()
    feed(b, "CRUX", [4.0])
    feed(b, "CRUX", [3.9], high=4.6, start=60)   # Yahoo's day high: 4.60 printed between readings
    assert feed(b, "CRUX", [4.3, 4.5], start=120) == []  # not past 4.60
    assert feed(b, "CRUX", [4.7], start=240)[0][1].startswith("CRUX made a new high of day at $4.70, after pulling back 15% from $4.60")


def test_sub_dollar_prices_keep_four_decimals():
    b = Breakouts()
    feed(b, "DYNA", [0.41], regular=False)
    assert feed(b, "DYNA", [0.4321], pre_high=0.4250)[0][1] == "DYNA broke its pre-market high of $0.4250 (now $0.4321)"


def test_the_pre_market_high_comes_from_the_scan_bars():
    bars = [{"t": int(ts("15:59", date(2026, 10, 12))), "o": 10.0, "h": 10.0, "c": 10.0, "v": 1},
            {"t": int(ts("07:00")), "o": 11.0, "h": 12.4, "c": 11.5, "v": 0},
            {"t": int(ts("08:00")), "o": 11.5, "h": None, "c": 11.8, "v": 0},
            {"t": int(ts("09:31")), "o": 13.0, "h": 13.5, "c": 13.2, "v": 9}]  # after the open: not pre-market
    assert premarket_row("ACME", bars, date(2026, 10, 13))["pre_high"] == 12.4


# --- in the engine -------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path, start="04:00")


def test_a_gapper_breaks_its_pre_market_high_after_the_open(day):
    day.headline("06:30", "Acme Rocket Corp (NASDAQ: ACME) wins launch contract")  # why the scanner looks at it
    day.premarket("07:00", ACME=(30.0, 40), pre_high={"ACME": 14.0})   # last 13.00, high 14.00
    day.movers("09:35", gainers={"ACME": 42.0})                          # 14.20: opened through it
    day.movers("09:45", gainers={"ACME": 35.0})                          # 13.50
    day.movers("09:55", gainers={"ACME": 43.0})                          # 14.30: once a day
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "09:36", "09:46", "09:56")
    try:
        assert run.engine.pre_highs["ACME"] == 14.0
        assert {"ACME": 14.0} in [m["data"] for m in run.hub.messages if m["type"] == "pre_highs"]
        alerts = [(a["kind"], a["message"]) for a in run.browser("alerts") if a["kind"] in ("pmh", "hod")]
        assert alerts == [("pmh", "ACME broke its pre-market high of $14.00 (now $14.20)")]
        assert [a["time"][11:16] for a in run.browser("alerts") if a["kind"] == "pmh"] == ["09:36"]
        # Breakouts can come often, so they reach the phone only once switched on there.
        assert run.phone == []
    finally:
        run.close()


def breakout_day(folder):
    day = Day(folder, start="09:00")
    day.quotes("09:31", BOLT=10.0)                  # 11.00
    day.quotes("09:40", BOLT=4.0)                   # 10.40: a 5.5% pullback
    day.quotes("09:50", BOLT=12.0)                  # 11.20: new high of day
    return day


def test_a_watchlist_stock_making_a_new_high_after_a_pullback_reaches_the_phone_when_breakouts_are_on(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    run = Run(breakout_day(tmp_path / "on"), watchlist=["BOLT"], phone={"breakouts": True})
    run.steps("09:32", "09:41", "09:51")
    try:
        assert [(n["title"], n["body"], n["priority"]) for n in run.phone if n["category"] == "breakouts"] == [(
            "BOLT · new high of day", "BOLT made a new high of day at $11.20, after pulling back 5% from $11.00 to $10.40",
            "high")]
    finally:
        run.close()


def test_breakout_alerts_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    run = Run(breakout_day(tmp_path / "off"), watchlist=["BOLT"], phone={"breakouts": True})
    run.engine.state.breakout_alerts = False
    run.steps("09:32", "09:41", "09:51")
    try:
        assert [n for n in run.phone if n["category"] == "breakouts"] == []
        assert [a for a in run.browser("alerts") if a["kind"] in ("pmh", "hod")] == []
    finally:
        run.close()
