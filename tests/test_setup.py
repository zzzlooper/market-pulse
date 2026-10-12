import dataclasses
import json
import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.setup import (
    VERSION,
    DashboardSettings,
    SettingsError,
    checklist,
    masked,
    validate,
)

EMAIL = "jane.trader@mailbox.test"
TOPIC = "mp-Zq8fWm3RkT2vYp"
TG_TOKEN = "123456789:AAH4kSecretBotTokenValue0123456789xyz"
TOKEN = "mp-access-Kx81QnV2rT5u"


@pytest.fixture
def client(monkeypatch):
    """A client on localhost, with the shared engine's settings restored afterwards."""
    engine = main.engine
    saved = dataclasses.replace(engine.settings)
    monkeypatch.setattr(engine.settings, "access_token", "")
    yield TestClient(main.app, base_url="http://localhost")
    for f in dataclasses.fields(Settings):
        setattr(engine.settings, f.name, getattr(saved, f.name))
    engine.sec.set_user_agent(saved.sec_user_agent)
    engine.dashboard.path.unlink(missing_ok=True)


def everything(resp) -> str:
    return resp.text + json.dumps(dict(resp.headers))


# --- validation and masking -----------------------------------------------------------------

def test_the_email_becomes_the_sec_user_agent_and_placeholders_are_refused():
    assert validate("sec_user_agent", f" {EMAIL} ") == f"market-pulse/{VERSION} {EMAIL}"
    with pytest.raises(SettingsError, match="real address"):
        validate("sec_user_agent", "you@example.com")


@pytest.mark.parametrize(("name", "value"), [
    ("sec_user_agent", "not-an-email jane.trader"), ("ntfy_topic", "short"), ("telegram_bot_token", "abc"),
    ("telegram_chat_id", "chat-me"), ("access_token", "too short"), ("ntfy_server", "ftp://x"),
    ("recap_time", "4pm"), ("rvol_threshold", "lots"), ("rvol_threshold", 0.5), ("feeds", "x"),
])
def test_invalid_values_are_refused_without_repeating_them(name, value):
    with pytest.raises(SettingsError) as err:
        validate(name, value)
    assert str(value) not in str(err.value)


def test_masks_tell_values_apart_without_revealing_them():
    assert masked("sec_user_agent", f"market-pulse/{VERSION} {EMAIL}") == "j…@….test"
    assert masked("sec_user_agent", "market-pulse/0.1 admin@example.com") == "placeholder"
    assert masked("telegram_bot_token", TG_TOKEN) == "…xyz"
    assert masked("telegram_chat_id", "987654321") == "set"  # too short to show any of it


def test_dashboard_file_is_private_and_layers_below_the_environment(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('ntfy_server = "https://ntfy.example.test"\nrvol_threshold = 3.0\n')
    store = DashboardSettings(tmp_path / "data" / "settings.json", config)
    store.save({"rvol_threshold": 4.0, "ntfy_topic": TOPIC, "bogus": 1})
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.load() == {"rvol_threshold": 4.0, "ntfy_topic": TOPIC}
    monkeypatch.setenv("NTFY_TOPIC", "mp-from-the-environment")
    settings = store.effective(store.load())
    assert settings.rvol_threshold == 4.0 and settings.ntfy_topic == "mp-from-the-environment"
    sources = {f["name"]: f["source"] for f in store.describe(settings)}
    assert sources["rvol_threshold"] == "dashboard" and sources["ntfy_topic"] == "environment"
    assert sources["ntfy_server"] == "config.toml" and sources["min_volume"] == "default"


# --- the API -----------------------------------------------------------------------------------

def test_secrets_go_in_but_never_come_back_out(client, caplog):
    caplog.set_level(logging.INFO)
    resp = client.put("/api/settings", json={"values": {
        "sec_user_agent": EMAIL, "ntfy_topic": TOPIC, "telegram_bot_token": TG_TOKEN,
        "telegram_chat_id": "987654321", "rvol_threshold": 2.5}})
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    later = client.get("/api/settings")
    for r in (resp, later):
        text = everything(r)
        assert not any(s in text for s in (EMAIL, TOPIC, TG_TOKEN, "987654321"))
    fields = {f["name"]: f for f in later.json()["fields"]}
    assert fields["ntfy_topic"]["set"] and fields["ntfy_topic"]["source"] == "dashboard"
    assert fields["rvol_threshold"]["value"] == 2.5  # plain settings are shown as they are
    assert not any(s in caplog.text for s in (EMAIL, TOPIC, TG_TOKEN, "987654321"))
    assert "settings changed from the dashboard" in caplog.text

    # Applied at once: the phone uses the new channel, SEC requests the new contact.
    engine = main.engine
    assert engine.phone.channels == ["ntfy", "telegram"]
    assert engine.sec._client.headers["User-Agent"] == f"market-pulse/{VERSION} {EMAIL}"
    assert engine.settings.rvol_threshold == 2.5
    assert stat.S_IMODE(os.stat(engine.dashboard.path).st_mode) == 0o600
    # Phone status masks the topic and chat id too.
    assert TOPIC not in json.dumps(engine.phone.status()) and "987654321" not in json.dumps(engine.phone.status())


def test_clearing_a_dashboard_value_falls_back(client):
    client.put("/api/settings", json={"values": {"rvol_threshold": 7}})
    assert main.engine.settings.rvol_threshold == 7
    client.put("/api/settings", json={"values": {"rvol_threshold": None}})
    assert main.engine.settings.rvol_threshold == Settings().rvol_threshold


def test_bad_values_and_environment_settings_are_refused(client, monkeypatch):
    bad = client.put("/api/settings", json={"values": {"telegram_bot_token": "oops-my-password"}})
    assert bad.status_code == 422 and "oops-my-password" not in bad.text
    monkeypatch.setenv("FINNHUB_API_KEY", "fromenv12345")
    locked = client.put("/api/settings", json={"values": {"finnhub_api_key": "abcdef123456"}})
    assert locked.status_code == 422 and "FINNHUB_API_KEY" in locked.json()["detail"]
    assert "fromenv12345" not in locked.text
    unknown = client.put("/api/settings", json={"values": {"feeds": "x"}})
    assert unknown.status_code == 422


def test_a_restart_is_flagged_for_settings_read_at_startup(client):
    resp = client.put("/api/settings", json={"values": {"finnhub_api_key": "abcdef123456"}}).json()
    assert resp["restart_required"] == ["finnhub_api_key"]


def test_generated_values_are_shown_once_and_the_token_signs_this_browser_in(client):
    topic = client.post("/api/settings/generate", json={"name": "ntfy_topic"}).json()
    assert topic["value"].startswith("mp-") and len(topic["value"]) >= 15
    assert topic["value"] not in client.get("/api/settings").text

    resp = client.post("/api/settings/generate", json={"name": "access_token"})
    token = resp.json()["value"]
    assert resp.cookies.get("mp_token") == token and "httponly" in resp.headers["set-cookie"].lower()
    assert client.get("/api/settings").status_code == 200  # this browser kept the cookie
    stranger = TestClient(main.app, base_url="http://localhost")
    assert stranger.get("/api/settings").status_code == 401
    assert client.post("/api/settings/generate", json={"name": "telegram_bot_token"}).status_code == 422


def test_without_a_token_only_requests_addressed_to_localhost_are_answered(client, monkeypatch):
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/api/snapshot", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/snapshot", headers={"host": "[::1]:8000"}).status_code == 200
    # A website rebinding its name to 127.0.0.1 still sends its own name.
    rebind = client.get("/", headers={"host": "evil.example:8000"})
    assert rebind.status_code == 403 and "access token" in rebind.text
    assert client.get("/api/snapshot", headers={"host": "192.168.1.20:8000"}).status_code == 403
    monkeypatch.setattr(main.engine.settings, "access_token", TOKEN)
    lan = client.get("/api/snapshot", headers={"host": "192.168.1.20:8000", "authorization": f"Bearer {TOKEN}"})
    assert lan.status_code == 200


