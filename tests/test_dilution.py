import asyncio
import json
from datetime import date
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app import sec as sec_module
from app.dilution import (
    DilutionProfiles,
    assess,
    cash_runway,
    filing_history,
    share_growth,
)
from app.health import SourceError
from app.sec import SecClient

AGENT = "test-agent/1.0 ops@example.org"
TODAY = date(2026, 10, 12)
# Made up, standing in for the contact details SEC's responses carry.
PERSONAL = ["1 PRIVATE LANE", "555-0100", "JANE Q EXAMPLE"]


def submissions(rows, cik="0000000001"):
    """A data.sec.gov submissions response; rows are (form, filed, items)."""
    return {
        "cik": cik, "name": "Acme Corp", "phone": "555-0100",
        "addresses": {"mailing": {"street1": "1 PRIVATE LANE"}},
        "filings": {"recent": {
            "form": [r[0] for r in rows],
            "filingDate": [r[1] for r in rows],
            "items": [r[2] if len(r) > 2 else "" for r in rows],
            "accessionNumber": [f"0000000001-26-{n:06d}" for n in range(len(rows))],
            "primaryDocument": ["doc.htm"] * len(rows),
        }, "files": []},
    }


def concept(unit, facts):
    return {"units": {unit: facts}}


DILUTER = submissions([
    ("S-1", "2026-10-09"),
    ("8-K", "2026-09-21", "1.01,3.02,3.03,5.03,9.01"),
    ("4", "2026-08-01"),  # an insider's Form 4: not dilution, never passed on
    ("424B5", "2026-06-01"),
    ("424B5", "2026-06-01"),  # an ATM supplement filed twice the same day counts once
    ("8-K", "2026-05-01", "3.01,9.01"),
    ("424B4", "2026-01-15"),
    ("EFFECT", "2025-03-10"),
    ("S-3", "2025-03-01"),
    ("424B5", "2024-01-01"),  # more than a year ago
])
CASH = concept("USD", [{"end": "2025-12-31", "val": 6_811_000, "filed": "2026-03-02"},
                       {"end": "2026-06-30", "val": 4_960_000, "filed": "2026-08-14"}])
BURN = concept("USD", [{"start": "2026-01-01", "end": "2026-03-31", "val": -2_468_000},
                       {"start": "2026-01-01", "end": "2026-06-30", "val": -6_728_000}])
SHARES = concept("shares", [{"end": "2025-08-08", "val": 9_000_000}, {"end": "2025-11-06", "val": 10_636_135},
                            {"end": "2026-08-10", "val": 45_109_497}])


def test_filing_history_finds_shelf_pending_offering_and_8k_signals():
    h = filing_history(DILUTER, TODAY)
    assert h["shelf"] == {"form": "S-3", "filed": date(2025, 3, 1), "effective": True, "expires": date(2028, 2, 29)}
    assert h["pending"] == {"form": "S-1", "filed": date(2026, 10, 9)}
    assert h["offerings_12m"] == [date(2026, 6, 1), date(2026, 1, 15)]
    assert h["private_sales_12m"] == [date(2026, 9, 21)]
    assert h["delisting_notices_12m"] == [date(2026, 5, 1)]
    assert h["rights_changes_24m"] == [date(2026, 9, 21)]
    assert [(e["form"], e["label"]) for e in h["events"]][:3] == [
        ("S-1", "offering registration"),
        ("8-K", "unregistered share sale, shareholder rights change"),
        ("424B5", "offering from shelf"),
    ]
    assert all(e["form"] != "4" for e in h["events"])
    assert h["events"][0]["url"] == "https://www.sec.gov/Archives/edgar/data/1/000000000126000000/0000000001-26-000000-index.htm"


def test_a_priced_or_withdrawn_registration_is_not_pending():
    priced = submissions([("424B4", "2026-10-10"), ("S-1/A", "2026-10-01"), ("S-1", "2026-09-01")])
    withdrawn = submissions([("RW", "2026-10-10"), ("S-1", "2026-09-01")])
    resale = submissions([("424B3", "2026-06-01"), ("EFFECT", "2026-05-30"), ("S-1", "2026-05-08")])
    assert filing_history(resale, TODAY)["pending"] is None
    assert filing_history(priced, TODAY)["pending"] is None
    assert filing_history(withdrawn, TODAY)["pending"] is None
    assert filing_history(submissions([("S-3", "2026-01-01")]), TODAY)["shelf"]["effective"] is False


