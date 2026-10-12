import asyncio
import os
import stat
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import main, market
from app.config import Settings, UserState
from app.engine import Engine
from app.market import ET
from app.reactions import (
    GRACE,
    ReactionStore,
    close_after,
    due,
    finished,
    measure,
    price_at,
    returns,
    summarize,
)
from app.recap import build_recap, next_run


def ts(day, hh, mm):
    return int(datetime(2026, 10, day, hh, mm, tzinfo=ET).timestamp())


def bars(day, start=(4, 0), end=(20, 0), price=lambda t: 10.0, step=60):
    """Bars every step seconds on an October 2026 day (ET), priced by price(t)."""
    out, t, stop = [], ts(day, *start), ts(day, *end)
    while t < stop:
        out.append({"t": t, "o": price(t), "h": price(t), "l": price(t), "c": price(t), "v": 100})
        t += step
    return out


def event(at, **kw):
    return {"id": "e1", "symbol": "ACME", "kind": "dilution", "label": "offering from shelf", "title": "t",
            "context": "mover", "risk": "high", "at": at, "base": None, "p5m": None, "p30m": None,
            "pclose": None, "close_day": None, "m5m": 0, "m30m": 0, "done": 0, **kw}


# Tue Oct 13 2026: the price slides from 10.00 by 1 cent a minute after 10:00 ET.
AT = ts(13, 10, 0)
SLIDE = bars(13, price=lambda t: 10.0 - max(0, t - AT) // 60 * 0.01)


def test_price_at_uses_the_last_bar_within_reach():
    assert price_at(SLIDE, AT, 60) == 10.0
    assert price_at(SLIDE, AT + 300, 300) == pytest.approx(9.95)
    assert price_at(SLIDE, ts(13, 21, 0), 300) is None  # post-market ended at 20:00
    assert price_at(SLIDE, ts(13, 3, 0), 3600) is None  # before the first bar


def test_an_intraday_event_is_measured_at_5m_30m_and_the_close():
    e = event(AT)
    assert not due(e, AT + 300 + GRACE - 1) and due(e, AT + 300 + GRACE)
    early = measure(e, SLIDE, AT + 300 + GRACE)
    assert early == {"base": 10.0, "p5m": pytest.approx(9.95), "m5m": 1}
    e |= early
    later = measure(e, SLIDE, ts(13, 16, 0) + GRACE)
    assert later["p30m"] == pytest.approx(9.70) and later["m30m"] == 1
    assert later["close_day"] == "2026-10-13" and later["pclose"] == pytest.approx(10.0 - 359 * 0.01)
    e |= later
    assert finished(e, ts(13, 16, 30))
    r = returns(e)
    assert r["5m"] == pytest.approx(-0.5) and r["30m"] == pytest.approx(-3.0)
    assert r["close"] == pytest.approx(-35.9)


def test_the_close_waits_for_the_session_and_the_late_bars():
    e = event(AT, base=10.0, m5m=1, m30m=1)
    assert not due(e, ts(13, 16, 0) + GRACE - 1) and due(e, ts(13, 16, 0) + GRACE)
    assert close_after(SLIDE, AT, ts(13, 16, 10)) is None
    assert close_after(SLIDE, AT, ts(13, 16, 0) + GRACE)[0] == "2026-10-13"


def test_an_overnight_event_has_no_5m_reading_and_closes_the_next_day():
    day1, day2 = bars(13, price=lambda t: 8.0), bars(14, price=lambda t: 6.0)
    at = ts(13, 22, 0)
    e = event(at)
    m = measure(e, day1 + day2, ts(14, 16, 0) + GRACE)
    assert m["base"] == 8.0  # last post-market trade before the filing
    assert m["m5m"] == m["m30m"] == 1 and m["p5m"] is None and m["p30m"] is None
    assert m["close_day"] == "2026-10-14" and m["pclose"] == 6.0
    assert returns(e | m) == {"5m": None, "30m": None, "close": pytest.approx(-25.0)}


def test_a_friday_evening_event_closes_on_monday():
    friday = bars(16, price=lambda t: 5.0)
    at = ts(16, 18, 0)
    e = event(at, base=5.0, m5m=1, m30m=1)
    # Saturday: due (a close has passed in the calendar) but there's no session to read yet.
    assert measure(e, friday, ts(17, 12, 0)) == {}
    monday = bars(19, price=lambda t: 5.5)
    assert measure(e, friday + monday, ts(19, 16, 30))["close_day"] == "2026-10-19"


def test_store_ignores_duplicates_and_is_private(tmp_path):
    store = ReactionStore(tmp_path / "data" / "reactions.db")
    row = {k: v for k, v in event(AT).items() if k in ("id", "symbol", "kind", "label", "title", "context", "risk", "at")}
    assert store.add(row) is True and store.add(row) is False
    assert stat.S_IMODE(os.stat(tmp_path / "data" / "reactions.db").st_mode) == 0o600
    store.update("e1", {"base": 10.0, "m5m": 1})
    assert store.pending()[0]["base"] == 10.0
    store.update("e1", {"done": 1})
    assert store.pending() == [] and len(store.since(AT - 1)) == 1
    with pytest.raises(AssertionError):
        store.update("e1", {"symbol": "EVIL"})
    store.close()


def test_summaries_give_medians_and_the_share_that_fell():
    rows = [event(AT, base=10.0, pclose=p, p5m=10.0, label="offering from shelf", risk=r)
            for p, r in ((9.0, "high"), (8.0, "high"), (10.5, "low"))] + [event(AT, label="insider buy")]
    by_label = summarize(rows, lambda r: r["label"])
    assert by_label[0] == {"group": "offering from shelf", "n": 3, "measured": 3, "median_5m": 0.0,
                           "median_30m": None, "median_close": -10.0, "down_share": 0.67}
    assert by_label[1]["group"] == "insider buy" and by_label[1]["measured"] == 0
    by_risk = {g["group"]: g for g in summarize(rows[:3], lambda r: r["risk"])}
    assert by_risk["high"]["median_close"] == -15.0 and by_risk["low"]["median_close"] == 5.0


# --- engine ---------------------------------------------------------------------------------

class Hub:
    async def broadcast(self, msg):
        pass


def make_engine(tmp_path):
    state = UserState(tmp_path / "state.json")
    state.watchlist = ["AAPL"]
    return Engine(Settings(), state, Hub())


def close_engine(engine):
    async def go():
        await engine.news.aclose()
        await engine.sec.aclose()
        await engine.halts.aclose()
        await engine.phone.aclose()
        engine.reactions.close()
    asyncio.run(go())


def test_engine_tracks_catalysts_and_measures_them_with_one_download_per_symbol(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    monkeypatch.setattr("app.engine.now_ts", lambda: AT + 3600)
    engine._track("f1", "ACME", "dilution", "offering from shelf", "Acme: offering", "2026-10-13T10:00:00-04:00", "high")
    engine._track("f2", "ACME", "event", "delisting notice", "Acme: delisting", "2026-10-13T10:00:00-04:00")
    engine._track("old", "ACME", "event", "x", "x", "2026-09-01T10:00:00-04:00")      # too old to measure
    engine._track("idx", "^GSPC", "halt", "x", "x", "2026-10-13T10:00:00-04:00")      # not a stock
    assert [e["id"] for e in engine.reactions.pending()] == ["f1", "f2"]
    assert engine.reactions.pending()[0]["context"] == "other" and engine.reactions.pending()[0]["risk"] == "high"

    fetches = []

    def chart(symbol, range_):
        fetches.append((symbol, range_))
        return {"bars": SLIDE}
    monkeypatch.setattr(market, "fetch_chart", chart)
    asyncio.run(engine._update_reactions())
    assert fetches == [("ACME", "1d")]
    rows = {e["id"]: e for e in engine.reactions.pending()}
    assert rows["f1"]["base"] == 10.0 and rows["f1"]["p30m"] == pytest.approx(9.70)

    asyncio.run(engine._update_reactions())  # only the close is left and it isn't due: no download
    assert len(fetches) == 1
    report = engine.reactions_report(30)
    assert report["tracked"] == 2 and report["offerings_by_risk"][0]["group"] == "high"
    assert set(report["recent"][0]) == {"symbol", "kind", "label", "title", "context", "risk", "at",
                                        "close_day", "returns"}
    close_engine(engine)


def test_reactions_db_lives_next_to_the_state_file(tmp_path):
    engine = make_engine(tmp_path)
    assert engine.reactions.path == tmp_path / "reactions.db"
    close_engine(engine)


# --- recap --------------------------------------------------------------------------------------

def test_recap_lists_moves_halts_filings_and_reactions():
    quotes = [{"symbol": "AAPL", "change_pct": 0.9}, {"symbol": "NVDA", "change_pct": -1.6}, {"symbol": "X"}]
    halts = [{"symbol": "ACME", "reason": "volatility pause"}, {"symbol": "ZZZ", "reason": "news pending"}]
    events = [
        event(AT, id="a", base=10.0, pclose=8.6, close_day="2026-10-13"),
        event(AT, id="b", label="offering from shelf"),
        event(AT, id="c", kind="insider_buy", label="insider buy", symbol="ZZZ", base=4.0, pclose=4.2,
              close_day="2026-10-13"),
        event(AT, id="d", kind="halt", label="volatility pause"),
    ]
    note = build_recap(date(2026, 10, 13), quotes, halts, events, {"ACME", "AAPL"}, ["SEC filings"],
                       "http://192.168.1.20:8000")
    assert note["category"] == "recap" and note["title"] == "Market Pulse · close recap Tue Oct 13"
    assert note["body"].splitlines() == [
        "Watchlist: NVDA -1.6%, AAPL +0.9%",
        "Halts: 2 today · yours: ACME (volatility pause)",
        "Filings: 2 offerings from shelf, 1 insider buy",
        "Biggest reactions by the close:",
        "  ACME offering from shelf: -14.0%",
        "  ZZZ insider buy: +5.0%",
        "Tracked today: 4 catalysts, 2 measured to the close",
        "Data: SEC filings down",
    ]
    assert note["url"] == "http://192.168.1.20:8000/#reactions"
    quiet = build_recap(date(2026, 10, 13), [], [], [], set(), [])
    assert quiet["body"] == "Data: all sources OK" and quiet["url"] is None


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 10, 13, 9, 0, tzinfo=ET), datetime(2026, 10, 13, 16, 30, tzinfo=ET)),   # later today
    (datetime(2026, 10, 13, 16, 30, tzinfo=ET), datetime(2026, 10, 14, 16, 30, tzinfo=ET)),  # just sent
    (datetime(2026, 10, 16, 17, 0, tzinfo=ET), datetime(2026, 10, 19, 16, 30, tzinfo=ET)),   # Friday evening
    (datetime(2026, 10, 17, 12, 0, tzinfo=ET), datetime(2026, 10, 19, 16, 30, tzinfo=ET)),   # Saturday
])
def test_recap_runs_on_weekdays_after_the_close(now, expected):
    assert next_run(now, "16:30") == expected


