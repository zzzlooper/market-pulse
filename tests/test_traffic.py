"""What the engine sends to the browsers: only what changed. Invented tickers only."""
import asyncio

import pytest
from scenario_kit import Day, Run


@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path)


def sent(run, kind):
    return [m["data"] for m in run.hub.messages if m["type"] == kind]


def test_real_time_ticks_send_only_the_rows_whose_price_moved(day):
    day.quotes("09:30", BOLT=1.0, FERN=2.0)
    run = Run(day, watchlist=["BOLT", "FERN"]).steps("09:31")
    e = run.engine
    try:
        before = len(sent(run, "quotes"))
        e.yahoo_quotes = [{**q, "price": 10.5} if q["symbol"] == "BOLT" else q for q in e.yahoo_quotes]
        asyncio.run(e._publish_quotes(ticks=True))
        asyncio.run(e._publish_quotes(ticks=True))  # nothing moved since: nothing sent
        assert [[q["symbol"] for q in t] for t in sent(run, "quote_ticks")] == [["BOLT"]]
        assert len(sent(run, "quotes")) == before  # no full list for a tick
    finally:
        run.close()


def test_sympathy_and_gaps_go_out_when_they_change_not_every_round(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="04:00")
    day.floats("04:00", ACME=None, CRUX=None, industry={"ACME": "Biotechnology", "CRUX": "Biotechnology"})
    day.premarket("07:00", ACME=(30.0, 20))
    day.movers("07:00", gainers={"ACME": 45.0, "CRUX": 8.0})
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "07:02", "07:03", "07:04")
    try:
        assert len(sent(run, "sympathy")) == 1 and len(sent(run, "gaps")) == 1
    finally:
        run.close()


def test_only_stocks_near_a_luld_band_go_to_the_browsers(day):
    for hhmm, pct in (("09:31", 0.0), ("09:32", 0.0), ("09:33", 0.0), ("09:34", 10.5)):
        day.quotes(hhmm, BOLT=pct, FERN=0.0)
    run = Run(day, watchlist=["BOLT", "FERN"]).steps("09:31", "09:32", "09:33", "09:34")
    try:
        assert set(run.engine.luld) == {"BOLT", "FERN"}  # kept for the ticker panel
        assert sent(run, "luld")[-1].keys() == {"BOLT"} and run.engine.snapshot()["luld"].keys() == {"BOLT"}
        assert asyncio.run(run.engine.ticker_detail("FERN"))["luld"]["near"] is None
    finally:
        run.close()


def test_a_stock_hovering_at_the_sympathy_threshold_doesnt_flicker(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="09:00")
    day.floats("09:00", ACME=None, CRUX=None, industry={"ACME": "Biotechnology", "CRUX": "Biotechnology"})
    for hhmm, crux in (("09:29", 5.5), ("09:30", 5.5), ("09:31", 4.5), ("09:32", 5.2), ("09:33", 2.0)):
        day.movers(hhmm, gainers={"ACME": 45.0, "CRUX": crux})
    run = Run(day, watchlist=["BOLT"])
    try:
        assert "CRUX" in run.steps("09:29", "09:30").engine.sympathy  # 09:29 looks them up as movers
        assert "CRUX" in run.steps("09:31", "09:32").engine.sympathy  # 4.5% and 5.2%: still in
        assert "CRUX" not in run.steps("09:33").engine.sympathy        # 2%: under half the 5% bar
        assert len(sent(run, "sympathy")) == 2                          # in, then out
    finally:
        run.close()


def test_the_tick_loop_sends_just_the_moved_rows(day):
    day.quotes("09:30", BOLT=1.0, FERN=2.0)
    run = Run(day, watchlist=["BOLT", "FERN"]).steps("09:31")
    e = run.engine

    async def one_flush():
        task = asyncio.create_task(e._flush_ticks())
        e.yahoo_quotes = [{**q, "price": 10.7} if q["symbol"] == "FERN" else q for q in e.yahoo_quotes]
        e._ticks.set()
        await asyncio.sleep(0.05)
        task.cancel()
    try:
        asyncio.run(one_flush())
        assert [[q["symbol"] for q in t] for t in sent(run, "quote_ticks")] == [["FERN"]]
    finally:
        run.close()
