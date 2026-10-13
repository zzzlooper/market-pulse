"""HTTP + WebSocket entry point. Run with: uvicorn app.main:app"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .alerts import KINDS
from .clock import clock
from .config import (
    DEFAULT_PHONE_ALERTS,
    DEFAULT_WATCHLIST,
    ROOT,
    STATE_PATH,
    TLS_DIR,
    UserState,
    load_settings,
)
from .engine import Engine, Hub
from .health import SourceError, describe
from .market import CHART_RANGES
from .market import ET as market_ET
from .replay import Player, Recorder, replay_settings
from .setup import EDITABLE, GENERATED, DashboardSettings, SettingsError, checklist
from .tls import ca_der
from .tls import status as tls_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO; Telegram bot URLs contain the bot token and ntfy URLs
# the topic, so keep those out of the log.
logging.getLogger("httpx").setLevel(logging.WARNING)
# yfinance logs every delisted or unknown symbol as an ERROR; failures show in data health instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

log = logging.getLogger(__name__)
mimetypes.add_type("application/manifest+json", ".webmanifest")  # so browsers accept the app manifest

SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")
TOKEN_COOKIE = "mp_token"
TOKEN_COOKIE_DAYS = 365
LOGIN_FAIL_DELAY = 1.0  # seconds; slows down guessing

hub = Hub()
REPLAY = os.environ.get("MARKET_PULSE_REPLAY")  # set by `python -m app.replay play`


def build_engine() -> Engine:
    settings = load_settings(dashboard=DashboardSettings(STATE_PATH.parent / "settings.json").load())
    state = UserState()
    if REPLAY:
        # Its own throwaway folder for state, settings and reactions: a replay must never write
        # to your real data folder (your watchlist, your reaction history).
        state = UserState(Path(tempfile.mkdtemp(prefix="market-pulse-replay-")) / "state.json")
        player = Player(Path(REPLAY))
        recorded = player.header.get("state", {})
        for key in ("watchlist", "keywords", "alert_rules", "unusual_alerts", "halt_alerts"):
            if key in recorded:
                setattr(state, key, recorded[key])
        return Engine(replay_settings(settings), state, hub, io=player)
    if settings.record or os.environ.get("MARKET_PULSE_RECORD") == "1":
        recorder = Recorder(STATE_PATH.parent / "recordings", state.to_dict(), settings.record_keep)
        log.info("recording to %s", recorder.recording.name)
        return Engine(settings, state, hub, io=recorder)
    return Engine(settings, state, hub)


engine = build_engine()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if engine.settings.public_url and not engine.settings.access_token:
        log.warning("public_url is set but access_token is empty: anyone who can reach this app "
                    "can change its watchlist, alert rules and phone alerts (see README)")
    if "@example.com" in engine.settings.sec_user_agent and not REPLAY:
        # The value itself isn't logged: once set, it holds the user's contact address.
        log.warning("sec_user_agent is still the example value; SEC may block news and filings. "
                    "Set it in config.toml (or SEC_USER_AGENT) to an app name and your contact email")
    if REPLAY:
        player = engine.io
        clock.start_replay(player.start_at(os.environ.get("MARKET_PULSE_REPLAY_FROM")),
                           float(os.environ.get("MARKET_PULSE_REPLAY_SPEED") or 1))
        log.warning("REPLAY of %s: no network, no phone alerts", player.recording.name)
    await engine.start()
    yield
    await engine.stop()
    if REPLAY:  # the replay's throwaway copy of state, settings and reactions
        shutil.rmtree(engine.state.path.parent, ignore_errors=True)


app = FastAPI(title="Market Pulse", lifespan=lifespan)


# --- access token ----------------------------------------------------------------------
# With access_token set, every /api/ route and /ws needs it: from the HttpOnly cookie that
# /api/login sets (browsers can't add headers to a WebSocket), or as "Authorization: Bearer".
# The static page itself loads without it, so it can show the login prompt.

def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def host_name(host_header: str | None) -> str:
    """"localhost:8000" -> "localhost", "[::1]:8000" -> "::1"."""
    host = (host_header or "").strip()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str | None) -> bool:
    """Without an access token, answer only to requests addressed to this computer by name.
    That stops DNS rebinding: a website whose name resolves to 127.0.0.1 can't drive the app,
    because its requests carry its own name in the Host header. With a token, its requests
    lack the sign-in cookie instead."""
    return bool(engine.settings.access_token) or is_loopback(host_name(host_header))


def authorized(cookie: str | None, header: str | None) -> bool:
    expected = engine.settings.access_token
    if not expected:
        return True
    scheme, _, bearer = (header or "").partition(" ")
    given = cookie or (bearer.strip() if scheme.lower() == "bearer" else "")
    return bool(given) and secrets.compare_digest(given.encode(), expected.encode())


@app.middleware("http")
async def require_token(request: Request, call_next):
    if not host_allowed(request.headers.get("host")):
        return PlainTextResponse("Market Pulse only answers on localhost until an access token is set "
                                 "(see README: Opening it from your phone).", status_code=403)
    # Browsers send Origin with every cross-site POST/PUT/DELETE: refuse ones from other sites,
    # so a page you visit can't change your watchlist or settings.
    origin = request.headers.get("origin")
    if (request.method not in ("GET", "HEAD", "OPTIONS") and origin
            and urlparse(origin).netloc != request.headers.get("host")):
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    if (request.url.path.startswith("/api/") and request.url.path != "/api/login"
            and not authorized(request.cookies.get(TOKEN_COOKIE), request.headers.get("authorization"))):
        return JSONResponse({"detail": "Access token required"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    token: str


@app.post("/api/login", status_code=204)
async def login(body: LoginIn, request: Request):
    expected = engine.settings.access_token
    if not expected:
        return Response(status_code=204)
    if not secrets.compare_digest(body.token.strip().encode(), expected.encode()):
        await asyncio.sleep(LOGIN_FAIL_DELAY)
        raise HTTPException(401, "Wrong access token")
    resp = Response(status_code=204)
    resp.set_cookie(TOKEN_COOKIE, expected, max_age=TOKEN_COOKIE_DAYS * 86400, httponly=True,
                    samesite="strict", secure=request.url.scheme == "https")
    return resp


@app.post("/api/logout", status_code=204)
async def logout(request: Request):
    """Sign this browser out: delete the HttpOnly cookie (page scripts can't touch it)."""
    resp = Response(status_code=204)
    resp.delete_cookie(TOKEN_COOKIE, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return resp


def same_origin(ws: WebSocket) -> bool:
    """Browsers let any website open a WebSocket to localhost; they always send Origin, so
    refuse pages served from somewhere else. Clients without Origin (scripts) are allowed."""
    origin = ws.headers.get("origin")
    return origin is None or urlparse(origin).netloc == ws.headers.get("host")


class SymbolIn(BaseModel):
    symbol: str


class KeywordIn(BaseModel):
    keyword: str


class RuleIn(BaseModel):
    symbol: str = ""  # empty = every watchlist symbol
    kind: str
    value: float


class ToggleIn(BaseModel):
    enabled: bool


MAX_RULES = 100


@app.get("/api/snapshot")
def snapshot():
    return engine.snapshot()


class SettingsIn(BaseModel):
    values: dict[str, bool | str | float | int | None]


class GenerateIn(BaseModel):
    name: str


NO_STORE = {"Cache-Control": "no-store"}


def _settings_response(body: dict, request: Request) -> JSONResponse:
    resp = JSONResponse(body, headers=NO_STORE)
    # A new access token signs this browser in with it, so changing it doesn't lock you out.
    if engine.settings.access_token and request.cookies.get(TOKEN_COOKIE) != engine.settings.access_token:
        resp.set_cookie(TOKEN_COOKIE, engine.settings.access_token, max_age=TOKEN_COOKIE_DAYS * 86400,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return resp


@app.get("/api/settings")
def get_settings():
    return JSONResponse({"fields": engine.dashboard.describe(engine.settings)}, headers=NO_STORE)


@app.put("/api/settings")
async def put_settings(body: SettingsIn, request: Request):
    try:
        result = await engine.update_settings(body.values)
    except SettingsError as exc:
        raise HTTPException(422, str(exc)) from None
    return _settings_response({**result, "fields": engine.dashboard.describe(engine.settings)}, request)


@app.post("/api/settings/generate")
async def generate_setting(body: GenerateIn, request: Request):
    """Make a strong random access token or ntfy topic, save it, and return it this once."""
    if body.name not in GENERATED:
        raise HTTPException(422, f"Only {', '.join(GENERATED)} can be generated")
    value = GENERATED[body.name]()
    try:
        result = await engine.update_settings({body.name: value})
    except SettingsError as exc:
        raise HTTPException(422, str(exc)) from None
    return _settings_response({**result, "name": body.name, "value": value,
                               "fields": engine.dashboard.describe(engine.settings)}, request)


@app.get("/api/setup")
def setup(request: Request):
    server = request.scope.get("server") or ("", 0)
    client = request.client.host if request.client else None
    phone = engine.phone.status()
    items = checklist(
        engine.settings, channels=engine.phone.channels, phone_error=phone["last_error"],
        health=engine.health.status(), watchlist=engine.state.watchlist, default_watchlist=DEFAULT_WATCHLIST,
        recap_on=engine.state.phone_alerts.get("recap", False), local_client=is_loopback(client),
        local_server=is_loopback(server[0]), https=request.url.scheme == "https",
        under_systemd=os.environ.get("MARKET_PULSE_SERVICE") == "1",  # set by scripts/install-service.sh
        tls=tls_status(TLS_DIR))
    if REPLAY:
        items.insert(0, {"id": "replay", "state": "info", "title": "This is a replay",
                         "detail": "Settings changed here apply to this replay only, and nothing is sent anywhere.",
                         "fix": None})
    return JSONResponse({"items": items, "editable": list(EDITABLE)}, headers=NO_STORE)


@app.get("/api/reactions")
def reactions(days: int = 30):
    if not 1 <= days <= 365:
        raise HTTPException(422, "days must be between 1 and 365")
    return engine.reactions_report(days)


@app.get("/market-pulse-ca.crt")
def ca_certificate():
    """The public CA certificate, for installing on a phone. Never a key. Served without the
    token so a phone can fetch it before trusting the dashboard; it reveals nothing private."""
    der = ca_der(TLS_DIR)
    if der is None:
        raise HTTPException(404, "HTTPS isn't set up: start Market Pulse with --https first")
    return Response(der, media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="market-pulse-ca.crt"'})


@app.get("/api/tls")
def tls_info(request: Request):
    """Public facts for Setup: fingerprint, covered addresses, renewal date. No keys."""
    return JSONResponse({**tls_status(TLS_DIR), "https": request.url.scheme == "https"},
                        headers=NO_STORE)


class DeleteDataIn(BaseModel):
    store: str


@app.get("/api/data")
def your_data():
    """What's kept on this computer: sizes, counts, dates. No contents and no values."""
    return JSONResponse({"stores": engine.data_inventory()}, headers=NO_STORE)


@app.post("/api/data/delete")
async def delete_your_data(body: DeleteDataIn):
    try:
        await engine.delete_data(body.store)
    except SettingsError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse({"stores": engine.data_inventory()}, headers=NO_STORE)


@app.get("/api/reactions.csv")
def reactions_csv(days: int = 90):
    """The reaction history as a spreadsheet file, for your own analysis. Market data only."""
    if not 1 <= days <= 3650:
        raise HTTPException(422, "days must be between 1 and 3650")
    day = clock.now(market_ET).strftime("%Y-%m-%d")  # the replayed day during a replay
    return Response(engine.reactions_csv(days), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="market-pulse-reactions-{day}.csv"',
        "Cache-Control": "no-store"})


@app.get("/api/recap")
async def recap_preview():
    """Today's recap as it would be sent now, without sending it."""
    return await engine.recap_note(measure_first=False)


@app.post("/api/recap/send")
async def recap_send():
    if not engine.phone.channels:
        raise HTTPException(409, "No phone channel configured; see README (ntfy or Telegram)")
    await engine.phone.deliver(await engine.recap_note())
    return engine.phone.status()


@app.get("/api/dilution/{symbol}")
async def dilution(symbol: str):
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "Invalid ticker symbol")
    try:
        profile = await engine.dilution.get(symbol)
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        log.warning("dilution profile for %s failed: %s", symbol, describe(exc))
        raise HTTPException(502, "SEC data is unavailable right now") from exc
    if profile is None:
        raise HTTPException(404, f"{symbol} doesn't file with the SEC (ETFs, indices and many foreign stocks don't)")
    return profile


@app.get("/api/ticker/{symbol}")
async def ticker_detail(symbol: str, range: str = "1d"):
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "Invalid ticker symbol")
    if range not in CHART_RANGES:
        raise HTTPException(422, f"range must be one of: {', '.join(CHART_RANGES)}")
    detail = await engine.ticker_detail(symbol, range)
    if detail is None:
        raise HTTPException(404, f"No data for {symbol}")
    return detail


@app.post("/api/alert-rules", status_code=201)
async def add_rule(body: RuleIn):
    symbol = body.symbol.strip().upper()
    if body.kind not in KINDS:
        raise HTTPException(422, f"Unknown alert type '{body.kind}'")
    if not math.isfinite(body.value) or body.value <= 0:
        raise HTTPException(422, "Value must be a positive number")
    if symbol and symbol not in engine.state.watchlist:
        raise HTTPException(422, f"Add {symbol} to the watchlist first")
    if len(engine.state.alert_rules) >= MAX_RULES:
        raise HTTPException(422, f"At most {MAX_RULES} alert rules")
    return await engine.add_rule(symbol, body.kind, body.value)


@app.delete("/api/alert-rules/{rule_id}")
async def remove_rule(rule_id: str):
    if not await engine.remove_rule(rule_id):
        raise HTTPException(404, "No such alert rule")
    return {"alert_rules": engine.state.alert_rules}


@app.put("/api/alert-rules/unusual")
async def set_unusual_alerts(body: ToggleIn):
    await engine.set_unusual_alerts(body.enabled)
    return {"unusual_alerts": body.enabled}


class PhoneAlertsIn(BaseModel):
    categories: dict[str, bool]


@app.put("/api/phone")
async def set_phone_alerts(body: PhoneAlertsIn):
    unknown = set(body.categories) - set(DEFAULT_PHONE_ALERTS)
    if unknown:
        raise HTTPException(422, f"Unknown categories: {', '.join(sorted(unknown))}")
    await engine.set_phone_alerts(body.categories)
    return {"phone_alerts": engine.state.phone_alerts}


@app.post("/api/phone/test")
async def test_phone():
    try:
        return await engine.phone.send_test()
    except RuntimeError as exc:
        raise HTTPException(422, str(exc))


class HaltModeIn(BaseModel):
    mode: str


@app.put("/api/alert-rules/halts")
async def set_halt_alerts(body: HaltModeIn):
    if body.mode not in ("watchlist", "all", "off"):
        raise HTTPException(422, "mode must be 'watchlist', 'all' or 'off'")
    await engine.set_halt_alerts(body.mode)
    return {"halt_alerts": body.mode}


@app.post("/api/watchlist", status_code=201)
async def add_symbol(body: SymbolIn):
    symbol = body.symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "Invalid ticker symbol")
    if not await engine.add_symbol(symbol):
        raise HTTPException(409, f"{symbol} is already on the watchlist")
    return {"watchlist": engine.state.watchlist}