def test_changes_from_other_websites_are_refused(client):
    evil = client.put("/api/settings", json={"values": {"ntfy_topic": TOPIC}},
                      headers={"origin": "http://evil.example"})
    assert evil.status_code == 403 and not main.engine.settings.ntfy_topic
    assert client.post("/api/watchlist", json={"symbol": "EVIL"},
                       headers={"origin": "http://evil.example"}).status_code == 403
    same = client.put("/api/settings", json={"values": {"rvol_threshold": 3}}, headers={"origin": "http://localhost"})
    assert same.status_code == 200


def test_setup_checklist_over_the_api(client, monkeypatch):
    monkeypatch.delenv("MARKET_PULSE_SERVICE", raising=False)
    items = {i["id"]: i for i in client.get("/api/setup").json()["items"]}
    assert items["sec"]["state"] == "todo" and items["sec"]["fix"] == {"field": "sec_user_agent"}
    assert items["service"]["fix"] == {"command": "sh scripts/install-service.sh --enable"}
    client.put("/api/settings", json={"values": {"sec_user_agent": EMAIL}})
    after = client.get("/api/setup")
    assert {i["id"]: i for i in after.json()["items"]}["sec"]["state"] == "ok"
    assert EMAIL not in after.text


# --- the checklist ------------------------------------------------------------------------------

def items(**kw):
    base = dict(channels=[], phone_error=None, health=[], watchlist=["AAPL"], default_watchlist=["SPY"],
                recap_on=True, local_client=True, local_server=True, https=False, under_systemd=False)
    return {i["id"]: i for i in checklist(Settings(**kw.pop("settings", {})), **{**base, **kw})}


def test_checklist_flags_an_exposed_dashboard_without_a_token():
    assert items(local_client=False)["access"]["state"] == "warn"
    assert items(local_server=False)["access"]["state"] == "warn"
    assert items()["access"]["state"] == "info"
    lan = items(local_client=False, settings={"access_token": TOKEN})["access"]
    assert lan["state"] == "info" and "plain HTTP" in lan["detail"]


def test_checklist_covers_phone_service_data_and_extras():
    i = items(health=[{"label": "SEC filings", "state": "down"}], watchlist=["SPY"])
    assert i["phone"]["state"] == "todo" and i["service"]["state"] == "todo"
    assert i["data"]["state"] == "warn" and "SEC filings" in i["data"]["detail"]
    assert i["watchlist"]["state"] == "info" and i["finnhub"]["state"] == "info"
    ok = items(channels=["ntfy"], under_systemd=True, recap_on=False,
               settings={"sec_user_agent": f"market-pulse/{VERSION} {EMAIL}", "finnhub_api_key": "k" * 20})
    assert ok["sec"]["state"] == ok["phone"]["state"] == ok["service"]["state"] == "ok"
    assert "recap is switched off" in ok["phone"]["detail"]
    assert "finnhub" not in ok and "watchlist" not in ok
    assert items(channels=["ntfy"], phone_error="ntfy: HTTP 403")["phone"]["state"] == "warn"
