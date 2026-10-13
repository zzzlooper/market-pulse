"""What happened after the app's own alerts: measured like catalysts, reported apart from them.
Invented tickers and prices."""
from scenario_kit import Day, Run


def price(hhmm):
    """BOLT: 11.00, a pullback to 10.40, a new high at 11.20 at 09:50, then on to 12.00 by 10:00."""
    if hhmm < "09:40":
        return 11.0
    if hhmm < "09:50":
        return 10.4
    return 11.2 if hhmm < "09:55" else 12.0


def test_a_breakout_alert_is_measured_and_reported_with_the_alerts_not_the_catalysts(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="09:00")
    day.quotes("09:31", BOLT=10.0)
    day.quotes("09:40", BOLT=4.0)
    day.quotes("09:50", BOLT=12.0)
    day.halt("09:45", "CRUX", "T1")  # a catalyst, for comparison
    day.chart("BOLT", price)
    day.chart("CRUX", lambda hhmm: 5.0)
    run = Run(day, watchlist=["BOLT"]).steps("09:32", "09:41", "09:46", "09:51", "10:20", "10:45")
    try:
        report = run.engine.reactions_report(1)
        alerts = {g["group"]: g for g in report["alerts"]}
        hod = alerts["new high of day after a pullback"]
        assert hod["n"] == 1 and hod["median_5m"] == hod["median_30m"] == 7.14  # 11.20 -> 12.00
        assert "your rules: move ±%" in alerts  # the default ±5% rule fired too
        assert [g["group"] for g in report["by_type"]] == ["news halt"]  # catalysts only
        assert report["catalysts"] == 1 and report["tracked"] == 1 + sum(g["n"] for g in report["alerts"])
        recent = next(r for r in report["recent"] if r["kind"] == "alert" and r["label"].startswith("new high"))
        assert recent["title"].startswith("BOLT made a new high of day at $11.20")
        assert ",BOLT,alert,new high of day after a pullback," in run.engine.reactions_csv(1)
        recap = run.recap("16:30")["body"]
        assert "Filings:" not in recap  # alerts aren't counted as filings
        assert any(line.startswith("Alerts today: ") and "1 new high of day after a pullback" in line
                   for line in recap.splitlines())
    finally:
        run.close()
