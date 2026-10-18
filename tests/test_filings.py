import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import sec as sec_module
from app.config import Settings, load_settings
from app.engine import Engine
from app.filings import (
    FilingsFeed,
    embedded_xml,
    insider_item,
    parse_13d,
    parse_feed_title,
    parse_form4,
)
from app.health import SourceError
from app.notify import from_filing
from app.sec import SecClient

# Everything below is made up. These strings stand in for the personal details real filings
# carry, and must never reach an item, a title or the phone.
PERSONAL = ["JANE Q EXAMPLE", "1 PRIVATE LANE", "SPRINGFIELD", "555-0100", "John Placeholder", "Doe Family Trust"]
AGENT = "test-agent/1.0 ops@example.org"
TICKERS = {"0000000001": "ACME", "0000000002": "AAPL", "0000000003": "ZZZ", "0000000004": "BANK"}


def entry(form, name, cik, role, acc, updated="2026-10-12T10:00:00-04:00"):
    folder = acc.replace("-", "")
    return f"""<entry><title>{form} - {name} ({cik}) ({role})</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{acc}-index.htm"/>
<summary type="html">Filed</summary><updated>{updated}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="{form}"/>
<id>urn:tag:sec.gov,2008:accession-number={acc}</id></entry>"""


def feed(*entries):
    return ('<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">'
            f'<title>Latest Filings</title>{"".join(entries)}</feed>')


def submission(xml):
    return f"<SEC-DOCUMENT>\n<SEC-HEADER>\nCOMPANY CONFORMED NAME: JANE Q EXAMPLE\n</SEC-HEADER>\n" \
           f"<DOCUMENT>\n<TYPE>4\n<TEXT>\n<XML>\n{xml}\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>"


def form4(ticker, issuer, cik, trades, officer="Chief Executive Officer", plan=False):
    rows = "".join(f"""
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{"A" if code in "PA" else "D"}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>""" for code, shares, price, after in trades)
    return submission(f"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>{cik}</issuerCik><issuerName>{issuer}</issuerName><issuerTradingSymbol>{ticker}</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000099999</rptOwnerCik><rptOwnerName>JANE Q EXAMPLE</rptOwnerName></reportingOwnerId>
    <reportingOwnerAddress><rptOwnerStreet1>1 PRIVATE LANE</rptOwnerStreet1><rptOwnerCity>SPRINGFIELD</rptOwnerCity></reportingOwnerAddress>
    <reportingOwnerRelationship><isDirector>false</isDirector><isOfficer>true</isOfficer>
      <isTenPercentOwner>false</isTenPercentOwner><officerTitle>{officer}</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>{"1" if plan else "0"}</aff10b5One>
  <nonDerivativeTable>{rows}</nonDerivativeTable>
</ownershipDocument>""")


def schedule_13d(issuer, issuer_cik, persons, amendment=False):
    infos = "".join(f"""<reportingPersonInfo><reportingPersonName>{name}</reportingPersonName>
        <percentOfClass>{pct}</percentOfClass><typeOfReportingPerson>{kind}</typeOfReportingPerson></reportingPersonInfo>"""
                    for name, kind, pct in persons)
    return submission(f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D" xmlns:com="http://www.sec.gov/edgar/common">
  <headerData><submissionType>SCHEDULE 13D{"/A" if amendment else ""}</submissionType></headerData>
  <formData>
    <coverPageHeader><issuerInfo><issuerCIK>{issuer_cik}</issuerCIK><issuerName>{issuer}</issuerName></issuerInfo>
      <authorizedPersons><notificationInfo><personName>John Placeholder</personName>
        <personPhoneNum>555-0100</personPhoneNum></notificationInfo></authorizedPersons>
    </coverPageHeader>
    <reportingPersons>{infos}</reportingPersons>
  </formData>
</edgarSubmission>""")


class FakeSec:
    """Serves EDGAR feeds and documents from dicts, and records every request."""

    def __init__(self):
        self.feeds: dict[str, list[str]] = {}   # feed type -> entries, newest first
        self.docs: dict[str, str | int] = {}    # accession -> submission text, or an HTTP status
        self.requests: list[httpx.Request] = []
        self.tickers_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlparse(str(request.url))
        if url.path == "/files/company_tickers.json":
            rows = {str(n): {"cik_str": int(cik), "ticker": t, "title": t} for n, (cik, t) in enumerate(TICKERS.items())}
            return httpx.Response(self.tickers_status, json=rows)
        if url.path == "/cgi-bin/browse-edgar":
            q = parse_qs(url.query)
            start = int(q["start"][0])
            return httpx.Response(200, text=feed(*self.feeds.get(q["type"][0], [])[start:start + 100]))
        accession = url.path.rsplit("/", 1)[-1].removesuffix(".txt")
        doc = self.docs.get(accession, 404)
        return httpx.Response(doc, text="") if isinstance(doc, int) else httpx.Response(200, text=doc)


