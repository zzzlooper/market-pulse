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
