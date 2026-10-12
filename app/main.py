"""HTTP + WebSocket entry point. Run with: uvicorn app.main:app"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .alerts import KINDS
from .config import DEFAULT_PHONE_ALERTS, ROOT, UserState, load_settings
from .engine import Engine, Hub
from .market import CHART_RANGES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO; Telegram bot URLs contain the bot token and ntfy URLs
# the topic, so keep those out of the log.
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")
TOKEN_COOKIE = "mp_token"
TOKEN_COOKIE_DAYS = 365
LOGIN_FAIL_DELAY = 1.0  # seconds; slows down guessing

hub = Hub()
engine = Engine(load_settings(), UserState(), hub)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if engine.settings.public_url and not engine.settings.access_token:
        log.warning("public_url is set but access_token is empty: anyone who can reach this app "
                    "can change its watchlist, alert rules and phone alerts (see README)")
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Market Pulse", lifespan=lifespan)


# --- access token ----------------------------------------------------------------------
# With access_token set, every /api/ route and /ws needs it: from the HttpOnly cookie that
# /api/login sets (browsers can't add headers to a WebSocket), or as "Authorization: Bearer".
# The static page itself loads without it, so it can show the login prompt.

def authorized(cookie: str | None, header: str | None) -> bool:
    expected = engine.settings.access_token
    if not expected:
        return True
    scheme, _, bearer = (header or "").partition(" ")
    given = cookie or (bearer.strip() if scheme.lower() == "bearer" else "")
    return bool(given) and secrets.compare_digest(given.encode(), expected.encode())


@app.middleware("http")
async def require_token(request: Request, call_next):
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
    if not same_origin(ws):
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