def make_sec(handler):
    client = SecClient(AGENT)
    client._client = httpx.AsyncClient(headers=client._client.headers, follow_redirects=False,
                                       transport=httpx.MockTransport(handler))
    return client


def make_feed(fake, **settings):
    # A lambda, so a test can swap fake.handler after the feed is made.
    return FilingsFeed(Settings(sec_user_agent=AGENT, **settings), make_sec(lambda r: fake.handler(r)))


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(sec_module, "MIN_REQUEST_GAP", 0)


def run(feed_, *polls):
    """Run polls in order; each is (watchlist, movers). Returns each poll's fresh items."""
    async def scenario():
        try:
            return [await feed_.poll(w, m) for w, m in polls]
        finally:
            await feed_.sec.aclose()
    return asyncio.run(scenario())


# --- parsing ---------------------------------------------------------------------------

def test_parse_feed_title():
    assert parse_feed_title("4 - Acme Corp (0000000001) (Issuer)") == ("4", "Acme Corp", "0000000001", "Issuer")
    assert parse_feed_title("SCHEDULE 13D/A - Example Fund - Series A LP (0000000005) (Filed by)") == (
        "SCHEDULE 13D/A", "Example Fund - Series A LP", "0000000005", "Filed by")
    assert parse_feed_title("Latest Filings") is None


def test_form4_buy_totals_lots_at_their_average_price_and_reads_no_personal_details():
    doc = embedded_xml(form4("ACME", "Acme Corp", "0000000001",
                             [("P", 10_000, 2.00, 110_000), ("P", 30_000, 3.00, 140_000)], plan=True))
    trade = parse_form4(doc)
    assert trade["ticker"] == "ACME" and trade["role"] == "CEO" and trade["plan"] is True
    assert trade["buy"] == {"shares": 40_000, "value": 110_000, "price": 2.75, "owned_after": 140_000}
    assert trade["sell"] is None
    assert not any(p in json.dumps(trade) for p in PERSONAL)

    _, title, summary = insider_item(trade, "buy")
    assert title == "Acme Corp: CEO bought 40,000 shares at $2.75 ($110K)"
    assert "Holds 140,000 shares after (+40%)." in summary and "10b5-1" in summary


def test_form4_ignores_awards_and_option_exercises():
    trade = parse_form4(embedded_xml(form4("ACME", "Acme Corp", "0000000001",
                                           [("A", 5_000, 0, 5_000), ("M", 1_000, 1.0, 6_000)])))
    assert trade["buy"] is None and trade["sell"] is None


def test_form4_symbol_none_is_not_a_ticker():
    assert parse_form4(embedded_xml(form4("NONE", "Private Co", "0000000009", [("P", 1, 1, 1)])))["ticker"] == ""


def test_13d_names_firms_but_never_people_even_when_their_type_is_misdeclared():
    stake = parse_13d(embedded_xml(schedule_13d("Acme Corp", "0000000001", [
        ("John Placeholder", "IA", 9.1),            # a person filing as an adviser
        ("Doe Family Trust", "OO", 9.1),            # a person's trust
        ("Example Capital Partners LP", "PN", 9.4),
    ], amendment=True)))
    assert stake == {"issuer": "Acme Corp", "issuer_cik": "0000000001", "filer": "Example Capital Partners LP",
                     "percent": 9.4, "amendment": True}
    alone = parse_13d(embedded_xml(schedule_13d("Acme Corp", "0000000001", [("John Placeholder", "IA", 21.7)])))
    assert alone["filer"] is None and alone["percent"] == 21.7


# --- the feed ----------------------------------------------------------------------------

