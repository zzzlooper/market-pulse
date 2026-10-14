"""Reverse splits from 8-Ks, and day-2 runners. All filing text here is invented, worded like real
8-Ks; no company or person in it exists."""
from datetime import date

import pytest
from scenario_kit import Day, Run

from app.engine.tape import day2_move
from app.gaps import build_brief
from app.replay import sanitize
from app.splits import current, detect, digest, number

SPLIT_8K = """<html><body><p>Item 5.03 Amendments to Articles of Incorporation.</p>
<p>On October 9, 2026, Bolt Battery Inc filed a certificate of amendment to effect a 1-for-20 reverse
stock split of its common stock. The Reverse Stock Split will become effective at 12:01 a.m. Eastern
Time on October 15, 2026, and the common stock will begin trading on a split-adjusted basis at market
open on October 15, 2026, under a new CUSIP number.</p><p>Signed: Officer Name Placeholder</p></body></html>"""


# --- reading the filing ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "found"), [
    (SPLIT_8K, {"ratio": 20, "effective": "2026-10-15"}),
    ("to effect a one-for-twenty-five (1:25) reverse stock split, effective as of October 20, 2026",
     {"ratio": 25, "effective": "2026-10-20"}),
    ("On October 14, 2026, the Company effected a 1:50 share consolidation.", {"ratio": 50, "effective": "2026-10-14"}),
    ("a reverse split of the ordinary shares at a ratio of one-for-two hundred fifty", {"ratio": 250, "effective": None}),
    ("to effect a reverse stock split at a ratio of 5,000-for-1, effective October 16, 2026",
     {"ratio": 5000, "effective": "2026-10-16"}),
    ("a 1-for-1,500 reverse stock split", {"ratio": 1500, "effective": None}),
    ("a 1-for-20,000 reverse stock split", None),  # beyond any real ratio: a misread
    ("the reverse stock split: each 1-for-1 exchange of certificates", None),
    # not a split the board has set:
    ("stockholders approved a reverse stock split at a ratio within the range of 1-for-5 to 1-for-30", None),
    ("a reverse stock split at a ratio of not less than 1-for-2 and not more than 1-for-40", None),
    ("the Company effected a 2-for-1 forward stock split", None),
    ("The reverse stock split vote will be held at 1:30 p.m. on October 15, 2026", None),  # a time, not a ratio
    ("<p>An 8-K about a new office lease.</p>", None),
])
def test_the_ratio_and_the_day_it_takes_effect(text, found):
    assert detect(text) == found


def test_numbers_in_words():
    assert [number(w) for w in ("20", "twenty", "twenty-five", "one hundred fifty", "two hundred and fifty")] == [
        20, 20, 25, 150, 250]
    assert number("several") is None


def test_a_split_counts_from_a_month_before_until_five_days_after():
    rs = {"ratio": 20, "effective": "2026-10-15"}
    today = date(2026, 10, 13)
    assert current(rs, date(2026, 10, 9), today)
    assert current(rs, date(2026, 10, 9), date(2026, 10, 20)) and not current(rs, date(2026, 10, 9), date(2026, 10, 21))
    assert not current({"ratio": 20, "effective": "2026-12-01"}, date(2026, 10, 9), today)  # too far out
    undated = {"ratio": 20, "effective": None}
    assert current(undated, date(2026, 9, 20), today) and not current(undated, date(2026, 9, 1), today)


def test_a_recording_keeps_the_split_facts_and_no_text():
    kept = sanitize("https://www.sec.gov/Archives/edgar/data/102/x/split.htm", SPLIT_8K.encode()).decode()
    assert "Officer Name Placeholder" not in kept and "Bolt" not in kept
    assert detect(kept) == detect(SPLIT_8K) and detect(digest("<p>nothing</p>")) is None


# --- day 2 -------------------------------------------------------------------------------------------

