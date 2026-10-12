"""End-to-end scenarios: a made-up trading day replayed through the real engine, checked by what
reaches the browser and the phone. See scenario_kit.py; all companies are invented and no
person appears anywhere."""
import gzip

import pytest
from scenario_kit import CONTACT, PHONE_TOPIC, Day, Run


@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)  # offline: no SEC rate limit to respect
    return Day(tmp_path)


def slide(start: str, before: float, after: float):
    """Price `before` until start, then `after`."""
    return lambda hhmm: before if hhmm < start else after


def test_an_offering_on_a_top_gainer_alerts_once_with_its_risk_and_its_drop_reaches_the_recap(day):
    day.quotes("09:00", BOLT=0.5)
    day.movers("09:45", gainers={"ACME": 45.0})
    day.filing("10:02", "424B5", "ACME")
    day.profile("ACME", "high")
    day.chart("ACME", slide("10:03", 10.00, 8.60))  # falls the minute after the filing
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:46", "10:05", "10:40", "16:25")
    try:
        assert run.phone_titles() == ["ACME · offering from shelf"]
        note = run.phone[0]
        assert note["at"] == "10:05" and note["priority"] == "high"
        assert note["body"] == "Acme Rocket Corp: offering from shelf (424B5) (top gainer +45%, dilution risk HIGH)"

        item = run.news("offering from shelf")
        assert item["alert"] and item["filing"]["hot"] == "top gainer +45%" and item["filing"]["risk"] == "high"
        risk = run.engine.risk["ACME"]
        assert risk["level"] == "high" and {"ACME": risk} in run.browser("risk")  # the DIL badge

        report = run.engine.reactions_report(1)
        offering = next(g for g in report["by_type"] if g["group"] == "offering from shelf")
        assert offering["n"] == offering["measured"] == 1 and offering["median_close"] == -14.0
        assert report["offerings_by_risk"][0]["group"] == "high"
        assert report["recent"][0]["context"] == "mover"

        recap = run.recap("16:30")
        assert "  ACME offering from shelf: -14.0%" in recap["body"].splitlines()
        assert "Filings: 1 offering from shelf" in recap["body"]
    finally:
        run.close()


def test_filings_already_out_at_startup_show_but_never_alert(day):
    day.movers("09:00", gainers={"ACME": 30.0})
    day.filing("08:30", "424B5", "ACME")  # before the app started
    day.profile("ACME", "high")
    run = Run(day, watchlist=["ACME"]).steps("09:31", "09:46")
    try:
        assert run.phone == []
        item = run.news("offering from shelf")
        assert item["alert"] is False
        assert run.engine.reactions_report(1)["tracked"] == 1  # still measured
    finally:
        run.close()


def test_switching_filings_off_for_the_phone_keeps_them_in_the_browser(day):
    day.movers("09:45", gainers={"ACME": 45.0})
    day.filing("10:02", "424B5", "ACME")
    day.profile("ACME", "high")
    run = Run(day, watchlist=["BOLT"], phone={"filings": False}).steps("09:31", "09:46", "10:05")
    try:
        assert run.phone == []
        assert run.news("offering from shelf")["alert"] is True
    finally:
        run.close()


def test_a_watchlist_halt_alerts_and_resumes_but_others_and_old_ones_dont(day):
    day.halt("09:00", "ECHO", "T1", halted="08:50")             # already in the feed at startup
    day.halt("10:10", "BOLT", "LUDP")                            # watchlist
    day.halt("10:12", "FERN", "LUDP")                            # not watched
    day.halt("10:20", "BOLT", "LUDP", halted="10:10", resumes="10:15")  # BOLT gets its resumption time
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:11", "10:13", "10:21")
    try:
        assert run.phone_titles() == ["BOLT · trading halted", "BOLT · trading resumes"]
        assert [n["priority"] for n in run.phone] == ["urgent", "high"]
        assert [n["at"] for n in run.phone] == ["10:11", "10:21"]
        tracked = {e["symbol"]: e["label"] for e in run.engine.reactions_report(1)["recent"]}
        assert tracked == {"BOLT": "volatility pause", "FERN": "volatility pause"}  # new halts only
    finally:
        run.close()


def test_a_price_rule_fires_when_crossed_not_while_it_holds_and_respects_the_cooldown(day):
    for hhmm, pct in (("09:00", 3.0), ("09:45", 6.0), ("10:00", 7.0), ("10:05", 2.0), ("10:10", 6.0),
                      ("10:15", 2.0), ("10:20", 6.5)):
        day.quotes(hhmm, BOLT=pct)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:46", "10:01", "10:06", "10:11", "10:16", "10:21")
    try:
        # 09:31 is the baseline; 10:11 crosses again inside the 30-minute cooldown.
        assert [n["at"] for n in run.phone if n["category"] == "price"] == ["09:46", "10:21"]
        assert run.alert_log()[0].startswith("BOLT")
    finally:
        run.close()


def test_a_delisting_notice_on_a_mover_alerts_but_routine_8ks_and_quiet_insider_buys_dont(day):
    day.movers("09:00", small_caps={"CRUX": 22.0, "DYNA": 15.0})
    day.filing("10:00", "8-K", "CRUX", items=[("3.01", "Notice of Delisting or Failure to Satisfy a Listing Rule")])
    day.filing("10:00", "8-K", "DYNA", items=[("2.02", "Results of Operations and Financial Condition")])
    day.insider_trade("10:00", "FERN", "P", shares=40_000, price=2.50, owned_after=140_000)   # quiet stock
    day.insider_trade("10:00", "ECHO", "S", shares=90_000, price=5.00, owned_after=10_000)    # sale, not watched
    day.profile("CRUX", "low")
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:01")
    try:
        assert run.phone_titles() == ["CRUX · delisting notice"]
        assert run.phone[0]["body"].startswith("Crux Biotherapeutics Inc: delisting notice (small-cap gainer +22%")
        titles = [n["title"] for n in run.browser("news")]
        assert "Fern Foods Inc: CEO bought 40,000 shares at $2.50 ($100K)" in titles
        assert not any(t.startswith("Echo Mining") for t in titles)  # sales: watchlist only
        assert run.news("Dyna Robotics")["alert"] is False
    finally:
        run.close()


def test_a_feed_down_five_minutes_sends_one_outage_note_and_one_recovery(day):
    day.headline("09:10", "Bolt Battery Inc (NASDAQ: BOLT) opens a new plant")
    day.wire_down("10:00", "10:12")
    times = ["09:31", "09:40", *(f"10:{m:02d}" for m in range(12)), "10:13", "10:14"]
    run = Run(day, watchlist=["BOLT"]).steps(*times)
    try:
        assert run.phone_titles("sources") == ["Market Pulse · News feeds down", "Market Pulse · News feeds back"]
        down, back = [n for n in run.phone if n["category"] == "sources"]
        assert down["at"] == "10:05" and back["at"] == "10:13"
        assert run.phone_titles("news") == []  # the 09:10 headline was already out at startup
    finally:
        run.close()


def test_a_recording_never_contains_the_contact_address_or_phone_topic(day):
    day.movers("09:45", gainers={"ACME": 45.0})
    day.filing("10:02", "424B5", "ACME")
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:05")
    try:
        with gzip.open(run.recording, "rt") as f:
            raw = f.read()
        assert CONTACT not in raw and PHONE_TOPIC not in raw
        assert all(PHONE_TOPIC not in str(n) for n in run.phone)  # notes carry no delivery details
    finally:
        run.close()
