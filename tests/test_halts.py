import asyncio
from pathlib import Path

from app.config import Settings, UserState
from app.engine import Engine
from app.halts import HaltFeed, halt_messages, parse_et, parse_halts

FIXTURE = Path(__file__).parent / "fixtures" / "nasdaq_halts.xml"  # real feed, 9 Oct 2026


def halt(symbol="ACME", code="T1", halted="13:00:00", resume=None):
    return {
        "id": f"{symbol}|10/09/2026|{halted}",
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "market": "NASDAQ",
        "reason_code": code,
        "reason": {"T1": "News pending", "LUDP": "Volatility pause (limit up/down)"}[code],
        "category": "news",
        "halted_at": f"2026-10-09T{halted}-04:00",
        "resume_quote_at": None,
        "resume_trade_at": f"2026-10-09T{resume}-04:00" if resume else None,
        "pause_price": None,
    }


def test_parse_real_feed():
    halts = parse_halts(FIXTURE.read_bytes())  # starts with a UTF-8 BOM
    assert len(halts) == 53
    worx = next(h for h in halts if h["symbol"] == "WORX")
    assert worx["reason"] == "Volatility pause (limit up/down)" and worx["category"] == "volatility"
    assert worx["halted_at"] == "2026-10-09T13:28:54.286000-04:00"
    assert worx["resume_trade_at"] == "2026-10-09T13:33:54-04:00"
    open_halt = next(h for h in halts if h["symbol"] == "BKKT.W")
    assert open_halt["reason_code"] == "H11" and open_halt["resume_trade_at"] is None
    assert halts == sorted(halts, key=lambda h: h["halted_at"], reverse=True)
    assert len({h["id"] for h in halts}) == len(halts)


def test_parse_et_formats():
    assert parse_et("10/09/2026", "19:50:00").hour == 19
    assert parse_et("10/09/2026 ", " 13:28:54.286 ").microsecond == 286000
    assert parse_et("10/09/2026", "") is None


def test_feed_baseline_then_new_and_resumption():
    feed = HaltFeed()
    a, b = halt("AAA"), halt("BBB", "LUDP", "13:05:00", resume="13:10:00")
    assert feed.apply([a]) == (True, [], [])                         # startup: baseline only
    assert feed.apply([a]) == (False, [], [])
    changed, new, resumed = feed.apply([a, b])
    assert changed and new == [b] and resumed == []
    a_resuming = {**a, "resume_trade_at": "2026-10-09T14:00:00-04:00"}
    changed, new, resumed = feed.apply([a_resuming, b])
    assert changed and new == [] and resumed == [a_resuming]
    assert feed.apply([b]) == (True, [], [])                          # dropped from the feed


def test_halt_messages_modes():
    watched = halt("AAPL", "LUDP", resume="13:05:00")
    other = halt("ZZZ")
    [(h, kind, msg)] = halt_messages([watched, other], [], ["AAPL"], "watchlist")
    assert h is watched and kind == "halt"
    assert msg == "AAPL halted at 13:00 ET · Volatility pause (limit up/down) · resumes 13:05 ET"
    assert len(halt_messages([watched, other], [], ["AAPL"], "all")) == 2
    assert halt_messages([watched], [], ["AAPL"], "off") == []

    resuming = {**other, "resume_trade_at": "2026-10-09T14:30:00-04:00"}
    [(_, kind, msg)] = halt_messages([], [resuming], ["ZZZ"], "watchlist")
    assert kind == "resume" and msg.startswith("ZZZ trading resumes 14:30 ET")


def test_watchlist_class_share_symbols_match():
    assert len(halt_messages([halt("BRK.B")], [], ["BRK-B"], "watchlist")) == 1


def test_engine_halt_alerts(tmp_path):
    class Hub:
        def __init__(self):
            self.messages = []

        async def broadcast(self, msg):
            self.messages.append(msg)

    async def scenario():
        state = UserState(tmp_path / "state.json")
        state.watchlist = ["ACME"]
        hub = Hub()
        engine = Engine(Settings(), state, hub)
        await engine._apply_halts(*engine.halts.apply([halt("OLD")]))
        await engine._apply_halts(*engine.halts.apply([halt("OLD"), halt("ACME", halted="13:30:00")]))
        await engine.news.aclose()
        await engine.halts.aclose()
        return engine, hub

    engine, hub = asyncio.run(scenario())
    assert [m["type"] for m in hub.messages] == ["halts", "halts", "alerts", "alert_counts"]
    [event] = hub.messages[-2]["data"]
    assert event["kind"] == "halt" and event["symbol"] == "ACME"
    assert [h["symbol"] for h in engine.snapshot()["halts"]] == ["ACME", "OLD"]
