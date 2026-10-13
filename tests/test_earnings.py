from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from scenario_kit import Day, Run

from app import main
from app.earnings import (
    brief_lines,
    describe,
    earnings_row,
    next_trading_day,
    recap_lines,
    upcoming,
    when,
)
from app.gaps import build_brief
from app.market import ET
from app.recap import build_recap

TUESDAY = date(2026, 10, 13)
FRIDAY = date(2026, 10, 16)


def at(day: str, hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time().replace(hour=h, minute=m), ET).timestamp())


def row(symbol, day, hhmm, estimate=False):
    moment = datetime.fromtimestamp(at(day, hhmm), ET)
    return {"symbol": symbol, "at": at(day, hhmm), "date": day, "when": when(moment), "estimate": estimate}


# --- reading Yahoo's data -------------------------------------------------------------------------

def test_the_next_report_comes_from_whichever_field_points_ahead():
    now = at("2026-10-13", "01:00")
    info = {"earningsTimestamp": at("2026-07-14", "08:30"), "earningsTimestampStart": at("2026-10-13", "08:30"),
            "isEarningsDateEstimate": False}
    assert earnings_row("JPM", info, now) == {"symbol": "JPM", "at": at("2026-10-13", "08:30"), "date": "2026-10-13",
                                              "when": "before the open", "estimate": False}
    assert earnings_row("OLD", {"earningsTimestamp": at("2026-08-01", "16:05")}, now) is None
    assert earnings_row("NONE", {}, now) is None
    estimated = earnings_row("X", {"earningsTimestampStart": at("2026-11-02", "06:59"), "isEarningsDateEstimate": True}, now)
    assert estimated["estimate"] is True


@pytest.mark.parametrize(("hhmm", "expected"), [
    ("06:59", "before the open"), ("09:29", "before the open"), ("09:30", ""), ("15:00", ""),  # 15:00: a placeholder
    ("16:00", "after the close"), ("16:30", "after the close"),
])
def test_only_times_clearly_outside_the_session_are_trusted(hhmm, expected):
    assert when(datetime.fromtimestamp(at("2026-10-13", hhmm), ET)) == expected


def test_descriptions_say_when_and_how_sure():
    assert describe(row("JPM", "2026-10-13", "08:30"), TUESDAY) == "reports before the open today"
    assert describe(row("TSLA", "2026-10-14", "16:05"), TUESDAY) == "reports after the close tomorrow"
    assert describe(row("AAPL", "2026-10-15", "15:00"), TUESDAY) == "reports Thursday, time not confirmed"
    assert describe(row("ECHO", "2026-11-02", "16:05", estimate=True), TUESDAY) == \
        "reports after the close Mon Nov 2 (estimated date)"


def test_next_trading_day_skips_the_weekend():
    assert next_trading_day(TUESDAY) == date(2026, 10, 14)
    assert next_trading_day(FRIDAY) == date(2026, 10, 19)


def test_upcoming_keeps_the_watchlist_window_soonest_first():
    earnings = {r["symbol"]: r for r in (row("A", "2026-10-20", "16:05"), row("B", "2026-10-13", "08:00"),
                                         row("C", "2026-10-19", "08:00"), row("D", "2026-10-14", "16:30"))}
    assert [r["symbol"] for r in upcoming(earnings, ["A", "B", "C", "D", "Z"], TUESDAY)] == ["B", "D"]
    assert [r["symbol"] for r in upcoming(earnings, ["A", "B", "C"], TUESDAY, days=7)] == ["B", "C", "A"]


def test_the_brief_names_todays_reports_and_the_recap_tonights_and_the_next_days():
    earnings = {r["symbol"]: r for r in (row("JPM", "2026-10-13", "08:30"), row("BOLT", "2026-10-13", "16:05"),
                                         row("ACME", "2026-10-14", "07:00"), row("FERN", "2026-10-15", "16:05"))}
    watch = ["JPM", "BOLT", "ACME", "FERN"]
    assert brief_lines(earnings, watch, TUESDAY) == ["JPM reports before the open today",
                                                     "BOLT reports after the close today"]
    assert recap_lines(earnings, watch, TUESDAY) == ["BOLT reports after the close today",
                                                     "ACME reports before the open tomorrow"]
    monday = {"MON": row("MON", "2026-10-19", "07:00")}
    assert recap_lines(monday, ["MON"], FRIDAY) == ["MON reports before the open Monday"]
    assert brief_lines(earnings, ["FERN"], TUESDAY) == []


def test_brief_and_recap_show_an_earnings_section_only_when_theres_one():
    brief = build_brief(TUESDAY, [], {}, 3.0, earnings=["JPM reports before the open today"])
    assert brief["body"].splitlines()[-2:] == ["Earnings:", "  JPM reports before the open today"]
    assert "Earnings" not in build_brief(TUESDAY, [], {}, 3.0)["body"]
    recap = build_recap(TUESDAY, [], [], [], set(), [], earnings=["ACME reports before the open tomorrow"])
    assert recap["body"].splitlines() == ["Earnings:", "  ACME reports before the open tomorrow", "Data: all sources OK"]


# --- through the engine ------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path, start="04:00")


def test_a_watchlist_report_today_reaches_the_brief_and_tomorrows_the_recap(day):
    day.earnings("04:00", BOLT=("2026-10-13", "07:00", False), FERN=("2026-10-14", "16:05", True))
    day.premarket("07:00", BOLT=(4.5, 12))
    run = Run(day, watchlist=["BOLT", "FERN"]).steps("04:05", "07:05")
    try:
        assert set(run.engine.earnings) == {"BOLT", "FERN"}
        assert run.browser("earnings")[-1]["BOLT"]["when"] == "before the open"
        assert run.brief("09:15") is True
        brief = next(n for n in run.phone if n["category"] == "gaps")
        assert brief["body"].splitlines()[-2:] == ["Earnings:", "  BOLT reports before the open today"]
        recap = run.recap("16:30")
        assert "  FERN reports after the close tomorrow (estimated date)" in recap["body"].splitlines()
    finally:
        run.close()


def test_earnings_are_fetched_again_only_when_due_or_when_the_watchlist_changes(day):
    day.earnings("04:00", BOLT=("2026-10-13", "07:00", False))
    run = Run(day, watchlist=["BOLT"])
    calls = []
    original = run.player.call

    async def counting(name, fn, *args):
        if name == "fetch_earnings":
            calls.append(list(args[0]))
        return await original(name, fn, *args)
    run.player.call = counting
    try:
        run.steps("04:05", "05:05", "06:05")             # within 6 hours: one lookup
        assert calls == [["BOLT"]]
        run.engine.state.watchlist.append("FERN")
        run.steps("06:06")                               # new symbol: looked up at once
        assert calls[-1] == ["BOLT", "FERN"]
        run.steps("12:30")                               # 6 hours later: refreshed
        assert len(calls) == 3
    finally:
        run.close()


# --- installing it on a phone ----------------------------------------------------------------------

def test_the_app_manifest_and_icons_are_served_even_with_an_access_token(monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "mp-test-token-q9x")
    client = TestClient(main.app, base_url="http://localhost")
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["display"] == "standalone" and data["start_url"] == "/"
    for icon in [i["src"] for i in data["icons"]] + ["icon-180.png"]:
        assert client.get(f"/{icon}").status_code == 200
    page = client.get("/").text
    assert 'rel="manifest" href="manifest.webmanifest"' in page and 'rel="apple-touch-icon"' in page
    assert client.get("/api/snapshot").status_code == 401  # the data itself still needs the token