def test_day_2_is_after_a_30_percent_close_to_close_gain():
    days = [{"date": "2026-10-09", "low": 9.0, "close": 10.0}, {"date": "2026-10-12", "low": 10.0, "close": 14.5},
            {"date": "2026-10-13", "low": 13.0, "close": 13.5}]
    assert day2_move(days, "2026-10-13") == {"date": "2026-10-12", "change_pct": 45.0}  # today's bar is ignored
    assert day2_move(days, "2026-10-14") is None                                        # 13.5 after 14.5
    assert day2_move(days[:1], "2026-10-13") is None


def test_the_brief_names_day_2_gappers_and_todays_reverse_splits():
    gaps = [{"symbol": "ACME", "gap_pct": 22.0, "why": "mover", "catalyst": None}]
    risk = {"ACME": {"level": "low", "reverse_split": None},
            "BOLT": {"level": "high", "reverse_split": {"ratio": 20, "effective": "2026-10-13"}},
            "CRUX": {"level": "low", "reverse_split": {"ratio": 8, "effective": "2026-10-20"}}, "ECHO": None}
    body = build_brief(date(2026, 10, 13), gaps, risk, 3.0, day2={"ACME": {"date": "2026-10-12", "change_pct": 45.0}})["body"]
    assert body.splitlines() == ["ACME +22.0% · day 2 (+45% yesterday)", "Reverse splits today: BOLT 1-for-20"]


# --- in the engine -------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    return Day(tmp_path)


def test_a_watched_stocks_profile_carries_its_announced_reverse_split(day):
    day.profile("BOLT", "high", split_text=SPLIT_8K)
    run = Run(day, watchlist=["BOLT"]).steps("09:31")
    try:
        rs = run.engine.risk["BOLT"]["reverse_split"]
        assert rs == {"ratio": 20, "effective": "2026-10-15", "form": "8-K", "filed": "2026-10-09"}
    finally:
        run.close()


def test_a_split_long_done_isnt_carried(day):
    day.profile("BOLT", "high", split_text="<p>a 1-for-20 reverse stock split effective September 1, 2026</p>",
                split_filed="2026-08-25")
    run = Run(day, watchlist=["BOLT"]).steps("09:31")
    try:
        assert run.engine.risk["BOLT"]["reverse_split"] is None
    finally:
        run.close()


def test_a_reverse_split_8k_says_so_in_its_alert_and_badges_the_stock_at_once(day):
    day.profile("BOLT", "high")  # the profile predates the 8-K
    day.filing("10:02", "8-K", "BOLT", items=[("3.03", "Material Modification to Rights of Security Holders"),
                                              ("5.03", "Amendments to Articles of Incorporation")], text=SPLIT_8K)
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:05")
    try:
        item = run.news("1-for-20 reverse split")
        assert item["title"].endswith(" · 1-for-20 reverse split")
        assert item["summary"].startswith("1-for-20 reverse split, effective Oct 15, 2026. ")
        assert "reverse split" in item["tags"] and item["filing"]["reverse_split"]["ratio"] == 20
        assert [n["title"] for n in run.phone if n["category"] == "filings"] == ["BOLT · shareholder rights change"]
        assert run.engine.risk["BOLT"]["reverse_split"]["ratio"] == 20
        badged = [m["data"]["BOLT"] for m in run.hub.messages if m["type"] == "risk" and "BOLT" in m["data"]]
        assert badged[-1]["reverse_split"] == {"ratio": 20, "effective": "2026-10-15", "form": "8-K", "filed": "2026-10-13"}
        assert "Officer Name Placeholder" not in str(run.hub.messages)
    finally:
        run.close()


def test_yesterdays_big_gainers_are_marked_day_2(day):
    day.movers("09:30", gainers={"ACME": 12.0, "CRUX": 5.0})
    day.daily("09:00", ACME=[("2026-10-09", 9.0, 10.0), ("2026-10-12", 10.0, 14.5)],   # +45%
                       CRUX=[("2026-10-09", 9.0, 10.0), ("2026-10-12", 10.0, 11.0)])   # +10%: no
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:32")
    try:
        assert run.engine.day2 == {"ACME": {"date": "2026-10-12", "change_pct": 45.0}}
        assert {"ACME": {"date": "2026-10-12", "change_pct": 45.0}} in [
            m["data"] for m in run.hub.messages if m["type"] == "day2"]
    finally:
        run.close()