def test_recap_time_must_be_hh_mm(tmp_path):
    from app.config import load_settings
    (tmp_path / "c.toml").write_text('recap_time = "4:30pm"')
    with pytest.raises(ValueError, match="recap_time"):
        load_settings(tmp_path / "c.toml")


# --- API -----------------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "")
    return TestClient(main.app, base_url="http://localhost")


def test_api_reports_reactions_and_previews_the_recap_without_sending(client, monkeypatch):
    assert client.get("/api/reactions?days=0").status_code == 422
    report = client.get("/api/reactions?days=7").json()
    assert report["days"] == 7 and "by_type" in report and "offerings_by_risk" in report
    sent = []

    async def deliver(note):
        sent.append(note)
    monkeypatch.setattr(main.engine.phone, "deliver", deliver)
    preview = client.get("/api/recap").json()
    assert preview["category"] == "recap" and sent == []
    assert client.post("/api/recap/send").status_code == 409  # no phone channel in tests


def test_an_event_before_todays_first_bar_is_priced_off_the_last_session(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    at = ts(13, 2, 0)  # 2 AM: today's 1-minute bars start at 4 AM
    monkeypatch.setattr("app.engine.now_ts", lambda: at + 3 * 3600)
    engine._track("early", "ACME", "event", "delisting notice", "t", "2026-10-13T02:00:00-04:00")
    fetches = []

    def chart(symbol, range_):
        fetches.append(range_)
        return {"bars": bars(13, price=lambda t: 9.0) if range_ == "1d" else bars(12, (9, 30), (16, 0), lambda t: 7.0, 300)}
    monkeypatch.setattr(market, "fetch_chart", chart)
    asyncio.run(engine._update_reactions())
    assert fetches == ["1d", "5d"]
    assert engine.reactions.pending()[0]["base"] == 7.0  # Monday's close
    close_engine(engine)


def test_offerings_without_a_risk_level_get_one_looked_up(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    monkeypatch.setattr("app.engine.now_ts", lambda: AT + 60)

    async def get(symbol):
        return {"level": "high", "points": 5, "reasons": []}
    engine.dilution.get = get
    item = {"id": "o1", "title": "Acme: offering from shelf (424B5)", "published": "2026-10-13T10:00:00-04:00",
            "tickers": ["ACME"], "alert": False,
            "filing": {"kind": "dilution", "form": "424B5", "label": "offering from shelf", "hot": None}}

    async def poll(watchlist, movers):
        return [item]
    engine.filings.poll = poll

    async def scenario():
        await engine._update_filings()
        filler = asyncio.create_task(engine._fill_offering_risk())
        while not engine._risk_queue.empty():
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        filler.cancel()
    asyncio.run(scenario())
    assert engine.reactions.pending()[0]["risk"] == "high"
    close_engine(engine)