def test_first_poll_is_a_baseline_and_later_filings_alert_for_watchlist_and_movers():
    sec = FakeSec()
    sec.feeds["424B"] = [entry("424B5", "Acme Corp", "0000000001", "Filer", "0000000001-26-000001")]
    feed_ = make_feed(sec)

    def second_poll():
        sec.feeds["424B"] = [
            entry("424B5", "Acme Corp", "0000000001", "Filer", "0000000001-26-000002"),
            entry("424B2", "Bank Notes LLC", "0000000004", "Filer", "0000000004-26-000001"),  # structured notes
            *sec.feeds["424B"],
        ]
        sec.feeds["S-1"] = [entry("S-1", "Unlisted IPO Inc", "0000000077", "Filer", "0000000077-26-000001")]
        sec.feeds["4"] = [
            entry("4", "Zzz Inc", "0000000003", "Issuer", "0000000003-26-000001"),
            entry("4", "EXAMPLE JANE Q", "0000099999", "Reporting", "0000000003-26-000001"),
            entry("4", "Zzz Inc", "0000000003", "Issuer", "0000000003-26-000002"),
            entry("4", "Apple Inc", "0000000002", "Issuer", "0000000002-26-000001"),
            entry("4", "Acme Corp", "0000000001", "Issuer", "0000000001-26-000003"),
        ]
        sec.docs["0000000003-26-000001"] = form4("ZZZ", "Zzz Inc", "0000000003", [("P", 20_000, 3.0, 120_000)])
        sec.docs["0000000003-26-000002"] = form4("ZZZ", "Zzz Inc", "0000000003", [("P", 100, 3.0, 100)])  # too small
        sec.docs["0000000002-26-000001"] = form4("AAPL", "Apple Inc", "0000000002", [("S", 1_000, 250.0, 9_000)])
        sec.docs["0000000001-26-000003"] = form4("ACME", "Acme Corp", "0000000001", [("S", 90_000, 3.0, 0)])
        sec.feeds["SCHEDULE 13D"] = [
            entry("SCHEDULE 13D", "PLACEHOLDER JOHN", "0000088888", "Filed by", "0000088888-26-000001"),
            entry("SCHEDULE 13D", "Acme Corp", "0000000001", "Subject", "0000088888-26-000001"),
        ]
        sec.docs["0000088888-26-000001"] = schedule_13d("Acme Corp", "0000000001", [("John Placeholder", "IA", 12.5)])

    async def scenario():
        try:
            first = await feed_.poll(["AAPL"], {})
            second_poll()
            second = await feed_.poll(["AAPL"], {"ACME": "top gainer +45%"})
            return first, second
        finally:
            await feed_.sec.aclose()
    first, second = asyncio.run(scenario())

    assert [(i["title"], i["alert"]) for i in first] == [("Acme Corp: offering from shelf (424B5)", False)]
    by_title = {i["title"]: i for i in second}
    assert set(by_title) == {
        "Acme Corp: offering from shelf (424B5)",
        "Zzz Inc: CEO bought 20,000 shares at $3.00 ($60K)",
        "Apple Inc: CEO sold 1,000 shares at $250.00 ($250K)",
        "An investor disclosed a stake in Acme Corp: 12.5%",
    }  # no 424B2, unlisted S-1, tiny buy, or sale outside the watchlist
    offering = by_title["Acme Corp: offering from shelf (424B5)"]
    assert offering["alert"] and offering["filing"] == {
        "kind": "dilution", "form": "424B5", "label": "offering from shelf", "hot": "top gainer +45%",
        "warrants": None}
    assert offering["sentiment"]["label"] == "bearish" and offering["tickers"] == ["ACME"]
    assert by_title["Apple Inc: CEO sold 1,000 shares at $250.00 ($250K)"]["filing"]["hot"] == "watchlist"
    assert not by_title["Zzz Inc: CEO bought 20,000 shares at $3.00 ($60K)"]["alert"]  # not watched, not moving
    assert not any(p.lower() in json.dumps(second).lower() for p in PERSONAL)
    assert "PLACEHOLDER" not in json.dumps(second)

    # Each Form 4 is fetched once although it appears under both the issuer and the insider.
    docs = [r.url.path for r in sec.requests if r.url.path.endswith(".txt")]
    assert len(docs) == len(set(docs)) == 7  # 5 insider/13D documents + the 2 offerings' first pages (warrants)


def test_the_contact_address_only_goes_to_sec_gov():
    sec = FakeSec()
    sec.feeds["424B"] = [entry("424B4", "Acme Corp", "0000000001", "Filer", "0000000001-26-000001")]
    feed_ = make_feed(sec)
    run(feed_, (["AAPL"], {}))
    assert sec.requests and all(r.url.host == "www.sec.gov" for r in sec.requests)
    assert all(r.headers["User-Agent"] == AGENT for r in sec.requests)
    assert feed_.sec._client.follow_redirects is False

    async def elsewhere():
        f = make_feed(FakeSec())
        try:
            await f.sec.get("https://example.org/anything")
        finally:
            await f.sec.aclose()
    with pytest.raises(ValueError):
        asyncio.run(elsewhere())


def test_a_refused_request_explains_the_user_agent_without_echoing_it():
    sec = FakeSec()
    sec.handler = lambda request: httpx.Response(403)
    feed_ = make_feed(sec)
    with pytest.raises(SourceError) as err:
        run(feed_, (["AAPL"], {}))
    assert "sec_user_agent" in str(err.value) and AGENT not in str(err.value)


