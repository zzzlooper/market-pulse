import asyncio

from app.config import Settings, UserState
from app.dilution import summary
from app.engine import Engine
from app.health import SourceError
from app.notify import from_filing


class RecordingHub:
    def __init__(self):
        self.messages = []

    async def broadcast(self, msg):
        self.messages.append(msg)


def profile(level, points, *reasons):
    return {"level": level, "points": points,
            "reasons": [{"points": p, "text": t} for p, t in reasons]}


def make_engine(tmp_path, profiles, **settings):
    state = UserState(tmp_path / "state.json")
    state.watchlist = ["AAPL", "^GSPC", "ACME"]
    hub = RecordingHub()
    engine = Engine(Settings(**settings), state, hub)
    calls = []

    async def get(symbol):
        calls.append(symbol)
        result = profiles[symbol]
        if isinstance(result, Exception):
            raise result
        return result
    engine.dilution.get = get
    engine.dilution.is_fresh = lambda symbol: symbol in calls
    return engine, hub, calls


def run(engine, coro):
    async def scenario():
        try:
            return await coro
        finally:
            await engine.news.aclose()
            await engine.sec.aclose()
            await engine.halts.aclose()
            await engine.phone.aclose()
    return asyncio.run(scenario())


def test_summary_keeps_the_level_and_top_warning_signs():
    p = profile("high", 5, (2, "a"), (2, "b"), (1, "c"), (1, "d"), (-1, "e"))
    assert summary(p) == {"level": "high", "points": 5, "reasons": ["a", "b", "c"], "warrant_levels": [], "offering_levels": [], "reverse_split": None}
    assert summary(None) is None


def test_risk_covers_watchlist_then_chosen_movers_and_skips_indices(tmp_path):
    engine, hub, calls = make_engine(tmp_path, {
        "AAPL": profile("low", 0), "ACME": profile("high", 6, (2, "S-1 pending")),
        "UPUP": profile("medium", 2, (2, "2 offerings")), "SPY": None,
    }, risk_max_symbols=4)
    engine.movers = {"gainers": [{"symbol": "UPUP"}, {"symbol": "ACME"}], "small_caps": [{"symbol": "SPY"}],
                     "losers": [{"symbol": "DOWN"}], "active": []}

    async def twice():
        await engine._update_risk()
        await engine._update_risk()  # everything fresh now: no new lookups, no new messages
    run(engine, twice())
    assert calls == ["AAPL", "ACME", "UPUP", "SPY"]  # losers aren't in risk_movers; ^GSPC is an index
    assert engine.risk["ACME"] == {"level": "high", "points": 6, "reasons": ["S-1 pending"], "warrant_levels": [], "offering_levels": [], "reverse_split": None}
    assert engine.risk["SPY"] is None
    assert len(hub.messages) == 4 and all(m["type"] == "risk" for m in hub.messages)
    assert engine.snapshot()["risk"] is engine.risk


def test_a_failed_symbol_waits_and_an_sec_outage_pauses_the_round(tmp_path):
    engine, _, calls = make_engine(tmp_path, {
        "AAPL": RuntimeError("timeout"), "ACME": SourceError("SEC refused the request (403)"),
    })
    engine.dilution.is_fresh = lambda symbol: False

    async def rounds():
        await engine._update_risk()  # AAPL fails, ACME stops the round
        await engine._update_risk()  # AAPL is waiting out RISK_RETRY; ACME is tried again
    run(engine, rounds())
    assert calls == ["AAPL", "ACME", "ACME"]
    assert engine.risk == {}


def test_phone_message_mentions_only_elevated_risk():
    item = {"title": "Acme Corp: offering from shelf (424B5)", "url": "", "tickers": ["ACME"],
            "filing": {"kind": "dilution", "label": "offering from shelf", "hot": "watchlist", "risk": "medium"}}
    assert from_filing(item)["body"] == "Acme Corp: offering from shelf (424B5) (dilution risk MEDIUM)"
    item["filing"]["risk"] = "low"
    assert from_filing(item)["body"] == "Acme Corp: offering from shelf (424B5)"
