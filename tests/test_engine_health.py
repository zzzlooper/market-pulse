import asyncio

import httpx

from app import market
from app.config import Settings, UserState
from app.engine import Engine
from app.health import SourceHealth
from app.news import NewsFeed


class RecordingHub:
    def __init__(self):
        self.messages = []

    async def broadcast(self, msg):
        self.messages.append(msg)

    def of_type(self, kind):
        return [m["data"] for m in self.messages if m["type"] == kind]


class Clock:
    def __init__(self):
        self.t = 1_000.0

    def __call__(self):
        return self.t


def make_engine(tmp_path, **settings):
    state = UserState(tmp_path / "state.json")
    state.watchlist = ["AAPL", "MSFT"]
    state.alert_rules = []
    hub, clock = RecordingHub(), Clock()
    return Engine(Settings(**settings), state, hub, SourceHealth(clock)), hub, clock


def run(engine, coro):
    async def scenario():
        try:
            return await coro
        finally:
            await engine.news.aclose()
            await engine.halts.aclose()
            await engine.phone.aclose()
    return asyncio.run(scenario())


def quote(symbol, price):
    return {"symbol": symbol, "price": price, "change_pct": 0.0, "volume": 1_000_000, "rvol": 1.0}


def test_a_failed_yahoo_fetch_keeps_the_last_prices(tmp_path, monkeypatch):
    engine, hub, _ = make_engine(tmp_path)
    responses = iter([[quote("AAPL", 100), quote("MSFT", 200)], []])
    monkeypatch.setattr(market, "fetch_quotes", lambda symbols: next(responses))

    async def scenario():
        await engine._run_step("quotes", engine._update_quotes)
        await engine._run_step("quotes", engine._update_quotes)

    run(engine, scenario())
    assert [q["price"] for q in engine.yahoo_quotes] == [100, 200]  # not blanked
    assert len(hub.of_type("quotes")) == 1  # the failed fetch published nothing
    status = engine.health.status_of("quotes")
    assert (status["state"], status["reason"]) == ("degraded", "Yahoo returned no prices")