def test_a_document_that_fails_is_retried_and_a_backlog_filing_still_does_not_alert():
    sec = FakeSec()
    sec.feeds["4"] = [entry("4", "Apple Inc", "0000000002", "Issuer", "0000000002-26-000001")]
    sec.docs["0000000002-26-000001"] = 500
    feed_ = make_feed(sec)

    async def scenario():
        try:
            first = await feed_.poll(["AAPL"], {})
            sec.docs["0000000002-26-000001"] = form4("AAPL", "Apple Inc", "0000000002", [("P", 1_000, 250.0, 2_000)])
            return first, await feed_.poll(["AAPL"], {})
        finally:
            await feed_.sec.aclose()
    first, second = asyncio.run(scenario())
    assert first == []
    assert [(i["tickers"], i["alert"]) for i in second] == [(["AAPL"], False)]


def test_a_burst_bigger_than_a_page_is_read_from_the_next_page():
    sec = FakeSec()
    old = entry("424B5", "Acme Corp", "0000000001", "Filer", "0000000001-26-000999")
    sec.feeds["424B"] = [old]
    feed_ = make_feed(sec)

    async def scenario():
        try:
            await feed_.poll([], {})
            sec.feeds["424B"] = [entry("424B5", "Acme Corp", "0000000001", "Filer", f"0000000001-26-{n:06d}")
                                 for n in range(150)] + [old]
            return await feed_.poll([], {})
        finally:
            await feed_.sec.aclose()
    assert len(asyncio.run(scenario())) == 150


# --- phone, engine, settings ----------------------------------------------------------------

def item(kind="dilution", hot="top gainer +45%"):
    return {"id": "x", "title": "Acme Corp: offering from shelf (424B5)", "url": "https://www.sec.gov/x-index.htm",
            "tickers": ["ACME"], "source": "SEC filings", "published": "2026-10-12T14:00:00+00:00",
            "alert": True, "filing": {"kind": kind, "form": "424B5", "label": "offering from shelf", "hot": hot}}


def test_phone_message_says_why_a_filing_matters():
    note = from_filing(item(), "http://192.168.1.20:8000/")
    assert note == {"category": "filings", "title": "ACME · offering from shelf",
                    "body": "Acme Corp: offering from shelf (424B5) (top gainer +45%)",
                    "url": "http://192.168.1.20:8000/#ticker=ACME", "priority": "high", "tag": "warning"}
    assert from_filing(item(hot="watchlist"))["body"] == "Acme Corp: offering from shelf (424B5)"
    assert from_filing(item(hot="watchlist"))["url"] == "https://www.sec.gov/x-index.htm"


class RecordingHub:
    def __init__(self):
        self.messages = []

    async def broadcast(self, msg):
        self.messages.append(msg)


def test_engine_sends_filings_to_the_news_feed_and_the_phone(tmp_path):
    from app.config import UserState
    state = UserState(tmp_path / "state.json")
    hub = RecordingHub()
    engine = Engine(Settings(ntfy_topic="mp-test"), state, hub)  # a phone channel to route to
    engine.movers["gainers"] = [{"symbol": "ACME", "change_pct": 45.2}]
    sent, seen = [], {}

    async def poll(watchlist, movers):
        seen.update(movers)
        return [item(), {**item(), "id": "y", "alert": False}]
    engine.filings.poll = poll
    engine.phone.enqueue = lambda notes, categories: sent.extend(notes)

    async def profile(symbol):  # no network in tests
        return {"level": "high", "points": 6, "reasons": [{"points": 2, "text": "S-1 pending"}]}
    engine.dilution.get = profile

    async def scenario():
        try:
            await engine._update_filings()
        finally:
            await engine.news.aclose()
            await engine.sec.aclose()
            await engine.halts.aclose()
            await engine.phone.aclose()
    asyncio.run(scenario())
    assert seen == {"ACME": "top gainer +45%"}
    assert [m["type"] for m in hub.messages] == ["risk", "news", "alerts", "alert_counts"]
    logged = hub.messages[2]["data"]  # the alerting filing, in the alert log too, with why it matters
    assert [(a["symbol"], a["kind"]) for a in logged] == [("ACME", "filing")]
    assert logged[0]["message"] == "ACME Acme Corp: offering from shelf (424B5) (top gainer +45%, dilution risk HIGH)"
    assert list(engine.alerts.recent) == logged
    assert hub.messages[0]["data"] == {"ACME": {"level": "high", "points": 6, "reasons": ["S-1 pending"], "warrant_levels": [], "offering_levels": [], "reverse_split": None}}
    assert len(hub.messages[1]["data"]) == 2 and hub.messages[1]["data"][0]["filing"]["risk"] == "high"
    assert [n["category"] for n in sent] == ["filings"]
    assert sent[0]["body"].endswith("(top gainer +45%, dilution risk HIGH)")
    assert engine.state.phone_alerts["filings"] is True


