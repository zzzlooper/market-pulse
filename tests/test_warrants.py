"""Warrant overhang signals. All filing text here is invented, worded like real covers."""
import asyncio
import json
from datetime import date
from urllib.parse import urlparse

import httpx
import pytest
from scenario_kit import Day, Run

from app import sec as sec_module
from app.dilution import DilutionProfiles, assess, warrant_summary
from app.sec import SecClient
from app.warrants import describe_outstanding, describe_sold, detect, digest

UNITS_COVER = """<html><body><p>PROSPECTUS SUPPLEMENT</p><p>Acme Rocket Corp</p>
<p>Up to 4,000,000 Common Units, Each Consisting of One Share of Common Stock and One Series B Warrant</p>
<p>Up to 500,000 Pre-Funded Warrants Included in the Pre-Funded Units</p>
<p>Up to 4,500,000 Shares of Common Stock underlying the Series B Warrants</p>
<p>Each Series B Warrant has an exercise price of $0.80 per share and expires five years from issuance.</p>
<p>Signed: Officer Name Placeholder</p></body></html>"""
PRIVATE_PLACEMENT_8K = ("Item 3.02 Unregistered Sales of Equity Securities. On October 12, 2026, the Company sold "
                        "1,000,000 shares and warrants to purchase up to 1,000,000 shares of common stock at an "
                        "exercise price of $1.10 per share in a private placement.")
SHELF_BASE = ("We may offer from time to time common stock, preferred stock, debt securities, warrants, rights and "
              "units. DESCRIPTION OF WARRANTS 12. We may issue warrants for the purchase of common stock.")
DILUTION_TABLE = ("The number of shares outstanding excludes: · 2,000 shares issuable upon exercise of outstanding "
                  "warrants with an exercise price of $150.00; · 5,000,000 shares of common stock issuable upon the "
                  "exercise of outstanding warrants, having a weighted average exercise price of $2.00 per share")


# --- reading the first pages ---------------------------------------------------------------------------

def test_units_with_pre_funded_and_series_warrants():
    facts = detect(UNITS_COVER)
    assert facts["sold"] == [{"kind": "pre-funded", "shares": 500_000, "exercise_price": None},
                             {"kind": "Series B", "shares": 4_500_000, "exercise_price": 0.80}]
    assert describe_sold(facts) == "pre-funded warrants for 500K shares, warrants for 4.5M shares (Series B) at $0.80"


def test_a_private_placement_with_warrants():
    facts = detect(PRIVATE_PLACEMENT_8K)
    assert facts["sold"] == [{"kind": "common", "shares": 1_000_000, "exercise_price": 1.10}]
    assert describe_sold(facts) == "warrants for 1M shares at $1.10"


def test_shelf_boilerplate_and_headings_are_not_warrants():
    assert detect(SHELF_BASE) is None
    assert detect("<p>An 8-K about a new office lease.</p>") is None


def test_outstanding_tranches_add_up_with_a_weighted_price_and_repeats_count_once():
    facts = detect(DILUTION_TABLE + " … " + DILUTION_TABLE)  # supplement and base prospectus repeat it
    assert facts["sold"] == [] and facts["outstanding"] == {"shares": 5_002_000, "exercise_price": 2.0592}
    assert describe_outstanding(facts) == "warrants for 5M shares outstanding, average exercise price $2.06"


def test_sold_warrants_are_only_looked_for_where_the_offering_is_described():
    late = "x " * 25_000 + "warrants to purchase up to 9,000,000 shares at an exercise price of $1.00"
    assert detect(late) is None  # far past the cover: e.g. a description of old warrants


def test_a_recording_keeps_the_facts_and_nothing_else():
    kept = digest(UNITS_COVER)
    assert "Officer Name Placeholder" not in kept and "Acme" not in kept
    assert detect(kept) == detect(UNITS_COVER)
    assert detect(digest("<p>nothing about warrants</p>")) is None


# --- the dilution profile -----------------------------------------------------------------------------

