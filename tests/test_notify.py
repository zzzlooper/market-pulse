import asyncio
import json
import logging

import httpx
import pytest

from app import notify
from app.config import Settings
from app.notify import PhoneNotifier, from_alert, from_news

ALL = {"price": True, "halts": True, "news": True, "unusual": True}


def settings(**kw):
    base = {"ntfy_server": "https://ntfy.test", "ntfy_topic": "mp-secret-topic-x7k2",
                "telegram_bot_token": "123:SECRETTOKEN", "telegram_chat_id": "987654321",
                "telegram_api": "https://tg.test"}
    return Settings(**{**base, **kw})


def make(handler=None, **kw):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request) if handler else httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    statuses = []

    async def on_status(s):
        statuses.append(s)

    return PhoneNotifier(settings(**kw), client, on_status), requests, statuses


ALERT = {"symbol": "AAPL", "kind": "above", "message": "AAPL crossed above 350 · last 350.12"}


def test_from_alert_and_news():
    n = from_alert(ALERT, "http://192.168.1.20:8000/")
    assert n == {"category": "price", "title": "AAPL · price alert", "body": ALERT["message"],
                 "url": "http://192.168.1.20:8000/#ticker=AAPL", "priority": "high",
                 "tag": "chart_with_upwards_trend"}
    halt = from_alert({"symbol": "CABO", "kind": "halt", "message": "CABO halted"})
    assert (halt["category"], halt["priority"], halt["url"]) == ("halts", "urgent", None)
    news = from_news({"tickers": ["ACME"], "source": "GlobeNewswire", "title": "Acme wins FDA approval",
                      "url": "https://x.test/a", "sentiment": {"label": "bullish"}})
    assert (news["category"], news["title"], news["tag"]) == ("news", "ACME · GlobeNewswire", "green_circle")


def test_deliver_formats_ntfy_and_telegram():
    async def scenario():
        notifier, requests, statuses = make()
        await notifier.deliver(from_alert(ALERT, "http://box:8000"))
        await notifier.aclose()
        return notifier, requests, statuses

    notifier, requests, statuses = asyncio.run(scenario())
    ntfy, tg = sorted(requests, key=lambda r: r.url.host)
    assert str(ntfy.url) == "https://ntfy.test/mp-secret-topic-x7k2"
    assert ntfy.content.decode() == ALERT["message"]
    assert ntfy.headers["Title"] == "AAPL - price alert"
    assert ntfy.headers["Priority"] == "high" and ntfy.headers["Click"] == "http://box:8000/#ticker=AAPL"
    assert "Authorization" not in ntfy.headers
    assert str(tg.url) == "https://tg.test/bot123:SECRETTOKEN/sendMessage"
    body = json.loads(tg.content)
    assert body["chat_id"] == "987654321" and body["disable_notification"] is False
    assert body["text"] == "AAPL · price alert\nAAPL crossed above 350 · last 350.12\nhttp://box:8000/#ticker=AAPL"
    assert notifier.last_error is None and notifier.last_sent
    assert statuses[-1]["targets"] == ["ntfy ntfy.test/…x7k2", "Telegram chat …4321"]


def test_ntfy_token_and_single_channel():
    async def scenario():
        notifier, requests, _ = make(ntfy_token="tk_abc", telegram_bot_token="")
        assert notifier.channels == ["ntfy"]
        await notifier.deliver(from_alert(ALERT))
        await notifier.aclose()
        return requests

    [req] = asyncio.run(scenario())
    assert req.headers["Authorization"] == "Bearer tk_abc"


def test_errors_are_reported_without_secrets():
    def fail(request):
        if request.url.host == "tg.test":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(403, text="forbidden")

    async def scenario():
        notifier, _, statuses = make(fail)
        await notifier.deliver(from_alert(ALERT))
        await notifier.aclose()
        return notifier, statuses

    notifier, statuses = asyncio.run(scenario())
    assert "chat not found" in notifier.last_error and "HTTP 403" in notifier.last_error
    assert "SECRETTOKEN" not in notifier.last_error and "secret-topic" not in notifier.last_error
    assert statuses[-1]["last_error"] == notifier.last_error and notifier.last_sent is None


