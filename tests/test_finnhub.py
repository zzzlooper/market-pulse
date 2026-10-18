import asyncio
import json

import httpx
from websockets.asyncio.server import serve

from app.clock import clock
from app.finnhub import MAX_SYMBOLS, STALE_SECONDS, FinnhubFeed, LivePrice, to_finnhub


def run(coro):
    return asyncio.run(coro)


def make_feed(**kwargs):
    updates = []
    feed = FinnhubFeed("test-key", lambda: updates.append(1), **kwargs)
    return feed, updates


def trade(symbol, price, ms):
    return {"s": symbol, "p": price, "t": ms, "v": 100}


def test_to_finnhub():
    assert to_finnhub("AAPL") == "AAPL"
    assert to_finnhub("BRK-B") == "BRK.B"
    assert to_finnhub("^GSPC") is None
    assert to_finnhub("ES=F") is None


def test_set_symbols_caps_and_drops_removed_prices():
    feed, _ = make_feed()
    run(feed.set_symbols([f"S{i}" for i in range(MAX_SYMBOLS + 5)] + ["^VIX"]))
    assert len(feed._wanted) == MAX_SYMBOLS
    feed.prices["S0"] = LivePrice(price=1.0)
    run(feed.set_symbols(["S1"]))
    assert "S0" not in feed.prices


def test_handle_keeps_latest_trade_per_symbol():
    feed, updates = make_feed()
    run(feed.set_symbols(["AAPL", "BRK-B"]))
    feed.handle({"type": "trade", "data": [
        trade("AAPL", 101.0, 2_000), trade("AAPL", 100.0, 1_000),  # out of order
        trade("BRK.B", 450.0, 1_500), trade("MSFT", 1.0, 3_000),   # MSFT not subscribed
    ]})
    assert feed.prices["AAPL"].price == 101.0
    assert feed.prices["BRK-B"].price == 450.0
    assert "MSFT" not in feed.prices
    assert updates == [1]
    feed.handle({"type": "ping"})
    assert updates == [1]


def test_overlay():
    feed, _ = make_feed()
    yahoo = {"symbol": "AAPL", "price": 99.0, "prev_close": 100.0, "volume": 5, "rvol": 1.5}
    assert feed.overlay(yahoo) == {**yahoo, "live": False}

    feed.prices["AAPL"] = LivePrice(price=110.0, time=1.0, seen=clock.time())  # no Finnhub prev close: use Yahoo's
    row = feed.overlay(yahoo)
    assert (row["price"], row["change"], row["change_pct"], row["live"]) == (110.0, 10.0, 10.0, True)
    assert row["rvol"] == 1.5

    feed.prices["AAPL"].prev_close = 88.0
    assert feed.overlay(yahoo)["change"] == 22.0


def test_stale_price_falls_back_to_yahoo_until_finnhub_recovers():
    feed, _ = make_feed()
    run(feed.set_symbols(["AAPL"]))
    yahoo = {"symbol": "AAPL", "price": 99.0, "prev_close": 100.0}
    try:
        clock.freeze(1_000.0)
        feed.connected_at = 1_000.0
        feed.handle({"type": "trade", "data": [trade("AAPL", 110.0, 1_000_000)]})
        clock.freeze(1_000.0 + 600)  # a quiet stock on a live stream: its last trade is still its price
        assert feed.overlay(yahoo)["live"]

        feed.connected_at = None  # the stream drops
        clock.freeze(1_000.0 + 600 + STALE_SECONDS + 1)
        row = feed.overlay(yahoo)
        assert (row["price"], row["live"], row["delayed"]) == (99.0, False, True)

        feed.connected_at = clock.time()  # back, but the price is from before the drop
        assert feed.overlay(yahoo)["price"] == 99.0
        feed.handle({"type": "trade", "data": [trade("AAPL", 112.0, 1_700_000)]})
        row = feed.overlay(yahoo)
        assert (row["price"], row["live"], "delayed" in row) == (112.0, True, False)
    finally:
        clock.reset()


