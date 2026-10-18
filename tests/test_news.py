import asyncio
import logging

import httpx

from app.config import Settings
from app.news import NewsFeed, extract_tickers, feed_error, match_keywords, parse_sec_title


def test_extract_exchange_tickers():
    text = "Acme Corp. (NASDAQ: ACME) and Beta Inc (NYSE:BETA) announce merger; also NYSE American: XYZ"
    assert extract_tickers(text) == ["ACME", "BETA", "XYZ"]


def test_extract_handles_class_shares_otc_and_cashtags():
    assert extract_tickers("(NYSE: BRK.B)") == ["BRK.B"]
    assert extract_tickers("Foo (OTCQB: FOOO) rallies, $TSLA too") == ["FOOO", "TSLA"]


def test_extract_ignores_dollar_amounts_and_dupes():
    assert extract_tickers("raises $5 million; $ACME $ACME (Nasdaq: ACME)") == ["ACME"]


def test_match_keywords_whole_words_case_insensitive():
    text = "Company receives FDA approval and announces share buyback"
    assert match_keywords(text, ["fda", "buyback", "merger"]) == ["fda", "buyback"]
    assert match_keywords("Offerings update", ["offering"]) == []


def test_parse_sec_title():
    assert parse_sec_title("8-K - Apple Inc. (0000320193) (Filer)") == ("8-K", "Apple Inc.", "0000320193")
    assert parse_sec_title("Apple announces results") is None


def test_feed_errors_never_repeat_the_address():
    url = "https://user:pa55@feeds.example.test/k-s3cr3t/rss?token=t0ken"
    request = httpx.Request("GET", url)
    status = httpx.HTTPStatusError("Client error '404 Not Found' for url " + url,
                                   request=request, response=httpx.Response(404, request=request))
    assert feed_error(status) == "HTTP 404 Not Found"
    connect = feed_error(httpx.ConnectError(f"cannot reach {url}; tried http://[::1"))
    assert connect == "cannot reach feeds.example.test/…; tried …"
    assert feed_error(TimeoutError()) == "TimeoutError"


def test_a_headline_trimmed_from_the_history_never_comes_back_as_new():
    def rss(*titles):
        items = "".join(f"<item><title>{t}</title><link>https://wire.example.test/{i}</link></item>"
                        for i, t in enumerate(titles))
        return httpx.Response(200, text=f"<rss><channel>{items}</channel></rss>")
    later = ("Acme wins FDA approval", "Bolt opens a plant", "Crux names a chief")
    answers = [rss("Acme wins FDA approval"), rss(*later), rss(*later)]
    feed = NewsFeed(Settings(feeds={"Wire": "https://wire.example.test/rss"}, ticker_news=False, news_history=2))
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: answers.pop(0)))

    async def polls():
        try:
            return [await feed.poll([], ["FDA"]) for _ in range(3)]
        finally:
            await feed.aclose()
    first, second, third = asyncio.run(polls())
    assert [(n["title"], n["alert"]) for n in first] == [("Acme wins FDA approval", False)]  # backlog: baseline
    assert {n["title"] for n in second} == {"Bolt opens a plant", "Crux names a chief"}
    assert "Acme wins FDA approval" not in {n["title"] for n in feed.latest()}  # trimmed to news_history
    assert third == []  # still in the feed, but seen: no repeat, no alert


def test_a_failing_feed_with_a_token_in_its_address_keeps_it_out_of_the_log(caplog):
    def handler(request):
        if request.url.host == "down.example.test":
            raise httpx.ConnectError(f"connection refused: {request.url}")
        return httpx.Response(404)
    feed = NewsFeed(Settings(feeds={"Private": "https://feeds.example.test/rss?token=t0ken-404",
                                    "Down": "https://down.example.test/k-s3cr3t/rss"}, ticker_news=False))
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def poll():
        try:
            await feed.poll([], [])
        finally:
            await feed.aclose()
    with caplog.at_level(logging.WARNING, logger="app.news"):
        asyncio.run(poll())
    assert feed.failed == ["Private", "Down"]
    assert "feed Private failed: HTTP 404 Not Found" in caplog.text
    assert "feed Down failed: connection refused: down.example.test/…" in caplog.text
    assert "t0ken-404" not in caplog.text and "k-s3cr3t" not in caplog.text
