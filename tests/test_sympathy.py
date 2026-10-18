"""Sympathy moves within an industry, and the signal thresholds you can tune in Setup.
Invented tickers and prices only."""
import asyncio

import pytest
from scenario_kit import Day, Run, ts

from app.breakouts import Breakouts
from app.engine.tape import day2_move
from app.luld import Bands
from app.setup import SettingsError, validate


@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path)


def theme(day):
    """ACME leads Biotechnology (+45%); CRUX follows (+8%), DYNA barely (+3%); BOLT is elsewhere."""
    day.floats("09:00", ACME=None, CRUX=None, DYNA=None, BOLT=None,
               industry={"ACME": "Biotechnology", "CRUX": "Biotechnology", "DYNA": "Biotechnology",
                         "BOLT": "Electrical Equipment & Parts"})
    day.movers("09:30", gainers={"ACME": 45.0, "CRUX": 8.0, "DYNA": 3.0})
    day.quotes("09:30", BOLT=9.0)


# --- sympathy ------------------------------------------------------------------------------------------

def test_a_stock_moving_with_its_industrys_leader_is_marked_and_alerted_once(day):
    theme(day)
    day.movers("09:31", gainers={"ACME": 45.0, "CRUX": 2.0, "DYNA": 3.0})  # CRUX starts flat...
    day.movers("09:33", gainers={"ACME": 45.0, "CRUX": 8.0, "DYNA": 3.0})  # ...then joins in
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32", "09:33", "09:34")
    try:
        assert run.engine.sympathy == {"CRUX": {"industry": "Biotechnology", "leader": "ACME", "leader_pct": 45.0,
                                                "change_pct": 8.0}}
        alerts = [a["message"] for a in run.browser("alerts") if a["kind"] == "sympathy"]
        assert alerts == ["CRUX +8.0% in sympathy with ACME +45% (Biotechnology)"]
        assert any(m["type"] == "sympathy" and "CRUX" in m["data"] for m in run.hub.messages)
        assert [n for n in run.phone if n["category"] == "breakouts"] == []  # the phone's Breakouts are off by default
    finally:
        run.close()


def test_a_stock_already_moving_in_sympathy_at_startup_is_only_a_baseline(day):
    theme(day)  # CRUX is already up 8% with ACME when the app starts
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32", "09:33")
    try:
        assert "CRUX" in run.engine.sympathy  # the badge shows
        assert [a for a in run.browser("alerts") if a["kind"] == "sympathy"] == []
    finally:
        run.close()


def test_a_stock_already_moving_when_its_leaders_industry_arrives_is_only_a_baseline(day):
    theme(day)
    day.floats("09:00", ACME=None, CRUX=None, DYNA=None, BOLT=None,  # ACME's industry isn't known yet
               industry={"CRUX": "Biotechnology", "DYNA": "Biotechnology", "BOLT": "Electrical Equipment & Parts"})
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32")
    try:
        assert run.engine.sympathy == {}
        run.engine.industries["ACME"] = "Biotechnology"  # CRUX was up 8% all along
        run.steps("09:33", "09:34")
        assert "CRUX" in run.engine.sympathy  # the badge shows
        assert [a for a in run.browser("alerts") if a["kind"] == "sympathy"] == []
    finally:
        run.close()


