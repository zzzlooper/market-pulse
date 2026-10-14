"""The morning game plan. Invented tickers, prices and filings only."""
import pytest
from scenario_kit import Day, Run

from app.plan import brief_extras, build

TODAY = "2026-10-13"


def gap(symbol, pct, catalyst=None, price=10.0, why="mover"):
    return {"symbol": symbol, "gap_pct": pct, "price": price, "prev_close": 8.0, "at_open": False, "pre_minutes": 30,
            "why": why, "catalyst": {"text": catalyst, "at": "2026-10-13T07:00:00-04:00", "filing": False} if catalyst else None}


def context(**over):
    base = {"watchlist": [], "floats": {}, "market_caps": {}, "ssr": {}, "risk": {}, "day2": {}, "pre_highs": {},
            "sympathy": {}, "industries": {}, "halts": [], "earnings": {}, "today": TODAY}
    return {**base, **over}


def test_catalysts_first_then_by_the_size_of_the_gap_either_way():
    plan = build([gap("AAA", 50.0), gap("BBB", 8.0, "contract win"), gap("CCC", -30.0), gap("DDD", 20.0, "FDA nod")],
                 **context())
    assert [e["symbol"] for e in plan] == ["DDD", "BBB", "AAA", "CCC"]


def test_every_signal_becomes_a_flag_that_says_why():
    risk = {"ACME": {"level": "high", "reasons": ["S-1 pending", "Cash for 2 months"],
                     "reverse_split": {"ratio": 10, "effective": "2026-10-15"}, "warrant_levels": [], "offering_levels": []}}
    [e] = build([gap("ACME", 25.0, "news")], **context(
        watchlist=["ACME"], floats={"ACME": 4_000_000}, ssr={"ACME": {"since": "2026-10-12", "today": False}}, risk=risk,
        day2={"ACME": {"date": "2026-10-12", "change_pct": 45.0}},
        earnings={"ACME": {"date": TODAY, "when": "before the open"}},
        halts=[{"symbol": "ACME", "halted_at": "2026-10-13T09:35:00-04:00", "reason_code": "LUDP"},
               {"symbol": "ACME", "halted_at": "2026-10-12T10:00:00-04:00", "reason_code": "T1"}]))  # yesterday's
    flags = {f["text"]: f["tone"] for f in e["flags"]}
    assert flags == {"low float": "good", "SSR": "good", "day 2 (+45%)": "info", "RS 1:10 ahead": "warn",
                     "dilution high": "warn", "earnings before the open": "info", "halted ×1": "warn", "watchlist": "info"}
    assert next(f["why"] for f in e["flags"] if f["text"] == "dilution high") == "S-1 pending; Cash for 2 months"
    assert brief_extras(e) == ["low float", "SSR", "RS 1:10 ahead", "halted ×1"]


def test_levels_highest_first_with_their_distance_and_the_industry_theme():
    risk = {"ACME": {"level": "low", "reasons": [], "reverse_split": None,
                     "warrant_levels": [{"kind": "Series A", "price": 7.5}, {"kind": "outstanding", "price": 12.5}],
                     "offering_levels": [{"form": "424B4", "price": 9.0}]}}
    sympathy = {"DYNA": {"industry": "Biotechnology", "leader": "ACME", "leader_pct": 25.0, "change_pct": 8.0}}
    [e] = build([gap("ACME", 25.0, price=10.0)], **context(risk=risk, pre_highs={"ACME": 10.4}, sympathy=sympathy,
                                                              industries={"ACME": "Biotechnology"}))
    assert [(lv["label"], lv["away_pct"]) for lv in e["levels"]] == [
        ("warrants (average)", 25.0), ("pre-market high", 4.0), ("offering (424B4)", -10.0), ("Series A warrants", -25.0)]
    assert e["peers"] == {"industry": "Biotechnology", "leader": None,
                          "followers": [{"symbol": "DYNA", "change_pct": 8.0}]}


# --- in the engine -------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path, start="04:00")


def test_the_engine_plans_the_gappers_and_the_brief_mentions_the_flags(day):
    day.headline("06:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports positive topline data")
    day.premarket("07:00", CRUX=(38.0, 55), BOLT=(4.5, 12), pre_high={"CRUX": 14.2})
    day.floats("04:00", CRUX=6_000_000, BOLT=50_000_000)
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "07:02")
    try:
        plan = run.engine.game_plan()
        assert [e["symbol"] for e in plan["entries"]] == ["CRUX", "BOLT"] and plan["scanning"] is True
        crux = plan["entries"][0]
        assert crux["catalyst"]["text"].startswith("Crux Biotherapeutics") and crux["float_shares"] == 6_000_000
        assert crux["levels"] == [{"label": "pre-market high", "price": 14.2, "kind": "pmh", "away_pct": 2.9}]
        assert run.brief("09:15")
        body = run.phone[-1]["body"].splitlines()
        assert body[0].startswith("CRUX +38.0% · Crux Biotherapeutics") and body[0].endswith("topline data, low float")
        assert body[1] == "BOLT +4.5% · watchlist"
    finally:
        run.close()


def test_a_reverse_split_already_done_is_a_runner_hint_not_a_warning():
    risk = {"ACME": {"level": "low", "reasons": [], "reverse_split": {"ratio": 20, "effective": "2026-10-12"},
                     "warrant_levels": [], "offering_levels": []}}
    [e] = build([gap("ACME", 15.0)], **context(risk=risk))
    assert [(f["text"], f["tone"]) for f in e["flags"]] == [("RS 1:20", "info")]
