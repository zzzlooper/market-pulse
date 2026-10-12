import asyncio

import httpx
import pytest

from app import sec as sec_module
from app.config import Settings
from app.news import NewsFeed
from app.sec import SecClient

AGENT = "test-agent/1.0 ops@example.org"
TICKER_ROWS = {"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme"},
               "1": {"cik_str": 2, "ticker": "BRK-B", "title": "Berkshire"}}


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(sec_module, "MIN_REQUEST_GAP", 0)


def make_sec(handler):
    client = SecClient(AGENT)
    client._client = httpx.AsyncClient(headers=client._client.headers, follow_redirects=False,
                                       transport=httpx.MockTransport(handler))
    return client


def run(client, coro_fn):
    async def scenario():
        try:
            return await coro_fn()
        finally:
            await client.aclose()
    return asyncio.run(scenario())


def test_only_sec_hosts_get_the_contact_address():
    seen = []
    client = make_sec(lambda r: seen.append(r) or httpx.Response(200, json={}))

    async def calls():
        await client.get("https://www.sec.gov/x")
        await client.get("https://data.sec.gov/y")
        for url in ("https://example.org/z", "https://sec.gov.example.org/z", "http://evil/?www.sec.gov"):
            with pytest.raises(ValueError):
                await client.get(url)
    run(client, calls)
    assert [r.url.host for r in seen] == ["www.sec.gov", "data.sec.gov"]
    assert all(r.headers["User-Agent"] == AGENT for r in seen)
    assert SecClient(AGENT)._client.follow_redirects is False


def test_ticker_map_is_retried_after_a_failure_and_reports_why(monkeypatch):
    calls, clock = [], [1_000.0]
    monkeypatch.setattr(sec_module.clock, "time", lambda: clock[0])

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=TICKER_ROWS)
    client = make_sec(handler)

    async def scenario():
        first = await client.tickers()
        error = client.tickers_error
        again = await client.tickers()  # too soon to retry
        clock[0] += sec_module.TICKER_MAP_RETRY + 1
        return first, error, again, await client.tickers(), client.tickers_error
    first, error, again, later, error_after = run(client, scenario)
    assert first == again == {} and "503" in error
    assert later == {"0000000001": "ACME", "0000000002": "BRK-B"} and error_after is None
    assert len(calls) == 2


def test_concurrent_callers_share_one_ticker_map_download():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=TICKER_ROWS)
    client = make_sec(handler)
    results = run(client, lambda: asyncio.gather(client.tickers(), client.tickers(), client.cik("ACME")))
    assert results[0] == results[1] and results[2] == "0000000001"
    assert len(calls) == 1


def test_cik_lookup_accepts_dotted_class_shares():
    client = make_sec(lambda r: httpx.Response(200, json=TICKER_ROWS))
    assert run(client, lambda: asyncio.gather(client.cik("brk.b"), client.cik("NOPE"))) == ["0000000002", None]


def test_news_sends_sec_feeds_through_the_sec_client_only():
    sec_requests, other_requests = [], []
    client = make_sec(lambda r: sec_requests.append(r) or httpx.Response(200, text="<feed/>"))
    settings = Settings(sec_user_agent=AGENT, ticker_news=False, sec_filings=False,
                        feeds={"Wire": "https://wire.example.org/rss"})
    news = NewsFeed(settings, client)
    news._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: other_requests.append(r) or httpx.Response(200, text="<rss/>")))

    async def scenario():
        try:
            await news.poll([], [])
        finally:
            await news.aclose()
            await client.aclose()
    asyncio.run(scenario())
    # With sec_filings off, the news feed polls the 8-K feed itself, via the SEC client.
    assert [r.url.host for r in sec_requests] == ["www.sec.gov"]
    assert [r.url.host for r in other_requests] == ["wire.example.org"]
    assert "User-Agent" not in other_requests[0].headers or AGENT not in other_requests[0].headers["User-Agent"]