def test_filings_can_be_turned_off(tmp_path):
    from app.config import UserState
    engine = Engine(Settings(sec_filings=False), UserState(tmp_path / "s.json"), RecordingHub())
    assert engine.filings is None and "filings" not in engine.health


def test_sec_user_agent_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", AGENT)
    assert load_settings(tmp_path / "missing.toml").sec_user_agent == AGENT


def test_nothing_is_marked_seen_while_the_ticker_map_is_missing(monkeypatch):
    monkeypatch.setattr(sec_module, "TICKER_MAP_RETRY", 0)
    sec = FakeSec()
    sec.feeds["424B"] = [entry("424B5", "Acme Corp", "0000000001", "Filer", "0000000001-26-000001")]
    sec.tickers_status = 503
    feed_ = make_feed(sec)

    async def scenario():
        try:
            with pytest.raises(SourceError, match="ticker map unavailable"):
                await feed_.poll([], {})
            sec.tickers_status = 200
            return await feed_.poll([], {})
        finally:
            await feed_.sec.aclose()
    assert [i["tickers"] for i in asyncio.run(scenario())] == [["ACME"]]


# --- 8-K items ----------------------------------------------------------------------------

def eight_k(name, cik, acc, items):
    """An 8-K feed entry; EDGAR lists the items in the summary."""
    text = "".join(f"&lt;br&gt;Item {i}: {desc}" for i, desc in items)
    return entry("8-K", name, cik, "Filer", acc).replace(
        "<summary type=\"html\">Filed</summary>",
        f"<summary type=\"html\">&lt;b&gt;Filed:&lt;/b&gt; 2026-10-12{text}</summary>")


def test_8k_items_are_labelled_and_notable_ones_alert_for_movers():
    sec = FakeSec()
    feed_ = make_feed(sec)

    async def scenario():
        try:
            await feed_.poll([], {})  # baseline
            sec.feeds["8-K"] = [
                eight_k("Acme Corp", "0000000001", "0000000001-26-000010",
                        [("3.01", "Notice of Delisting or Failure to Satisfy a Continued Listing Rule"),
                         ("9.01", "Financial Statements and Exhibits")]),
                eight_k("Zzz Inc", "0000000003", "0000000003-26-000010",
                        [("2.02", "Results of Operations and Financial Condition"), ("9.01", "Exhibits")]),
                eight_k("Apple Inc", "0000000002", "0000000002-26-000010", [("7.01", "Regulation FD Disclosure")]),
                eight_k("Private Co", "0000000077", "0000000077-26-000010", [("3.02", "Unregistered Sales")]),
            ]
            return await feed_.poll(["AAPL"], {"ACME": "top loser -30%", "ZZZ": "top gainer +12%"})
        finally:
            await feed_.sec.aclose()
    items = {i["tickers"][0]: i for i in asyncio.run(scenario())}

    assert set(items) == {"ACME", "ZZZ", "AAPL"}  # the unlisted filer is skipped
    acme = items["ACME"]
    assert acme["title"] == "Acme Corp: delisting notice"
    assert acme["filing"] == {"kind": "event", "form": "8-K", "label": "delisting notice", "hot": "top loser -30%"}
    assert acme["alert"] and acme["sentiment"]["label"] == "bearish"
    assert acme["summary"].startswith("The exchange says the company breaks a listing rule")
    assert "Item 3.01: Notice of Delisting" in acme["summary"]
    # A routine 8-K on a mover doesn't alert; on the watchlist it does, as before.
    assert items["ZZZ"]["title"] == "Zzz Inc: earnings" and not items["ZZZ"]["alert"]
    assert items["ZZZ"]["filing"]["kind"] == "report" and items["ZZZ"]["filing"]["hot"] is None
    assert items["AAPL"]["alert"] and items["AAPL"]["filing"]["hot"] == "watchlist"


def test_describe_8k_prefers_notable_then_most_negative_items():
    from app.filings import describe_8k
    lead, known = describe_8k(["1.01", "3.02", "3.03", "9.01"])
    assert lead.label == "unregistered share sale"
    assert [i.label for i in known] == ["material agreement", "unregistered share sale", "shareholder rights change"]
    assert describe_8k(["9.01"])[0].label == "8-K"
