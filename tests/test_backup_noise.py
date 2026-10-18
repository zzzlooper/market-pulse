"""Backup and restore (never a secret), and alert noise control. Invented tickers only."""
import asyncio
import json

import pytest
from scenario_kit import Day, Run

from app.backup import BackupError, read


def test_a_backup_holds_no_secret_and_not_this_computers_address(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"], ntfy_topic="mp-secret-topic-123456", access_token="t" * 24,
              public_url="http://192.168.1.20:8000", day2_min_pct=40.0)
    try:
        backup = run.engine.backup()
        text = json.dumps(backup)
        for secret in ("mp-secret-topic-123456", "t" * 24, "192.168.1.20", "ops@example.org"):
            assert secret not in text
        assert backup["settings"] == {"day2_min_pct": 40.0}  # only what differs from the defaults
        assert backup["state"]["watchlist"] == ["BOLT"]
    finally:
        run.close()


def test_restoring_checks_everything_and_leaves_secrets_out(tmp_path):
    good = {"market_pulse_backup": 1, "state": {"watchlist": ["acme", "BOLT"],
                                                "alert_rules": [{"symbol": "ACME", "kind": "above", "value": 5}]},
            "settings": {"day2_min_pct": 25, "ntfy_topic": "mp-should-not-come-back"}}
    state, settings, notes = read(good)
    assert state["watchlist"] == ["ACME", "BOLT"] and settings == {"day2_min_pct": 25.0}
    assert any("never restored" in n for n in notes)
    for bad in ({"state": {}}, {"market_pulse_backup": 1, "state": {"watchlist": ["no spaces allowed"]}},
                {"market_pulse_backup": 1, "state": {"alert_rules": [{"symbol": "ZZZZ", "kind": "above", "value": 1}]}},
                {"market_pulse_backup": 1, "settings": {"day2_min_pct": 1}}):
        with pytest.raises(BackupError):
            read(bad)
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    try:
        plan = asyncio.run(run.engine.restore(good))
        assert {c["what"] for c in plan["changes"]} >= {"Watchlist", "Alert rules"}
        assert run.engine.state.watchlist == ["ACME", "BOLT"] and run.engine.settings.day2_min_pct == 25.0
    finally:
        run.close()