def test_gappers_on_no_list_move_in_sympathy_before_and_after_the_open(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="04:00")
    day.headline("06:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports topline data")  # why the scanner looks
    day.headline("06:31", "Dyna Robotics Corp (NASDAQ: DYNA) names a partner")
    day.premarket("07:00", CRUX=(40.0, 30), DYNA=(4.0, 20))   # DYNA gaps, but under the 5% sympathy bar
    day.floats("07:00", CRUX=None, DYNA=None, industry={"CRUX": "Biotechnology", "DYNA": "Biotechnology"})
    day.quotes("09:35", CRUX=41.0, DYNA=8.0)                   # after the open DYNA follows: one alert
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "07:02")
    try:
        assert not any(run.engine.movers.values())
        assert run.engine.sympathy == {}  # pre-market: CRUX leads, DYNA (+4%) not yet with it
        plan = {e["symbol"]: e for e in run.engine.game_plan()["entries"]}
        assert plan["CRUX"]["peers"] == {"industry": "Biotechnology", "leader": None, "followers": []}
        run.steps("09:36")
        assert run.engine.sympathy["DYNA"] == {"industry": "Biotechnology", "leader": "CRUX", "leader_pct": 41.0,
                                               "change_pct": 8.0}
        assert [a["message"] for a in run.browser("alerts") if a["kind"] == "sympathy"] == [
            "DYNA +8.0% in sympathy with CRUX +41% (Biotechnology)"]
    finally:
        run.close()


def test_a_pre_market_gapper_moving_with_its_industrys_leader_shows_in_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="04:00")
    day.headline("06:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports topline data")
    day.headline("06:31", "Dyna Robotics Corp (NASDAQ: DYNA) names a partner")
    day.premarket("07:00", CRUX=(40.0, 30), DYNA=(12.0, 20))
    day.floats("07:00", CRUX=None, DYNA=None, industry={"CRUX": "Biotechnology", "DYNA": "Biotechnology"})
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "07:02")
    try:
        assert run.engine.sympathy["DYNA"]["leader"] == "CRUX"
        plan = {e["symbol"]: e for e in run.engine.game_plan()["entries"]}
        assert plan["CRUX"]["peers"]["followers"] == [{"symbol": "DYNA", "change_pct": 12.0}]
        assert plan["DYNA"]["peers"]["leader"] == {"symbol": "CRUX", "change_pct": 40.0}
        assert [a for a in run.browser("alerts") if a["kind"] == "sympathy"] == []  # moving at its first check
    finally:
        run.close()


def test_the_sympathy_thresholds_are_yours_to_set(day):
    theme(day)
    run = Run(day, watchlist=["BOLT"], sympathy_min_pct=2.0, sympathy_lead_pct=50.0)
    run.steps("09:31", "09:32")
    try:
        assert run.engine.sympathy == {}  # ACME's +45% doesn't lead at 50
        run.engine.settings.sympathy_lead_pct = 40.0
        run.steps("09:33")
        assert sorted(run.engine.sympathy) == ["CRUX", "DYNA"]  # +3% counts at 2
    finally:
        run.close()


def test_with_breakout_alerts_off_the_badge_stays_and_the_alert_doesnt_come(day):
    theme(day)
    run = Run(day, watchlist=["BOLT"])
    run.engine.state.breakout_alerts = False
    run.steps("09:31", "09:32")
    try:
        assert "CRUX" in run.engine.sympathy
        assert [a for a in run.browser("alerts") if a["kind"] == "sympathy"] == []
    finally:
        run.close()


def test_the_panel_lists_the_industrys_movers_from_yahoos_screen(day):
    theme(day)
    day.industry("09:00", "Biotechnology", ACME=45.0, CRUX=8.0, ECHO=6.5)
    run = Run(day, watchlist=["BOLT"]).steps("09:31")
    try:
        found = asyncio.run(run.engine.industry_movers("CRUX"))
        assert found["industry"] == "Biotechnology"
        assert [(m["symbol"], m["change_pct"]) for m in found["movers"]] == [("ACME", 45.0), ("CRUX", 8.0), ("ECHO", 6.5)]
        assert asyncio.run(run.engine.industry_movers("FERN")) == {"industry": None, "movers": []}  # nothing known
    finally:
        run.close()


# --- thresholds ----------------------------------------------------------------------------------------

def test_thresholds_are_checked_before_they_are_saved():
    assert validate("breakout_pullback_pct", "3") == 3.0 and validate("record_min", "25") == 25
    for name, value in (("breakout_pullback_pct", 0.5), ("day2_min_pct", 4), ("luld_near_pct", 101),
                        ("record_min", 2), ("rotation_multiple", "lots")):
        with pytest.raises(SettingsError):
            validate(name, value)