def test_request_addresses_with_the_bot_token_stay_out_of_the_log(caplog):
    import app.main  # noqa: F401 - sets up logging the way the app does
    caplog.set_level(logging.INFO)

    async def scenario():
        notifier, requests, _ = make()
        await notifier.deliver(from_alert(ALERT))
        await notifier.aclose()
        return requests

    requests = asyncio.run(scenario())
    assert any("SECRETTOKEN" in str(r.url) for r in requests)  # Telegram's address carries the token...
    assert "SECRETTOKEN" not in caplog.text and "secret-topic" not in caplog.text  # ...the log never does


def test_partial_failure_still_counts_as_sent():
    def telegram_fails(request):
        if request.url.host == "tg.test":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200)

    async def scenario():
        notifier, _, _ = make(telegram_fails)
        await notifier.deliver(from_alert(ALERT))
        await notifier.aclose()
        return notifier

    notifier = asyncio.run(scenario())
    assert notifier.last_sent and notifier.last_error.startswith("telegram:")


def test_unconfigured_sends_nothing():
    async def scenario():
        notifier, requests, _ = make(ntfy_topic="", telegram_bot_token="")
        notifier.enqueue([from_alert(ALERT)], ALL)
        with pytest.raises(RuntimeError, match="No phone channel configured"):
            await notifier.send_test()
        await notifier.aclose()
        return notifier, requests

    notifier, requests = asyncio.run(scenario())
    assert requests == [] and notifier._queue.empty()


def test_run_filters_categories_and_collapses_bursts(monkeypatch):
    monkeypatch.setattr(notify, "BATCH_WINDOW", 0.05)

    async def scenario():
        notifier, requests, _ = make(telegram_bot_token="")  # ntfy only: one request per message
        task = asyncio.create_task(notifier.run())
        notifier.enqueue([from_alert({"symbol": "X", "kind": "unusual", "message": "m"})],
                         {"unusual": False, "price": True})  # filtered out
        notifier.enqueue([from_alert({**ALERT, "symbol": s}) for s in ("A", "B", "C", "D", "E")], ALL)
        await asyncio.sleep(0.3)
        task.cancel()
        await notifier.aclose()
        return requests

    requests = asyncio.run(scenario())
    titles = [r.headers["Title"] for r in requests]
    assert titles == ["A - price alert", "B - price alert", "Market Pulse - +3 more alerts"]
    assert requests[-1].content.decode() == "C, D, E"


def test_per_minute_cap(monkeypatch):
    monkeypatch.setattr(notify, "BATCH_WINDOW", 0.01)

    async def scenario():
        notifier, requests, _ = make(telegram_bot_token="")
        task = asyncio.create_task(notifier.run())
        for i in range(23):  # separate bursts, so batching doesn't merge them
            notifier.enqueue([from_alert({**ALERT, "symbol": f"S{i}"})], ALL)
            await asyncio.sleep(0.03)
        task.cancel()
        await notifier.aclose()
        return requests

    assert len(asyncio.run(scenario())) == 20  # the cap CLAUDE.md states, not just PER_MINUTE


def test_from_outage():
    down = notify.from_outage({"kind": "down", "label": "Yahoo prices", "reason": "HTTP 503", "minutes": 6})
    assert down["category"] == "sources"
    assert down["title"] == "Market Pulse · Yahoo prices down"
    assert down["body"].startswith("No data for 6 min: HTTP 503.")
    back = notify.from_outage({"kind": "recovered", "label": "Yahoo prices", "reason": None, "minutes": 7})
    assert (back["title"], back["priority"]) == ("Market Pulse · Yahoo prices back", "low")