def test_cash_runway_from_the_latest_cash_and_year_to_date_burn():
    r = cash_runway(CASH, BURN, TODAY)
    assert r["cash"] == 4_960_000 and r["as_of"] == date(2026, 6, 30) and r["currency"] == "USD"
    assert r["burn_months"] == 6 and round(r["monthly_burn"]) == 1_137_780  # $6.73M over 5.9 months
    # 4.39 months of cash on Jun 30, 3.45 months gone since.
    assert r["months_left"] == pytest.approx(0.94, abs=0.02)

    positive = concept("USD", [{"start": "2026-01-01", "end": "2026-06-30", "val": 1_000}])
    assert cash_runway(CASH, positive, TODAY)["monthly_burn"] == 0
    assert cash_runway(CASH, positive, TODAY)["months_left"] is None
    assert cash_runway(concept("USD", [{"end": "2024-01-01", "val": 1}]), BURN, TODAY) is None  # stale
    assert cash_runway(None, BURN, TODAY) is None


def test_share_growth_compares_with_about_a_year_earlier():
    g = share_growth(SHARES, TODAY)
    assert g["shares"] == 45_109_497 and g["year_ago"] == 9_000_000
    assert g["change_pct"] == pytest.approx(401.2, abs=0.1)


def test_a_serial_diluter_scores_high_with_every_reason_listed():
    result = assess(filing_history(DILUTER, TODAY), cash_runway(CASH, BURN, TODAY), share_growth(SHARES, TODAY))
    assert result["level"] == "high" and result["points"] == 11
    texts = [r["text"] for r in result["reasons"]]
    assert texts[0] == "S-1 filed Oct 9, 2026, not effective yet: an offering is being prepared"
    assert "About 1 month of cash left at the last reported burn rate" in texts
    assert ("Share count up 401% in about a year (counted Aug 10, 2026, before the Sep 21, 2026 "
            "shareholder-rights change; not split-adjusted)") in texts
    assert "2 offerings in the last 12 months, latest Jun 1, 2026" in texts
    assert texts[-1].startswith("Shareholder-rights change (8-K item 3.03")


def test_a_cash_generating_company_without_a_shelf_scores_low():
    history = filing_history(submissions([("10-Q", "2026-08-01"), ("8-K", "2026-07-30", "2.02,9.01")]), TODAY)
    positive = concept("USD", [{"start": "2026-01-01", "end": "2026-06-30", "val": 5_000_000}])
    shares = concept("shares", [{"end": "2025-07-20", "val": 1_000_000}, {"end": "2026-07-20", "val": 990_000}])
    result = assess(history, cash_runway(CASH, positive, TODAY), share_growth(shares, TODAY))
    assert result == {"level": "low", "points": -1, "reasons": [
        {"points": -1, "text": "Operating cash flow is positive: no cash burn to fund"}]}


# --- profiles over HTTP --------------------------------------------------------------------

class FakeData:
    def __init__(self):
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        path = urlparse(str(request.url)).path
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme Corp"}})
        if path == "/submissions/CIK0000000001.json":
            return httpx.Response(200, json=DILUTER)
        if path.endswith("/us-gaap/CashAndCashEquivalentsAtCarryingValue.json"):
            return httpx.Response(404)  # reports the next concept instead
        if path.endswith("/us-gaap/CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents.json"):
            return httpx.Response(200, json=CASH)
        if path.endswith("/NetCashProvidedByUsedInOperatingActivities.json"):
            return httpx.Response(200, json=BURN)
        if path.endswith("/EntityCommonStockSharesOutstanding.json"):
            return httpx.Response(200, json=SHARES)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(sec_module, "MIN_REQUEST_GAP", 0)


def make_profiles(fake):
    client = SecClient(AGENT)
    client._client = httpx.AsyncClient(headers=client._client.headers, follow_redirects=False,
                                       transport=httpx.MockTransport(fake.handler))
    return DilutionProfiles(client)


def run(profiles, coro_fn):
    async def scenario():
        try:
            return await coro_fn()
        finally:
            await profiles.sec.aclose()
    return asyncio.run(scenario())


