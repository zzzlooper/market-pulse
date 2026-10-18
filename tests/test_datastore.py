import asyncio
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, UserState
from app.datastore import recordings_item, settings_item, shown_path
from app.engine import Engine
from app.market import ET
from app.reactions import ReactionStore

EMAIL = "jane.trader@mailbox.test"
TOPIC = "mp-Zq8fWm3RkT2vYp"
TOKEN = "mp-access-Kx81QnV2rT5u"


def ts(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=ET).timestamp())


def event(i: int, day: str, title: str = "Acme Rocket Corp: offering from shelf (424B5)") -> dict:
    return {"id": f"e{i}", "symbol": "ACME", "kind": "dilution", "label": "offering from shelf", "title": title,
            "context": "mover", "risk": "high", "at": ts(day)}


class Hub:
    async def broadcast(self, msg):
        pass


def make_engine(tmp_path, **settings):
    state = UserState(tmp_path / "data" / "state.json")
    return Engine(Settings(**settings), state, Hub())


def close(engine):
    async def go():
        await engine.news.aclose()
        await engine.sec.aclose()
        await engine.halts.aclose()
        await engine.phone.aclose()
        engine.reactions.close()
    asyncio.run(go())


# --- the store itself ---------------------------------------------------------------------------------

def test_deleted_history_is_really_gone_from_the_file(tmp_path):
    store = ReactionStore(tmp_path / "reactions.db")
    for i, day in enumerate(("2025-01-02", "2025-06-01", "2026-10-01")):
        store.add(event(i, day, title=f"UNIQUE-HEADLINE-{i}"))
    assert store.stats() == {"count": 3, "oldest": ts("2025-01-02"), "newest": ts("2026-10-01")}
    assert store.prune(ts("2025-10-13")) == 2
    raw = (tmp_path / "reactions.db").read_bytes()
    assert b"UNIQUE-HEADLINE-0" not in raw and b"UNIQUE-HEADLINE-1" not in raw  # compacted, not just marked free
    assert b"UNIQUE-HEADLINE-2" in raw
    assert store.clear() == 1 and b"UNIQUE-HEADLINE-2" not in (tmp_path / "reactions.db").read_bytes()
    assert store.stats()["count"] == 0
    store.close()


def test_the_watchlist_file_is_readable_by_you_only(tmp_path):
    state = UserState(tmp_path / "data" / "state.json")
    state.watchlist = ["ACME"]
    state.save()
    assert stat.S_IMODE(os.stat(state.path).st_mode) == 0o600


# --- the inventory ----------------------------------------------------------------------------------

@pytest.fixture
def open_umask(monkeypatch):
    """A umask that lets everyone read new files, and no chmod afterwards: a file is private only
    if it's created that way, with no moment of being readable by others."""
    old = os.umask(0)
    seen = []
    replace = Path.replace

    def spy(self, target):
        seen.append((Path(target).name, stat.S_IMODE(os.stat(self).st_mode)))
        return replace(self, target)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(Path, "replace", spy)
    yield seen
    os.umask(old)


def test_alert_counts_and_luld_checks_are_private_from_the_start(tmp_path, open_umask):
    from datetime import date

    from app.luld import Checks
    from app.noise import AlertCounts
    counts = AlertCounts(tmp_path / "alert_counts.json")
    counts.add("move", "2026-10-13", phoned=False)
    counts.save(date(2026, 10, 13))
    Checks(tmp_path / "luld_checks.json").add("2026-10-13", "up", 11.3, 11.29, True)
    assert open_umask == [("alert_counts.json", 0o600), ("luld_checks.json", 0o600)]  # the .tmp as written


def test_a_left_over_tmp_file_doesnt_widen_permissions(tmp_path, open_umask):
    from datetime import date

    from app.noise import AlertCounts
    (tmp_path / "alert_counts.tmp").write_text("{}")  # left by a crash, readable by all (umask 0)
    assert stat.S_IMODE(os.stat(tmp_path / "alert_counts.tmp").st_mode) == 0o666
    AlertCounts(tmp_path / "alert_counts.json").save(date(2026, 10, 13))
    assert open_umask == [("alert_counts.json", 0o600)]


def test_the_reaction_history_is_private_from_the_start_and_when_older(tmp_path, monkeypatch):
    from app.reactions import ReactionStore
    old = os.umask(0)
    try:
        monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
        store = ReactionStore(tmp_path / "new.db")  # created private, not chmod-ed after
        assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
        store.close()
        monkeypatch.undo()
        older = tmp_path / "older.db"  # a history written before files were made private
        older.touch(mode=0o644)
        ReactionStore(older).close()
        assert stat.S_IMODE(os.stat(older).st_mode) == 0o600
    finally:
        os.umask(old)