def candidate(form, day):
    return {"form": form, "filed": date.fromisoformat(day), "accession": f"x-{day}", "doc": "https://www.sec.gov/x"}


def test_warrants_sold_and_a_large_overhang_each_score_a_point():
    checked = [(candidate("8-K", "2026-09-21"), detect(PRIVATE_PLACEMENT_8K)),
               (candidate("424B5", "2026-06-01"), detect(DILUTION_TABLE)),
               (candidate("S-1", "2026-03-01"), None)]
    summary = warrant_summary(checked)
    assert summary["offerings_with_warrants"] == 1 and summary["outstanding"]["as_of"] == date(2026, 6, 1)
    history = {"pending": None, "shelf": None, "offerings_12m": [], "private_sales_12m": [],
               "delisting_notices_12m": [], "rights_changes_24m": []}
    shares = {"shares": 45_000_000, "change_pct": None}
    result = assess(history, None, shares, summary)
    texts = {r["text"]: r["points"] for r in result["reasons"]}
    assert texts == {
        "Warrants sold with 1 offering in the last 12 months; latest 8-K on Sep 21, 2026: warrants for 1M shares at $1.10": 1,
        "Warrants for 5,002,000 shares outstanding (11% of shares), average exercise price $2.06, per the 424B5 of Jun 1, 2026": 1,
    }
    small = assess(history, None, {"shares": 500_000_000, "change_pct": None}, summary)
    assert [r["points"] for r in small["reasons"]] == [1, 0]  # 1% of shares: noted, not scored


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(sec_module, "MIN_REQUEST_GAP", 0)


def test_the_profile_reads_recent_offerings_once_and_names_no_one():
    from test_dilution import AGENT, BURN, CASH, DILUTER, SHARES
    docs = []

    def handler(request):
        path = urlparse(str(request.url)).path
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme"}})
        if path == "/submissions/CIK0000000001.json":
            return httpx.Response(200, json=DILUTER)
        if path.endswith("CashAndCashEquivalentsAtCarryingValue.json"):
            return httpx.Response(200, json=CASH)
        if path.endswith("NetCashProvidedByUsedInOperatingActivities.json"):
            return httpx.Response(200, json=BURN)
        if path.endswith("EntityCommonStockSharesOutstanding.json"):
            return httpx.Response(200, json=SHARES)
        if path.endswith("/doc.htm"):
            docs.append(path)
            accession = path.split("/")[-2]
            text = {"000000000126000001": PRIVATE_PLACEMENT_8K, "000000000126000003": DILUTION_TABLE}.get(accession, "")
            return httpx.Response(200, text=f"<p>Signed by Officer Name Placeholder.</p><p>{text}</p>")
        return httpx.Response(404)

    client = SecClient(AGENT)
    client._client = httpx.AsyncClient(headers=client._client.headers, transport=httpx.MockTransport(handler))
    profiles = DilutionProfiles(client)

    async def go():
        try:
            first = await profiles.get("ACME")
            profiles._cache.clear()
            await profiles.get("ACME")  # rebuilt, but the filings aren't read again
            return first
        finally:
            await client.aclose()
    profile = asyncio.run(go())
    assert len(docs) == len(set(docs)) == 3  # the 3 newest offering-type filings, each read once
    assert profile["warrants"]["offerings_with_warrants"] == 1
    assert profile["warrants"]["outstanding"]["shares"] == 5_002_000
    assert any(r["text"].startswith("Warrants sold with 1 offering") for r in profile["reasons"])
    assert "Officer Name Placeholder" not in json.dumps(profile, default=str)


# --- filing alerts --------------------------------------------------------------------------------------

@pytest.fixture
def day(tmp_path):
    return Day(tmp_path)


