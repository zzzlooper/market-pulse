import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main

TOKEN = "mp-test-token-k3v9"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", TOKEN)
    monkeypatch.setattr(main, "LOGIN_FAIL_DELAY", 0)
    # Not used as a context manager, so the lifespan (and its network polling) never starts.
    return TestClient(main.app, base_url="http://localhost")


def test_no_token_configured_means_open_access(client, monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "")
    assert client.get("/api/snapshot").status_code == 200
    with client.websocket_connect("ws://localhost/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_api_needs_the_token(client):
    assert client.get("/api/snapshot").status_code == 401
    assert client.delete("/api/watchlist/AAPL").status_code == 401
    assert client.put("/api/phone", json={"categories": {"news": False}}).status_code == 401
    assert client.get("/api/snapshot", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/snapshot", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_the_page_itself_loads_without_the_token(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="login"' in resp.text


def test_login_sets_a_strict_httponly_cookie(client):
    resp = client.post("/api/login", json={"token": "wrong"})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers

    resp = client.post("/api/login", json={"token": TOKEN})
    assert resp.status_code == 204
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{main.TOKEN_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert client.get("/api/snapshot").status_code == 200  # the client keeps the cookie


def test_websocket_without_the_token_is_closed_with_4401(client):
    with client.websocket_connect("ws://localhost/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_websocket_with_the_cookie_gets_the_snapshot(client):
    client.post("/api/login", json={"token": TOKEN})
    with client.websocket_connect("ws://localhost/ws") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert "health" in msg["data"]


def test_websocket_from_another_site_is_refused_even_without_a_token(client, monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "")
    with client.websocket_connect("ws://localhost/ws", headers={"origin": "http://evil.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4403
    with client.websocket_connect("ws://localhost/ws", headers={"origin": "http://localhost"}) as ws:
        assert ws.receive_json()["type"] == "snapshot"