def test_a_partial_yahoo_fetch_keeps_missing_rows_marked_stale(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    responses = iter([[quote("AAPL", 100), quote("MSFT", 200)], [quote("AAPL", 101)]])
    monkeypatch.setattr(market, "fetch_quotes", lambda symbols: next(responses))

    async def scenario():
        await engine._run_step("quotes", engine._update_quotes)
        await engine._run_step("quotes", engine._update_quotes)

    run(engine, scenario())
    assert engine.yahoo_quotes == [quote("AAPL", 101), {**quote("MSFT", 200), "stale": True}]
    assert engine.health.status_of("quotes")["reason"] == "no data for MSFT"


def test_movers_report_failing_screeners_and_keep_their_old_lists(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    old = [quote("OLD", 5)]
    engine.movers["losers"] = old
    monkeypatch.setattr(market, "fetch_movers", lambda: {"gainers": [quote("NEW", 9)], "active": [], "small_caps": []})
    run(engine, engine._run_step("movers", engine._update_movers))
    assert engine.movers["losers"] == old
    assert engine.health.status_of("movers")["reason"] == "screener failing: losers"

    engine, _, _ = make_engine(tmp_path)
    monkeypatch.setattr(market, "fetch_movers", lambda: {})
    run(engine, engine._run_step("movers", engine._update_movers))
    status = engine.health.status_of("movers")
    assert (status["state"], status["reason"]) == ("down", "every Yahoo screener failed")


def news_feed(failing):
    def handler(request):
        if request.url.host in failing:
            return httpx.Response(503)
        return httpx.Response(200, text="<rss><channel></channel></rss>")

    feed = NewsFeed(Settings(feeds={"Wire A": "https://a.test/rss", "Wire B": "https://b.test/rss"},
                             ticker_news=False))
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return feed


def test_news_names_feeds_that_keep_failing(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    asyncio.run(engine.news.aclose())
    engine.news = news_feed(failing={"b.test"})

    async def poll(times):
        for _ in range(times):
            await engine._run_step("news", engine._update_news)
            yield engine.health.status_of("news")

    async def scenario():
        return [s async for s in poll(3)]

    first, second, third = run(engine, scenario())
    assert first["state"] == second["state"] == "ok"  # one missed poll is common: not reported yet
    assert (third["state"], third["reason"]) == ("degraded", "1 of 2 feeds failing: Wire B")

    engine, _, _ = make_engine(tmp_path)
    asyncio.run(engine.news.aclose())
    engine.news = news_feed(failing={"a.test", "b.test"})
    run(engine, engine._run_step("news", engine._update_news))
    status = engine.health.status_of("news")
    assert (status["state"], status["reason"]) == ("down", "all 2 feeds failed")


def test_yahoo_ticker_feeds_are_named_by_symbol():
    feed = NewsFeed(Settings(feeds={}))
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    async def scenario():
        await feed.poll(["AAPL"], [])
        await feed.aclose()

    asyncio.run(scenario())
    assert feed.failed == ["Yahoo Finance AAPL"]


def test_health_changes_are_broadcast_once_and_outages_reach_the_phone(tmp_path):
    engine, hub, clock = make_engine(tmp_path, ntfy_topic="mp-test", source_alert_minutes=5)
    sent = []
    engine.phone.enqueue = lambda notes, categories: sent.extend(
        n for n in notes if categories.get(n["category"]))

    async def scenario():
        await engine._check_health()
        await engine._check_health()  # nothing changed: no second broadcast
        assert len(hub.of_type("health")) == 1
        clock.t += 181  # past every source's stale limit (movers: 3 x 60 s) with no data: all down
        await engine._check_health()
        clock.t += 300
        await engine._check_health()
        await engine._check_health()  # once per outage
        engine.health.ok("quotes")
        await engine._check_health()

    run(engine, scenario())
    states = [{s["name"]: s["state"] for s in h["sources"]} for h in hub.of_type("health")]
    assert states[0]["quotes"] == "starting"
    assert states[1] == {"quotes": "down", "movers": "down", "news": "down", "halts": "down"}
    assert states[-1]["quotes"] == "ok"
    titles = [n["title"] for n in sent]
    assert titles == ["Market Pulse · Yahoo prices down", "Market Pulse · Yahoo movers down",
                      "Market Pulse · News feeds down", "Market Pulse · Nasdaq halts down",
                      "Market Pulse · Yahoo prices back"]


def test_outage_alerts_respect_the_phone_toggle(tmp_path):
    engine, _, clock = make_engine(tmp_path, ntfy_topic="mp-test")
    engine.state.phone_alerts["sources"] = False
    sent = []
    engine.phone.enqueue = lambda notes, categories: sent.extend(
        n for n in notes if categories.get(n["category"]))

    async def scenario():
        clock.t += 61
        await engine._check_health()
        clock.t += 300
        await engine._check_health()  # same timing that alerts in the test above

    run(engine, scenario())
    assert sent == []
    assert engine.health.state("quotes") == "down"


def test_the_snapshot_includes_health_with_the_server_clock(tmp_path):
    engine, _, clock = make_engine(tmp_path)
    snapshot = run(engine, asyncio.sleep(0, engine.snapshot()))
    assert snapshot["health"]["now"] == clock.t
    assert [s["name"] for s in snapshot["health"]["sources"]] == ["quotes", "movers", "news", "halts"]


def test_a_feed_that_recovers_resets_its_failure_count():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) in (1, 2, 4, 5) else 200, text="<rss></rss>")

    async def scenario():
        feed = NewsFeed(Settings(feeds={"Wire": "https://w.test/rss"}, ticker_news=False))
        feed._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        for _ in range(5):  # fail, fail, ok, fail, fail: never 3 in a row
            await feed.poll([], [])
        await feed.aclose()
        return feed

    feed = asyncio.run(scenario())
    assert feed.failed == ["Wire"]
    assert feed.failing == []