def test_an_offering_with_warrants_says_so_in_its_alert(day):
    day.movers("09:45", gainers={"ACME": 45.0})
    day.filing("10:02", "424B5", "ACME", text=UNITS_COVER)
    day.filing("10:02", "8-K", "CRUX", items=[("3.02", "Unregistered Sales of Equity Securities")],
               text=PRIVATE_PLACEMENT_8K)
    day.movers("10:00", gainers={"ACME": 45.0}, small_caps={"CRUX": 30.0})
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "10:01", "10:05")
    try:
        bodies = {n["title"]: n["body"] for n in run.phone if n["category"] == "filings"}
        assert bodies["ACME · offering from shelf"].startswith(
            "Acme Rocket Corp: offering from shelf (424B5) · with warrants")
        assert bodies["CRUX · unregistered share sale"].startswith(
            "Crux Biotherapeutics Inc: unregistered share sale · with warrants")
        acme = run.news("Acme Rocket Corp: offering from shelf")
        assert acme["summary"].startswith("Includes pre-funded warrants for 500K shares, warrants for 4.5M shares")
        assert "warrants" in acme["tags"] and acme["filing"]["warrants"]["sold"][1]["exercise_price"] == 0.80
        assert "Officer Name Placeholder" not in json.dumps(run.browser("news"))
    finally:
        run.close()


def test_an_unreadable_document_doesnt_hold_the_filing_back(day):
    day.movers("09:45", gainers={"ACME": 45.0})
    day.filing("10:02", "424B5", "ACME")  # no document recorded: the first pages can't be read
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:46", "10:05")
    try:
        assert run.phone_titles("filings") == ["ACME · offering from shelf"]
        assert run.news("offering from shelf")["filing"]["warrants"] is None
    finally:
        run.close()


# --- warrants in the money ------------------------------------------------------------------------------

# Prices in a scenario day sit around $10 (Day.quotes), so these warrants are priced near it.
NEAR_TEN = """<p>Up to 2,000,000 Common Units, Each Consisting of One Share of Common Stock and One Series C Warrant</p>
<p>Up to 300,000 Pre-Funded Warrants</p>
<p>Up to 2,000,000 Shares of Common Stock underlying the Series C Warrants</p>
<p>Each Series C Warrant has an exercise price of $11.00 per share.</p>
<p>The number of shares outstanding excludes 6,000,000 shares of common stock issuable upon the exercise of
outstanding warrants, having a weighted average exercise price of $12.50 per share.</p>"""


def test_levels_list_each_exercise_price_newest_first_without_pre_funded():
    checked = [(candidate("424B4", "2026-10-09"), detect(UNITS_COVER)),
               (candidate("8-K", "2026-09-21"), detect(PRIVATE_PLACEMENT_8K)),
               (candidate("424B5", "2026-06-01"), detect(DILUTION_TABLE)),
               (candidate("424B5", "2026-05-01"), detect(UNITS_COVER.replace("$0.80", "$0.95")))]  # restated
    levels = warrant_summary(checked)["levels"]
    assert [(lv["kind"], lv["price"], lv["shares"], lv["form"], lv["filed"]) for lv in levels] == [
        ("Series B", 0.80, 4_500_000, "424B4", date(2026, 10, 9)),
        ("common", 1.10, 1_000_000, "8-K", date(2026, 9, 21)),
        ("outstanding", 2.0592, 5_002_000, "424B5", date(2026, 6, 1)),
    ]


def test_warrants_a_registration_statement_proposes_arent_levels_yet_but_outstanding_ones_are():
    checked = [(candidate("S-1", "2026-10-02"), detect(UNITS_COVER + DILUTION_TABLE))]
    assert [lv["kind"] for lv in warrant_summary(checked)["levels"]] == ["outstanding"]


def test_the_risk_summary_carries_the_levels_for_the_badge():
    from app.dilution import summary
    levels = [{"kind": "Series B", "shares": 4_500_000, "price": 0.8, "form": "424B4", "filed": date(2026, 10, 9)}]
    profile = {"level": "high", "points": 5, "reasons": [], "warrants": {"levels": levels}}
    assert summary(profile)["warrant_levels"] == [{**levels[0], "filed": "2026-10-09"}]
    assert summary({"level": "low", "points": 0, "reasons": [], "warrants": None})["warrant_levels"] == []


