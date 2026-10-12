import asyncio

from app.config import Settings, UserState
from app.engine import Engine


class RecordingHub:
    def __init__(self):
        self.messages = []

    async def broadcast(self, msg):
        self.messages.append(msg)

    def of_type(self, kind):
        return [m["data"] for m in self.messages if m["type"] == kind]


def make_engine(tmp_path):
    state = UserState(tmp_path / "state.json")
    state.watchlist = ["AAPL"]
    state.alert_rules = []
    hub = RecordingHub()
    return Engine(Settings(), state, hub), hub


def quote(price, rvol=1.0, symbol="AAPL"):
    return {"symbol": symbol, "price": price, "change_pct": 0.0, "volume": 1_000_000, "rvol": rvol}


def test_rule_fires_through_engine_and_is_persisted(tmp_path):
    async def scenario():
        engine, hub = make_engine(tmp_path)
        rule = await engine.add_rule("AAPL", "above", 105)
        engine.yahoo_quotes = [quote(100)]
        await engine._publish_quotes()
        engine.yahoo_quotes = [quote(106)]
        await engine._publish_quotes()
        await engine.news.aclose()
        return engine, hub, rule

    engine, hub, rule = asyncio.run(scenario())
    [[event]] = hub.of_type("alerts")
    assert event["symbol"] == "AAPL" and "crossed above 105" in event["message"]
    assert engine.snapshot()["alerts"] == [event]
    assert UserState(tmp_path / "state.json").alert_rules == [rule]  # survives restart


def test_unusual_alerts_wait_for_movers(tmp_path):
    async def scenario():
        engine, hub = make_engine(tmp_path)
        engine.yahoo_quotes = [quote(100, rvol=1)]
        await engine._update_unusual()                     # movers not loaded: no baseline yet
        engine.movers["gainers"] = [quote(10, rvol=5, symbol="BULL")]
        await engine._update_unusual()                     # first full list = baseline
        engine.movers["gainers"].append(quote(20, rvol=4, symbol="NEWX"))
        await engine._update_unusual()
        await engine.set_unusual_alerts(False)
        engine.movers["gainers"].append(quote(30, rvol=6, symbol="QUIET"))
        await engine._update_unusual()
        await engine.news.aclose()
        return hub

    hub = asyncio.run(scenario())
    assert [[e["symbol"] for e in batch] for batch in hub.of_type("alerts")] == [["NEWX"]]


def test_removing_rule_stops_it(tmp_path):
    async def scenario():
        engine, hub = make_engine(tmp_path)
        rule = await engine.add_rule("", "rvol", 3)
        engine.yahoo_quotes = [quote(100, rvol=1)]
        await engine._publish_quotes()
        assert await engine.remove_rule(rule["id"])
        engine.yahoo_quotes = [quote(100, rvol=4)]
        await engine._publish_quotes()
        await engine.news.aclose()
        return engine, hub

    engine, hub = asyncio.run(scenario())
    assert hub.of_type("alerts") == []
    assert engine.state.alert_rules == []