def test_the_rules_take_their_threshold():
    b = Breakouts()
    for i, p in enumerate([10.0, 9.7, 10.1]):  # a 3% pullback, then a new high
        found = b.update("BOLT", p, day="2026-10-13", now=60 * i, regular=True, min_pullback=0.02)
    assert found and found[0][0] == "hod"
    days = [{"date": "2026-10-09", "low": 9, "close": 10.0}, {"date": "2026-10-12", "low": 10, "close": 11.5}]
    assert day2_move(days, "2026-10-13") is None and day2_move(days, "2026-10-13", 0.10)["change_pct"] == 15.0
    bands = Bands()
    for i, p in enumerate([10.0, 10.0, 10.0, 10.8]):
        state = bands.update("ACME", p, ts("10:00") + 60 * i, 2, near_share=0.5)
    assert state["near"] == "up"  # 0.4 from an 11.2 band is within half of its 1.02 width


def test_float_rotation_at_your_multiple(day):
    day.floats("09:00", BOLT=4_000_000)
    day.quotes("09:30", BOLT=2.0, volume={"BOLT": 5_000_000})
    day.quotes("09:45", BOLT=2.0, volume={"BOLT": 8_400_000})
    run = Run(day, watchlist=["BOLT"], rotation_multiple=2.0).steps("09:31", "09:32", "09:46")
    try:
        assert [a["message"] for a in run.browser("alerts") if a["kind"] == "rotation"] == [
            "BOLT has traded 2× its float: 8.4M shares today, 2.1× the 4M-share float"]
    finally:
        run.close()


def test_changed_thresholds_reach_the_browsers(day):
    run = Run(day, watchlist=["BOLT"])
    try:
        assert run.engine.snapshot()["thresholds"]["rotation_multiple"] == 1.0
        asyncio.run(run.engine.update_settings({"rotation_multiple": "1.5", "rvol_threshold": "3"}))
        sent = [m["data"] for m in run.hub.messages if m["type"] == "thresholds"]
        assert sent[-1] == {"rvol_threshold": 3.0, "rotation_multiple": 1.5, "sympathy_min_pct": 5.0,
                            "sympathy_lead_pct": 30.0}
    finally:
        run.close()


def test_the_engine_uses_your_breakout_day2_and_luld_thresholds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="09:00")
    day.quotes("09:31", BOLT=10.0)   # 11.00
    day.quotes("09:40", BOLT=7.0)    # 10.70: a 2.7% pullback
    day.quotes("09:50", BOLT=12.0)   # 11.20: a new high
    day.movers("09:30", gainers={"ACME": 5.0})
    day.daily("09:00", ACME=[("2026-10-09", 9.0, 10.0), ("2026-10-12", 10.0, 11.5)])  # +15% yesterday
    run = Run(day, watchlist=["BOLT"], breakout_pullback_pct=2.0, day2_min_pct=10.0, luld_near_pct=100.0)
    run.steps("09:32", "09:41", "09:51")
    try:
        assert [a["kind"] for a in run.browser("alerts") if a["kind"] == "hod"] == ["hod"]  # 2.7% counts at 2
        assert run.engine.day2["ACME"]["change_pct"] == 15.0                                  # +15% counts at 10
        assert run.engine.luld["BOLT"]["near"] == "up"  # anywhere within the band counts at 100%
    finally:
        run.close()


def test_the_float_lookup_brings_the_industry(monkeypatch):
    from app import market

    class Ticker:
        def __init__(self, symbol):
            self.info = {"floatShares": 2_000_000, "marketCap": 30_000_000, "industry": "Biotechnology"}
    monkeypatch.setattr(market.yf, "Ticker", Ticker)
    assert market.fetch_floats(["ACME"]) == [{"symbol": "ACME", "float_shares": 2_000_000, "market_cap": 30_000_000,
                                              "industry": "Biotechnology"}]
