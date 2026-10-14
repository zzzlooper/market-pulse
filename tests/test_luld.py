"""Estimated LULD bands and their warnings, and each alert's track record. Invented tickers and
prices only."""
import asyncio
from datetime import datetime

import pytest
from scenario_kit import DAY, Day, Run, ts

from app.luld import Bands, band, tier
from app.market import ET


def at(hhmm):
    return datetime.fromtimestamp(ts(hhmm), ET)


# --- the rules ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("ref", "tier_", "hhmm", "width"), [
    (10.0, 2, "10:00", 1.0),     # Tier 2 above $3: 10%
    (10.0, 1, "10:00", 0.5),     # Tier 1 above $3: 5%
    (2.0, 2, "10:00", 0.4),      # $0.75 to $3: 20%
    (0.5, 2, "10:00", 0.15),     # below $0.75: the lesser of $0.15 ...
    (0.1, 2, "10:00", 0.075),    # ... or 75%
    (10.0, 1, "15:40", 1.0),     # doubled in the last 25 minutes for Tier 1
    (2.0, 2, "15:40", 0.8),      # and for Tier 2 at $3 or less
    (10.0, 2, "15:40", 1.0),     # but not for Tier 2 above $3
    (2.0, 2, "09:35", 0.4),      # no doubling at the open since 2020
])
def test_band_widths(ref, tier_, hhmm, width):
    assert band(ref, tier_, at(hhmm)) == pytest.approx(width)


def test_tier_1_is_guessed_from_the_market_cap():
    assert tier(10e9) == 1 and tier(300e6) == 2 and tier(None) == 2


def test_the_reference_is_the_average_of_the_last_5_minutes_and_near_is_a_quarter_band():
    b = Bands()
    for minute, price in enumerate([10.0, 10.0, 10.0]):
        state = b.update("ACME", price, ts("10:00") + 60 * minute, 2)
    assert state["near"] is None and state["upper"] == 11.0
    state = b.update("ACME", 11.05, ts("10:03"), 2)  # ref 10.2625, upper 11.2888: 0.24 away of a 1.03 band
    assert state["near"] == "up" and state["ref"] == 10.2625 and state["away_pct"] == 2.16
    state = b.update("ACME", 9.0, ts("10:20"), 2)  # 5 minutes on: the old prices have dropped out
    assert state["ref"] == 9.0 and state["near"] is None
    falling = Bands()
    for minute, price in enumerate([10.0, 10.0, 10.0, 8.95]):  # ref 9.7375, limit-down 8.76
        state = falling.update("BOLT", price, ts("11:00") + 60 * minute, 2)
    assert state["near"] == "down" and state["lower"] == pytest.approx(8.7638, abs=1e-4)


# --- in the engine -------------------------------------------------------------------------------------

def climbing(folder):
    day = Day(folder, start="09:00")
    for hhmm in ("09:31", "09:32", "09:33"):
        day.quotes(hhmm, BOLT=0.0)
    day.quotes("09:34", BOLT=10.5)   # 11.05: within a quarter band of the 11.29 limit-up band
    day.quotes("09:35", BOLT=15.0)   # 11.50, closer still to the band, now 11.56: no second warning
    return day


def test_a_watchlist_stock_nearing_its_limit_up_band_is_warned_about_once(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    run = Run(climbing(tmp_path / "on"), watchlist=["BOLT"]).steps("09:31", "09:32", "09:33", "09:34", "09:35")
    try:
        notes = [(n["at"], n["title"], n["body"], n["category"]) for n in run.phone if "band" in n["title"]]
        assert notes == [("09:34", "BOLT · near limit-up band",
                          ("BOLT is 2.2% from its estimated limit-up band of $11.29 (reference $10.26, ±10%): it "
                           "pauses for 5 minutes there if it can't get back inside within 15 seconds"), "halts")]
        assert run.engine.luld["BOLT"]["near"] == "up"
        assert any(m["type"] == "luld" and m["data"]["BOLT"]["near"] == "up" for m in run.hub.messages)
    finally:
        run.close()


def test_band_warnings_follow_the_halt_alert_setting(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    run = Run(climbing(tmp_path / "off"), watchlist=["BOLT"])
    run.engine.state.halt_alerts = "off"
    run.steps("09:31", "09:32", "09:33", "09:34")
    try:
        assert [n for n in run.phone if "band" in n["title"]] == []
        assert run.engine.luld["BOLT"]["near"] == "up"  # the badge still shows
    finally:
        run.close()


def test_no_bands_outside_regular_hours(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="08:00")
    day.quotes("08:30", BOLT=10.0)
    run = Run(day, watchlist=["BOLT"]).steps("08:31", "08:32")
    try:
        assert run.engine.luld == {}
    finally:
        run.close()


# --- track records --------------------------------------------------------------------------------------

def history(engine, n, close):
    """n measured pre-market-high breaks from the last days: +2% in 30 minutes, `close` by the close."""
    now = int(ts("09:00"))
    for i in range(n):
        eid = f"alert:past{i}"
        engine.reactions.add({"id": eid, "symbol": "ZZZZ", "kind": "alert", "label": "broke pre-market high",
                              "title": "past", "context": "mover", "risk": None, "at": now - 86400 - 60 * i})
        engine.reactions.update(eid, {"base": 10.0, "p30m": 10.2, "pclose": 10.0 * (1 + close(i) / 100),
                                      "close_day": DAY.isoformat(), "done": 1})


def fire(run):
    e = run.engine
    asyncio.run(e._publish_alerts(e.alerts.record([e.alerts.event(
        {"symbol": "ACME", "price": 5.1}, "pmh", "ACME broke its pre-market high of $5.00 (now $5.10)")])))
    return e.alerts.recent[0]


def test_an_alert_quotes_how_its_kind_followed_through_once_there_is_enough_history(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    try:
        history(run.engine, 9, lambda i: 3.0)
        assert "track record" not in fire(run)["message"]  # 9 isn't enough to say anything
        run.engine.settings.record_min, run.engine._records = 5, None
        assert "(median of 9 in 30 days)" in fire(run)["message"]  # unless you ask for fewer
        run.engine.settings.record_min = 10
        history(run.engine, 12, lambda i: -4.0 if i < 7 else 3.0)
        run.engine._records = None  # the cache would wait 10 minutes
        alert = fire(run)
        assert alert["message"] == ("ACME broke its pre-market high of $5.00 (now $5.10) · track record: 30 min +2.0%, "
                                    "close -4.0%, 58% closed lower (median of 12 in 30 days)")
        assert alert["record"]["measured"] == 12
        tracked = [r for r in run.engine.reactions.since(0) if r["id"] == f"alert:{alert['id']}"]
        assert tracked[0]["title"] == "ACME broke its pre-market high of $5.00 (now $5.10)"  # not the record
    finally:
        run.close()