def test_mute_snooze_and_the_phone_cap(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"], phone_cap_per_kind=1)
    e = run.engine

    def fire(symbol):
        asyncio.run(e._publish_alerts([e.alerts.event({"symbol": symbol, "price": 5.0}, "pmh", f"{symbol} broke")]))
    try:
        e.state.phone_alerts["breakouts"] = True
        fire("ACME")
        fire("CRUX")  # the cap lets one through to the phone
        assert e.phone._queue.qsize() == 1 and len(e.alerts.recent) == 2
        asyncio.run(e.set_alert_mute("pmh", browser=False, phone=None))
        fire("DYNA")
        assert [a["symbol"] for a in e.alerts.recent] == ["CRUX", "ACME"]  # muted here
        asyncio.run(e.set_alert_mute("pmh", browser=True, phone=None))
        asyncio.run(e.snooze("ECHO"))
        fire("ECHO")
        assert "ECHO" not in [a["symbol"] for a in e.alerts.recent]
        kinds = {k["kind"]: k for k in e.alert_kinds()}
        assert kinds["pmh"]["today"] == 4 and kinds["pmh"]["phoned_today"] == 1
        assert "ACME" not in e.alert_counts.path.read_text()  # counts hold kinds, never tickers
    finally:
        run.close()


def test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap(tmp_path):
    day = Day(tmp_path)
    day.headline("09:40", "Acme Rocket Corp (NASDAQ: ACME) wins launch contract")
    day.headline("09:41", "Bolt Battery Inc (NASDAQ: BOLT) opens a plant")
    day.headline("09:45", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports topline data")
    day.headline("09:50", "Dyna Robotics Corp (NASDAQ: DYNA) names a new chief")
    day.filing("09:55", "424B5", "ECHO")
    day.filing("09:58", "424B5", "ECHO")
    day.profile("ECHO", "high")
    run = Run(day, watchlist=["ACME", "BOLT", "CRUX", "DYNA", "ECHO"], phone_cap_per_kind=1)
    e = run.engine
    try:
        run.steps("09:31")  # every feed's first read is the baseline
        asyncio.run(e.snooze("BOLT"))
        run.steps("09:42", "09:46")
        assert run.news("Acme")["alert"] and not run.news("Bolt")["alert"]  # snoozed: in the news, no alert
        assert run.news("Crux")["alert"]  # past the phone cap it still shows here
        assert run.phone_titles("news") == ["ACME · Wire"]
        asyncio.run(e.set_alert_mute("news", browser=False, phone=None))
        asyncio.run(e.set_alert_mute("filing", browser=None, phone=False))
        run.steps("09:51", "09:56")
        assert not run.news("Dyna")["alert"]
        assert run.news("Echo Mining")["alert"] and run.phone_titles("filings") == []
        kinds = {k["kind"]: k for k in e.alert_kinds()}
        assert (kinds["news"]["today"], kinds["news"]["phoned_today"]) == (4, 1)
        assert (kinds["filing"]["today"], kinds["filing"]["phoned_today"]) == (1, 0)
        tracked = {r["symbol"] for r in e.reactions.since(0) if r["kind"] == "headline"}
        assert tracked == {"ACME", "BOLT", "CRUX", "DYNA"}  # muted and snoozed ones are still measured
        assert [(a["symbol"], a["kind"]) for a in e.alerts.recent] == [("ECHO", "filing")]  # headlines stay in the news
        asyncio.run(e.set_alert_mute("filing", browser=False, phone=None))
        run.steps("09:59")
        assert {k["kind"]: k for k in e.alert_kinds()}["filing"]["today"] == 2  # it did alert…
        assert len(e.alerts.recent) == 1  # …but muted on this page: not in the log either
    finally:
        run.close()


def test_a_kind_muted_everywhere_is_still_counted_and_measured(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    e = run.engine
    try:
        e.state.phone_alerts["breakouts"] = True
        asyncio.run(e.set_alert_mute("hod", browser=False, phone=False))
        asyncio.run(e._publish_alerts([e.alerts.event({"symbol": "ACME", "price": 5.0}, "hod", "ACME new high")]))
        assert list(e.alerts.recent) == [] and e.phone._queue.qsize() == 0
        assert not [m for m in run.hub.messages if m["type"] == "alerts"]
        assert {k["kind"]: k for k in e.alert_kinds()}["hod"]["today"] == 1
        assert [r["label"] for r in e.reactions.since(0)] == ["new high of day after a pullback"]  # still measured
    finally:
        run.close()


def test_an_alert_whose_phone_category_is_off_doesnt_count_against_the_cap(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"], phone_cap_per_kind=1)
    e = run.engine
    try:
        def fire(symbol):
            asyncio.run(e._publish_alerts([e.alerts.event({"symbol": symbol, "price": 5.0}, "pmh", f"{symbol} broke")]))
        fire("ACME")                              # Breakouts are off on the phone by default
        e.state.phone_alerts["breakouts"] = True
        fire("CRUX")
        assert e.phone._queue.qsize() == 1 and e.alert_counts.phoned("pmh", e.alerts._clock().date().isoformat()) == 1
    finally:
        run.close()


def test_the_counts_file_is_yours_only_drops_old_days_and_can_be_deleted(tmp_path):
    from datetime import date

    from app.noise import AlertCounts
    counts = AlertCounts(tmp_path / "alert_counts.json")
    counts.add("pmh", "2026-09-01", phoned=True)   # long ago
    counts.add("pmh", "2026-10-07", phoned=False)  # 6 days ago: in the week
    counts.add("pmh", "2026-10-13", phoned=True)
    counts.save(date(2026, 10, 13))
    assert oct(counts.path.stat().st_mode & 0o777) == "0o600"
    assert sorted(json.loads(counts.path.read_text())) == ["2026-10-07", "2026-10-13"]
    assert AlertCounts(counts.path).summary(date(2026, 10, 13)) == {"pmh": {"today": 1, "week": 2, "phoned_today": 1}}
    assert counts.clear() == 2 and not counts.path.exists()


def test_deleting_the_counts_from_your_data(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    e = run.engine
    try:
        asyncio.run(e._publish_alerts([e.alerts.event({"symbol": "ACME", "price": 5.0}, "pmh", "ACME broke")]))
        assert e.alert_counts.path.exists()
        asyncio.run(e.delete_data("alert_counts"))
        assert not e.alert_counts.path.exists() and {k["kind"]: k for k in e.alert_kinds()}["pmh"]["today"] == 0
    finally:
        run.close()


def test_a_preview_changes_nothing(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    try:
        plan = run.engine.restore_preview({"market_pulse_backup": 1, "state": {"watchlist": ["ACME"]}})
        assert plan["changes"] == [{"what": "Watchlist", "now": ["BOLT"], "then": ["ACME"]}]
        assert run.engine.state.watchlist == ["BOLT"]
    finally:
        run.close()


def test_a_snooze_lasts_until_the_end_of_the_day(tmp_path):
    run = Run(Day(tmp_path), watchlist=["BOLT"])
    e = run.engine
    try:
        e.state.snoozed = {"ACME": "2026-10-12"}  # snoozed yesterday
        asyncio.run(e._publish_alerts([e.alerts.event({"symbol": "ACME", "price": 5.0}, "pmh", "ACME broke")]))
        assert [a["symbol"] for a in e.alerts.recent] == ["ACME"]  # a new day: back
    finally:
        run.close()