def test_price_heard_recently_stays_live_without_the_stream():
    feed, _ = make_feed()
    yahoo = {"symbol": "AAPL", "price": 99.0}
    try:
        clock.freeze(1_000.0)
        feed.prices["AAPL"] = LivePrice(price=110.0, time=990.0, seen=1_000.0)  # e.g. a REST quote
        clock.freeze(1_000.0 + STALE_SECONDS)
        assert feed.overlay(yahoo)["live"]
        clock.freeze(1_000.0 + STALE_SECONDS + 1)
        assert feed.overlay(yahoo)["delayed"]
    finally:
        clock.reset()


def test_refresh_quotes_from_rest():
    def handler(request):
        assert request.headers["X-Finnhub-Token"] == "test-key"
        symbol = request.url.params["symbol"]
        if symbol == "NOPE":
            return httpx.Response(200, json={"c": 0, "pc": 0, "t": 0})
        return httpx.Response(200, json={"c": 150.0, "pc": 140.0, "t": 1_700_000_000})

    async def scenario():
        feed, updates = make_feed()
        feed._client = httpx.AsyncClient(base_url="https://test", headers=feed._client.headers,
                                         transport=httpx.MockTransport(handler))
        await feed.set_symbols(["AAPL", "NOPE"])
        await feed.refresh_quotes()
        return feed, updates

    feed, updates = run(scenario())
    live = feed.prices["AAPL"]
    assert (live.price, live.prev_close, live.time) == (150.0, 140.0, 1_700_000_000)
    assert feed.overlay({"symbol": "AAPL"})["live"]
    assert "NOPE" not in feed.prices
    assert updates == [1]


def test_stream_subscribes_and_applies_trades():
    """End-to-end against a fake Finnhub WebSocket server."""
    received = []

    async def fake_finnhub(ws):
        assert ws.request.path == "/?token=test-key"
        async for raw in ws:
            msg = json.loads(raw)
            received.append(msg)
            if msg == {"type": "subscribe", "symbol": "AAPL"}:
                await ws.send(json.dumps({"type": "ping"}))
                await ws.send(json.dumps({"type": "trade", "data": [trade("AAPL", 123.45, 1_000)]}))

    async def scenario():
        got_trade = asyncio.Event()
        feed = FinnhubFeed("test-key", got_trade.set, ws_url="ws://127.0.0.1:8799/")
        await feed.set_symbols(["AAPL", "TSLA"])
        async with serve(fake_finnhub, "127.0.0.1", 8799):
            task = asyncio.create_task(feed.run())
            await asyncio.wait_for(got_trade.wait(), 5)
            await feed.set_symbols(["AAPL"])  # live unsubscribe
            await asyncio.sleep(0.2)
            task.cancel()
        await feed.aclose()
        return feed

    feed = run(scenario())
    assert feed.prices["AAPL"].price == 123.45
    assert {"type": "subscribe", "symbol": "TSLA"} in received
    assert received[-1] == {"type": "unsubscribe", "symbol": "TSLA"}


def test_rejected_key_is_reported():
    async def scenario():
        feed, _ = make_feed()
        feed._client = httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(
            lambda request: httpx.Response(401)))
        await feed.set_symbols(["AAPL"])
        await feed.refresh_quotes()
        await feed.aclose()
        return feed

    assert run(scenario()).error == "API key rejected (HTTP 401); check finnhub_api_key"


def test_stream_errors_never_contain_the_key():
    async def scenario():
        # An invalid URL makes websockets echo the whole connect URL, ?token=… included.
        feed = FinnhubFeed("test-key", lambda: None, ws_url="not a url")
        task = asyncio.create_task(feed.run())
        for _ in range(50):
            if feed.error:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await feed.aclose()
        return feed

    feed = run(scenario())
    assert feed.error.startswith("stream disconnected:")
    assert "test-key" not in feed.error
    assert not feed.connected


def test_stream_disconnect_logs_never_contain_the_key(caplog):
    async def scenario():
        # An invalid URL makes websockets echo the whole connect URL, ?token=… included.
        feed = FinnhubFeed("fake-secret-key", lambda: None, ws_url="not a url")
        task = asyncio.create_task(feed.run())
        for _ in range(50):
            if feed.error:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await feed.aclose()

    with caplog.at_level("DEBUG", logger="app.finnhub"):
        run(scenario())
    lines = [r.getMessage() for r in caplog.records if r.name == "app.finnhub"]
    assert any("disconnected" in line for line in lines)
    assert not [line for line in lines if "fake-secret-key" in line]
