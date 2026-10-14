"""Do the plan's flags work (gappers measured after the open), and how good are the LULD
estimates (checked against official pause prices). Invented tickers and prices only."""
import json
import sqlite3
from datetime import date

import pytest
from scenario_kit import Day, Run, ts

from app.luld import CHECKS_KEPT, Checks
from app.plan import direction, tags
from app.reactions import ReactionStore


@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path, start="04:00")


def test_a_gappers_groups_are_its_catalyst_and_flags():
    e = {"gap_pct": 30.0, "catalyst": {"text": "x", "filing": True},
         "flags": [{"text": t} for t in ("low float", "SSR", "day 2 (+45%)", "RS 1:10 ahead", "dilution high",
                                          "earnings before the open", "halted ×2", "watchlist")]}
    assert direction(e) == "gap up" and direction({**e, "gap_pct": -5.0}) == "gap down"
    assert tags(e) == ["with a filing", "low float", "SSR", "day 2", "reverse split ahead", "dilution high",
                       "earnings today", "halted"]
    assert tags({"catalyst": None, "flags": [{"text": "RS 1:20"}]}) == ["no catalyst", "reverse split done"]


def test_gappers_are_measured_from_the_open_and_reported_by_flag(day):
    day.headline("06:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports positive topline data")
    day.premarket("07:00", CRUX=(38.0, 55), DYNA=(-8.0, 20))
    day.movers("04:00", gainers={"DYNA": 1.0})
    day.floats("04:00", CRUX=6_000_000, DYNA=90_000_000)
    day.chart("CRUX", lambda hhmm: 13.8 if hhmm < "09:45" else 15.18 if hhmm < "15:00" else 12.42)  # +10% by 10:00, -10% close
    day.chart("DYNA", lambda hhmm: 9.2)
    run = Run(day, watchlist=["BOLT"]).steps("07:01", "09:32", "10:40", "16:25")
    try:
        groups = {g["group"]: g for g in run.engine.reactions_report(1)["gaps"]}
        assert set(groups) == {"gap up", "gap up · with a headline", "gap up · low float", "gap down",
                               "gap down · no catalyst"}
        low = groups["gap up · low float"]
        assert (low["median_30m"], low["median_close"], low["down_share"]) == (10.0, -10.0, 1.0)
        assert run.engine.reactions_report(1)["by_type"] == [] or all(
            g["group"] not in ("gap up", "gap down") for g in run.engine.reactions_report(1)["by_type"])
        tracked = [r for r in run.engine.reactions.since(0) if r["kind"] == "gap"]
        assert {r["title"] for r in tracked} == {"CRUX gapped +38.0%", "DYNA gapped -8.0%"}
        assert all(r["at"] == int(ts("09:30")) for r in tracked)
        assert "Filings:" not in run.recap("16:30")["body"]  # gappers aren't catalysts
    finally:
        run.close()


def test_plan_cards_quote_their_groups_record_once_enough_were_measured(day):
    day.headline("06:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports positive topline data")
    day.premarket("07:00", CRUX=(38.0, 55))
    day.floats("04:00", CRUX=6_000_000)
    run = Run(day, watchlist=["BOLT"], record_min=3).steps("07:01", "07:02")
    try:
        for i in range(3):
            if i == 2:  # two measured: not enough to say anything yet
                run.engine._gap_stats = None
                assert run.engine.game_plan()["entries"][0]["records"] == []
            eid = f"gap:OLD{i}:2026-10-0{i + 5}"
            run.engine.reactions.add({"id": eid, "symbol": f"OLD{i}", "kind": "gap", "label": "gap up", "title": "x",
                                      "context": "mover", "risk": None, "at": int(ts("09:30", date(2026, 10, 5 + i))),
                                      "flags": json.dumps(["with a headline", "low float"])})
            run.engine.reactions.update(eid, {"base": 10.0, "p30m": 11.0, "pclose": 9.0, "done": 1,
                                              "close_day": f"2026-10-0{i + 5}"})
        run.engine._gap_stats = None
        [crux] = run.engine.game_plan()["entries"]
        assert [(g["group"], g["measured"], g["median_30m"], g["median_close"], g["down_share"]) for g in crux["records"]] == [
            ("gap up · with a headline", 3, 10.0, -10.0, 1.0), ("gap up · low float", 3, 10.0, -10.0, 1.0)]
    finally:
        run.close()


def test_a_history_from_before_flags_gets_the_column(tmp_path):
    path = tmp_path / "reactions.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE events (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL, "
                "title TEXT NOT NULL, context TEXT NOT NULL, risk TEXT, at INTEGER NOT NULL, base REAL, p5m REAL, "
                "p30m REAL, pclose REAL, close_day TEXT, m5m INTEGER NOT NULL DEFAULT 0, m30m INTEGER NOT NULL DEFAULT 0, "
                "done INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO events (id, symbol, kind, label, title, context, at) VALUES ('h1', 'ACME', 'halt', 'x', 'x', "
                "'other', 1)")
    old.commit()
    old.close()
    store = ReactionStore(path)
    assert store.add({"id": "g1", "symbol": "ACME", "kind": "gap", "label": "gap up", "title": "x", "context": "mover",
                      "risk": None, "at": 2, "flags": '["SSR"]'})
    assert [(r["id"], r["flags"]) for r in store.since(0)] == [("g1", '["SSR"]'), ("h1", None)]
    store.close()


# --- LULD estimates against the official pause prices ----------------------------------------------------

def test_checks_keep_errors_and_warnings_not_tickers(tmp_path):
    checks = Checks(tmp_path / "luld_checks.json")
    checks.add("2026-10-13", "up", 11.29, 11.30, warned=True)
    checks.add("2026-10-13", "down", 8.70, 9.00, warned=False)
    assert checks.summary() == {"n": 2, "median_error_pct": 1.71, "within_1_pct": 0.5, "within_3_pct": 0.5,
                                "warned_share": 0.5, "worst_error_pct": 3.33}
    assert oct(checks.path.stat().st_mode & 0o777) == "0o600" and "ACME" not in checks.path.read_text()
    for _ in range(CHECKS_KEPT + 5):
        checks.add("2026-10-13", "up", 10, 10, warned=False)
    assert Checks(checks.path).summary()["n"] == CHECKS_KEPT
    assert checks.clear() == CHECKS_KEPT and not checks.path.exists()


def test_a_luld_pause_is_compared_with_the_band_estimated_just_before(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day = Day(tmp_path, start="09:00")
    for hhmm, pct in (("09:31", 0.0), ("09:32", 0.0), ("09:33", 0.0), ("09:34", 10.5)):
        day.quotes(hhmm, BOLT=pct, FERN=0.0)   # BOLT 11.05, near its estimated 11.29 limit-up band
    day.halt("09:35", "BOLT", "LUDP", pause_price=11.30)
    day.halt("09:35", "ZZZZ", "LUDP", pause_price=3.0)   # not a stock the app follows: nothing to compare
    day.halt("09:35", "FERN", "T1", pause_price=9.9)      # a news halt, even with a price: not a band
    run = Run(day, watchlist=["BOLT", "FERN"]).steps("09:31", "09:32", "09:33", "09:34", "09:35")
    try:
        summary = run.engine.luld_checks.summary()
        assert summary["n"] == 1 and summary["median_error_pct"] == 0.1 and summary["warned_share"] == 1.0
        assert run.engine.reactions_report(1)["luld"] == summary
    finally:
        run.close()