def test_crossing_a_warrant_price_alerts_once_a_day_and_the_first_price_is_only_a_baseline(day, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day.profile("ACME", "high", offering_text=NEAR_TEN)
    day.profile("CRUX", "high", offering_text=NEAR_TEN)
    day.quotes("09:30", ACME=12.0, CRUX=0.0)  # ACME starts above $11: in the money, but no crossing seen
    day.quotes("09:40", ACME=12.0, CRUX=5.0)
    day.quotes("09:50", ACME=12.0, CRUX=12.0)   # CRUX crosses $11.00
    day.quotes("10:00", ACME=12.0, CRUX=8.0)    # back under
    day.quotes("10:10", ACME=26.0, CRUX=13.0)   # CRUX again (no repeat); ACME crosses $12.50
    run = Run(day, watchlist=["ACME", "CRUX"]).steps("09:31", "09:41", "09:51", "10:01", "10:11")
    try:
        levels = run.engine.risk["CRUX"]["warrant_levels"]
        assert [(lv["kind"], lv["price"]) for lv in levels] == [("Series C", 11.0), ("outstanding", 12.5)]
        fired = [(n["at"], n["title"], n["body"]) for n in run.phone if "warrants" in n["title"]]
        assert fired == [
            ("09:51", "CRUX · warrants in the money",
             ("CRUX crossed $11.00 (now $11.20), the Series C warrants' exercise price: 2M shares can now be "
              "exercised at a profit (per the 424B5 of Aug 1, 2026)")),
            ("10:11", "ACME · warrants in the money",
             ("ACME crossed $12.50 (now $12.60), the outstanding warrants' average exercise price: 6M shares can "
              "now be exercised at a profit (per the 424B5 of Aug 1, 2026)")),
        ]
        assert {(n["category"], n["priority"], n["tag"]) for n in run.phone if "warrants" in n["title"]} == {
            ("price", "high", "warning")}  # switched with the phone's price alerts
        assert [a["symbol"] for a in run.browser("alerts") if a["kind"] == "warrant"] == ["CRUX", "ACME"]
    finally:
        run.close()


def test_a_mover_off_the_watchlist_alerts_too(day, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day.profile("ACME", "high", offering_text=NEAR_TEN)
    day.movers("09:30", gainers={"ACME": 5.0})
    day.movers("09:45", gainers={"ACME": 9.0})
    day.movers("10:00", gainers={"ACME": 30.0})  # through $11.00 and $12.50 in one step: both levels
    run = Run(day, watchlist=["BOLT"]).steps("09:31", "09:46", "10:01")
    try:
        assert [n["body"][:32] for n in run.phone if "warrants" in n["title"]] == [
            "ACME crossed $11.00 (now $13.00)", "ACME crossed $12.50 (now $13.00)"]
    finally:
        run.close()


def test_a_watchlist_stock_goes_by_its_own_quote_not_the_movers_delayed_price(day, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day.profile("ACME", "high", offering_text=NEAR_TEN)
    day.quotes("09:30", ACME=5.0)
    day.movers("09:30", gainers={"ACME": 5.0})
    day.movers("09:45", gainers={"ACME": 15.0})  # a different price for the same stock: not used
    run = Run(day, watchlist=["ACME"]).steps("09:31", "09:41", "09:46")
    try:
        assert [n for n in run.phone if "warrants" in n["title"]] == []
    finally:
        run.close()


def test_a_stock_already_at_the_exercise_price_at_its_first_reading_hasnt_crossed_it(day, monkeypatch):
    monkeypatch.setattr("app.sec.MIN_REQUEST_GAP", 0)
    day.profile("ACME", "high", offering_text=NEAR_TEN)
    day.quotes("09:30", ACME=10.0)  # exactly $11.00
    day.quotes("09:40", ACME=11.0)
    run = Run(day, watchlist=["ACME"]).steps("09:31", "09:32", "09:41")  # 09:31 reads the profile
    try:
        assert [n for n in run.phone if "warrants" in n["title"]] == []
    finally:
        run.close()