def test_saved_settings_are_listed_by_name_never_by_value(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"sec_user_agent": f"market-pulse/0.15 {EMAIL}", "ntfy_topic": TOPIC,
                                "access_token": TOKEN, "rvol_threshold": 2.5}))
    item = settings_item(path)
    text = json.dumps(item)
    assert not any(secret in text for secret in (EMAIL, TOPIC, TOKEN, "2.5"))
    assert item["detail"] == ("4 saved: Your contact email for SEC (secret), ntfy topic (secret), "
                              "Access token (secret), Unusual volume at RVOL")
    assert "other devices are signed out" in item["confirm"]
    assert settings_item(tmp_path / "missing.json")["deletable"] is False


def test_paths_are_shown_relative_never_in_full(tmp_path):
    assert shown_path(main.ROOT / "data" / "reactions.db") == "data/reactions.db"
    assert shown_path(tmp_path / "reactions.db") == "(data folder)/reactions.db"  # e.g. a replay's temp folder


def test_the_recording_being_written_is_never_offered_for_deletion(tmp_path):
    folder = tmp_path / "recordings"
    folder.mkdir()
    for name in ("2026-10-12_040000", "2026-10-13_040000"):
        (folder / f"{name}.jsonl.gz").write_bytes(b"x" * 10)
    item = recordings_item(folder, keep=10, active=folder / "2026-10-13_040000.jsonl.gz")
    assert item["detail"] == "2 recordings, 2026-10-12 to 2026-10-13; newest 10 kept; recording now"
    assert item["size"] == 20 and item["deletable"] is True and "being written now is kept" in item["confirm"]


def test_the_engine_inventory_covers_every_store_without_contents(tmp_path):
    engine = make_engine(tmp_path, reactions_keep_days=365)
    try:
        engine.state.watchlist = ["ACME", "BOLT"]
        engine.state.save()
        engine.reactions.add(event(1, "2026-10-01", title="UNIQUE-HEADLINE"))
        stores = {s["id"]: s for s in engine.data_inventory()}
        assert list(stores) == ["state", "dashboard_settings", "reactions", "recordings", "tls", "luld_checks", "alert_counts",
                                "log"]
        assert stores["state"]["detail"].startswith("2 tickers")
        assert stores["reactions"]["detail"] == "1 event, 2026-10-01 to 2026-10-01; kept 365 days"
        text = json.dumps(list(stores.values()))
        assert "UNIQUE-HEADLINE" not in text and str(tmp_path) not in text and str(main.ROOT) not in text
    finally:
        close(engine)


# --- deleting --------------------------------------------------------------------------------------------

def test_deleting_dashboard_settings_falls_back_and_takes_effect_at_once(tmp_path):
    engine = make_engine(tmp_path)
    try:
        asyncio.run(engine.update_settings({"sec_user_agent": EMAIL, "rvol_threshold": 4}))
        assert engine.settings.rvol_threshold == 4 and EMAIL in engine.sec._client.headers["User-Agent"]
        asyncio.run(engine.delete_data("dashboard_settings"))
        assert not engine.dashboard.path.exists()
        assert engine.settings.rvol_threshold == Settings().rvol_threshold
        assert EMAIL not in engine.sec._client.headers["User-Agent"]
        with pytest.raises(Exception, match="can't be deleted"):
            asyncio.run(engine.delete_data("state"))
    finally:
        close(engine)


def test_old_history_is_pruned_once_a_day_by_the_retention_setting(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, reactions_keep_days=30)
    now = [ts("2026-10-13")]
    monkeypatch.setattr("app.engine.catalysts.now_ts", lambda: now[0])
    try:
        engine.reactions.add(event(1, "2026-08-01"))
        engine.reactions.add(event(2, "2026-10-10"))
        engine._prune_reactions()
        assert engine.reactions.stats()["count"] == 1
        engine.reactions.add(event(3, "2026-08-02"))
        engine._prune_reactions()  # same day: not again
        assert engine.reactions.stats()["count"] == 2
        now[0] += 86400
        engine._prune_reactions()
        assert engine.reactions.stats()["count"] == 1
    finally:
        close(engine)


# --- over the API ------------------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "")
    yield TestClient(main.app, base_url="http://localhost")
    main.engine.dashboard.path.unlink(missing_ok=True)


def test_api_lists_and_deletes_and_refuses_other_sites(client):
    stores = client.get("/api/data")
    assert stores.status_code == 200 and stores.headers["cache-control"] == "no-store"
    assert [s["id"] for s in stores.json()["stores"]][:3] == ["state", "dashboard_settings", "reactions"]
    assert client.post("/api/data/delete", json={"store": "reactions"},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/data/delete", json={"store": "state"}).status_code == 422
    assert client.post("/api/data/delete", json={"store": "reactions"}).status_code == 200


def test_logout_deletes_the_sign_in_cookie(client, monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", TOKEN)
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 204
    assert client.get("/api/snapshot").status_code == 200
    resp = client.post("/api/logout")
    assert resp.status_code == 204 and 'mp_token=""' in resp.headers["set-cookie"]
    assert client.get("/api/snapshot").status_code == 401