def test_profile_is_built_cached_and_carries_no_contact_details():
    fake = FakeData()
    profiles = make_profiles(fake)
    first, second, both, unknown = run(profiles, lambda: _gather(profiles))
    assert first["level"] == "high" and first["symbol"] == "ACME"
    assert first["cash"]["cash"] == 4_960_000  # from the fallback concept
    assert first["sec_url"].endswith("CIK=1&owner=include&count=40")
    assert second is first  # cached
    assert both[0] is both[1] and both[0] == first  # concurrent lookups share one rebuild
    assert unknown is None
    data_requests = [r for r in fake.requests if r.url.host == "data.sec.gov"]
    assert len(data_requests) == 2 * 5  # submissions + 4 concept tries, for 2 builds across 4 lookups
    assert {r.url.host for r in fake.requests} <= {"www.sec.gov", "data.sec.gov"}
    assert all(r.headers["User-Agent"] == AGENT for r in fake.requests)
    text = json.dumps(first, default=str)
    assert not any(p in text for p in PERSONAL)
    assert "Acme Corp" not in text  # nothing from the raw response but the computed facts


async def _gather(profiles):
    first = await profiles.get("ACME")
    second = await profiles.get("acme")
    profiles._cache.clear()
    both = await asyncio.gather(profiles.get("ACME"), profiles.get("ACME"))
    return first, second, both, await profiles.get("SPY")


def test_profile_without_the_ticker_map_explains_why():
    fake = FakeData()
    fake.handler = lambda request: httpx.Response(403)
    profiles = make_profiles(fake)

    async def get():
        return await profiles.get("ACME")
    with pytest.raises(SourceError, match="sec_user_agent"):
        run(profiles, get)


# --- the API ---------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.engine.settings, "access_token", "")
    return TestClient(main.app, base_url="http://localhost")


def test_api_returns_the_profile_or_explains_why_not(client, monkeypatch):
    async def get(symbol):
        if symbol == "BAD":
            raise SourceError("SEC refused the request (403)")
        if symbol == "BOOM":
            raise RuntimeError("socket details nobody should see")
        return {"symbol": symbol, "level": "low"} if symbol == "ACME" else None
    monkeypatch.setattr(main.engine.dilution, "get", get)

    assert client.get("/api/dilution/acme").json() == {"symbol": "ACME", "level": "low"}
    assert client.get("/api/dilution/SPY").status_code == 404
    assert client.get("/api/dilution/BAD").json()["detail"] == "SEC refused the request (403)"
    boom = client.get("/api/dilution/BOOM")
    assert boom.status_code == 502 and "socket" not in boom.text
    assert client.get("/api/dilution/no%20way").status_code == 422


def test_without_financials_a_quiet_history_is_unknown_not_low():
    quiet = filing_history(submissions([("N-CSR", "2026-08-01")]), TODAY)
    result = assess(quiet, None, None)
    assert result["level"] == "unknown" and "No recent XBRL financials" in result["reasons"][-1]["text"]
    noisy = filing_history(submissions([("S-1", "2026-10-01"), ("424B4", "2026-03-01")]), TODAY)
    assert assess(noisy, None, None)["level"] == "medium"  # filings alone can still raise it


def test_usd_is_preferred_and_burn_uses_the_cash_currency():
    cash = {"units": {"TWD": [{"end": "2026-06-30", "val": 3_000}], "USD": [{"end": "2026-06-30", "val": 100}]}}
    flow = {"units": {"TWD": [{"start": "2026-01-01", "end": "2026-06-30", "val": -600}],
                      "USD": [{"start": "2026-01-01", "end": "2026-06-30", "val": -20}]}}
    r = cash_runway(cash, flow, TODAY)
    assert r["currency"] == "USD" and r["cash"] == 100 and round(r["monthly_burn"], 2) == round(20 / (180 / 30.44), 2)
    only_twd = cash_runway({"units": {"TWD": cash["units"]["TWD"]}}, flow, TODAY)
    assert only_twd["currency"] == "TWD" and round(only_twd["monthly_burn"]) == round(600 / (180 / 30.44))


def test_offerings_by_a_large_seasoned_issuer_count_only_through_its_share_count():
    history = filing_history(submissions([("424B5", "2026-06-17"), ("424B5", "2026-02-01"),
                                          ("S-3ASR", "2025-05-28")]), TODAY)
    positive = concept("USD", [{"start": "2026-01-01", "end": "2026-06-30", "val": 9_000_000}])
    flat = concept("shares", [{"end": "2025-08-01", "val": 1_000}, {"end": "2026-08-01", "val": 1_010}])
    result = assess(history, cash_runway(CASH, positive, TODAY), share_growth(flat, TODAY))
    assert result["level"] == "low"
    assert any("(likely debt)" in r["text"] and r["points"] == 0 for r in result["reasons"])

    issuing = concept("shares", [{"end": "2025-08-01", "val": 1_000}, {"end": "2026-08-01", "val": 1_600}])
    assert assess(history, None, share_growth(issuing, TODAY))["level"] == "medium"  # +60% still counts