@app.delete("/api/watchlist/{symbol}")
async def remove_symbol(symbol: str):
    if not await engine.remove_symbol(symbol.upper()):
        raise HTTPException(404, f"{symbol} is not on the watchlist")
    return {"watchlist": engine.state.watchlist}


@app.post("/api/keywords", status_code=201)
async def add_keyword(body: KeywordIn):
    keyword = " ".join(body.keyword.split())
    if not 1 <= len(keyword) <= 50:
        raise HTTPException(422, "Keyword must be 1-50 characters")
    if not await engine.add_keyword(keyword):
        raise HTTPException(409, f"'{keyword}' is already a keyword")
    return {"keywords": engine.state.keywords}


@app.delete("/api/keywords/{keyword}")
async def remove_keyword(keyword: str):
    if not await engine.remove_keyword(keyword):
        raise HTTPException(404, f"'{keyword}' is not a keyword")
    return {"keywords": engine.state.keywords}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()  # then close with a code the page can read: 4403 other site, 4401 no token
    if not same_origin(ws) or not host_allowed(ws.headers.get("host")):
        await ws.close(code=4403)
        return
    if not authorized(ws.cookies.get(TOKEN_COOKIE), ws.headers.get("authorization")):
        await ws.close(code=4401)
        return
    hub.clients.add(ws)
    try:
        await hub.send(ws, {"type": "snapshot", "data": engine.snapshot()})
        while True:  # the client never sends anything; this just waits for the disconnect
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
