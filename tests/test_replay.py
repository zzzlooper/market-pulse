import asyncio
import gzip
import json
import os
import stat
from datetime import datetime

import httpx
import pytest

from app import replay
from app.clock import clock
from app.config import Settings, UserState
from app.engine import Engine
from app.filings import embedded_xml, parse_13d, parse_form4
from app.health import SourceError
from app.market import ET
from app.replay import (
    Player,
    Recorder,
    ReplayTransport,
    minimal_submission,
    prune,
    replay_settings,
    sanitize,
    url_key,
)

# Made-up people and details: none of this may survive into a recording.
PERSONAL = ["JANE Q EXAMPLE", "1 PRIVATE LANE", "SPRINGFIELD", "555-0100", "John Placeholder",
            "Doe Family Trust", "jane signed", "Footnote naming Jane", "contact@mailbox.test"]
AGENT = "market-pulse/0.7 contact@mailbox.test"

FORM4 = """<SEC-DOCUMENT>
<SEC-HEADER>
REPORTING-OWNER:
	OWNER DATA:
		COMPANY CONFORMED NAME:			JANE Q EXAMPLE
	MAIL ADDRESS:
		STREET 1:		1 PRIVATE LANE
		CITY:			SPRINGFIELD
		BUSINESS PHONE:		555-0100
</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000000001</issuerCik><issuerName>Acme Corp</issuerName><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000099999</rptOwnerCik><rptOwnerName>JANE Q EXAMPLE</rptOwnerName></reportingOwnerId>
    <reportingOwnerAddress><rptOwnerStreet1>1 PRIVATE LANE</rptOwnerStreet1><rptOwnerCity>SPRINGFIELD</rptOwnerCity></reportingOwnerAddress>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>1</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>20000</value></transactionShares>
        <transactionPricePerShare><value>3.00</value><footnoteId id="F1"/></transactionPricePerShare></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>120000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <footnotes><footnote id="F1">Footnote naming Jane and her spouse</footnote></footnotes>
  <ownerSignature><signatureName>jane signed</signatureName></ownerSignature>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""

SCHEDULE_13D = """<SEC-DOCUMENT><DOCUMENT><XML>
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D">
  <headerData><submissionType>SCHEDULE 13D/A</submissionType>
    <!-- Field: Pseudo-Tag; ID: Name; Data: John Placeholder --></headerData>
  <formData>
    <coverPageHeader><issuerInfo><issuerCIK>0000000001</issuerCIK><issuerName>Acme Corp</issuerName></issuerInfo>
      <authorizedPersons><notificationInfo><personName>John Placeholder</personName>
        <personPhoneNum>555-0100</personPhoneNum></notificationInfo></authorizedPersons></coverPageHeader>
    <reportingPersons>
      <reportingPersonInfo><reportingPersonName>John Placeholder</reportingPersonName>
        <percentOfClass>9.1</percentOfClass><typeOfReportingPerson>IA</typeOfReportingPerson>
        <commentContent>Held through Doe Family Trust</commentContent></reportingPersonInfo>
      <reportingPersonInfo><reportingPersonName>Example Capital Partners LP</reportingPersonName>
        <percentOfClass>9.4</percentOfClass><typeOfReportingPerson>PN</typeOfReportingPerson></reportingPersonInfo>
    </reportingPersons>
    <items1To7><item2>John Placeholder of 1 PRIVATE LANE</item2></items1To7>
  </formData>
</edgarSubmission>
</XML></DOCUMENT></SEC-DOCUMENT>"""

FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>4 - JANE Q EXAMPLE (0000099999) (Reporting)</title><id>urn:a=1</id></entry>
<entry><title>4 - Acme Corp (0000000001) (Issuer)</title><id>urn:a=1</id></entry>
<entry><title>SCHEDULE 13D - John Placeholder (0000088888) (Filed by)</title><id>urn:a=2</id></entry>
<entry><title>424B5 - Acme Corp (0000000001) (Filer)</title><id>urn:a=3</id></entry>
</feed>"""


@pytest.fixture(autouse=True)
def real_clock():
    yield
    clock.reset()


# --- what a recording may keep ---------------------------------------------------------------

def test_form4_is_cut_to_what_the_app_reads():
    kept = minimal_submission(FORM4)
    assert not any(p in kept for p in PERSONAL)
    assert parse_form4(embedded_xml(kept)) == parse_form4(embedded_xml(FORM4))


def test_13d_keeps_firms_only_and_the_same_result():
    kept = minimal_submission(SCHEDULE_13D)
    assert not any(p in kept for p in PERSONAL)
    assert "Example Capital Partners LP" in kept
    assert parse_13d(embedded_xml(kept)) == parse_13d(embedded_xml(SCHEDULE_13D))


def test_other_sec_responses_keep_only_what_is_used():
    feed = sanitize("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4", FEED.encode()).decode()
    assert "JANE Q EXAMPLE" not in feed and "John Placeholder" not in feed
    assert "4 - Acme Corp (0000000001) (Issuer)" in feed and "424B5 - Acme Corp" in feed
    subs = json.loads(sanitize("https://data.sec.gov/submissions/CIK0000000001.json", json.dumps({
        "cik": "1", "name": "Acme", "phone": "555-0100", "addresses": {"mailing": {"street1": "1 PRIVATE LANE"}},
        "filings": {"recent": {"form": ["4"], "filingDate": ["2026-10-13"], "items": [""],
                               "accessionNumber": ["x"], "primaryDocument": ["d.htm"]}}}).encode()))
    assert subs == {"cik": "1", "filings": {"recent": {"form": ["4"], "filingDate": ["2026-10-13"], "items": [""],
                                                       "accessionNumber": ["x"], "primaryDocument": ["d.htm"]},
                                            "files": []}}
    assert sanitize("https://www.sec.gov/cgi-bin/own-disp?action=getowner", b"names") == b""
    assert sanitize("https://www.globenewswire.com/rss", b"<rss/>") == b"<rss/>"  # public: as is


def test_url_keys_drop_credential_like_parameters():
    request = httpx.Request("GET", "https://api.example.test/q?symbol=ACME&token=abc&apiKey=def&x=1")
    assert url_key(request) == "GET https://api.example.test/q?symbol=ACME&x=1"


# --- recording --------------------------------------------------------------------------------

def make_recorder(tmp_path, keep=10):
    return Recorder(tmp_path / "recordings", {"watchlist": ["ACME"], "keywords": ["FDA"]}, keep)


def lines(path):
    with gzip.open(path, "rt") as f:
        return [json.loads(line) for line in f]


def test_recording_is_private_dedupes_and_never_holds_request_headers(tmp_path):
    def upstream(request):
        if "Archives" in request.url.path:
            return httpx.Response(200, text=FORM4)
        return httpx.Response(200, text="<rss>same</rss>", headers={"set-cookie": "session=xyz"})
    rec = make_recorder(tmp_path)

    async def scenario():
        client = httpx.AsyncClient(headers={"User-Agent": AGENT, "Authorization": "Bearer secret-token-q8"},
                                   transport=rec.transport("sec", httpx.MockTransport(upstream)))
        for _ in range(3):  # identical answers are written once
            await client.get("https://www.globenewswire.com/rss")
        resp = await client.get("https://www.sec.gov/Archives/edgar/data/1/x/0001-26-1.txt")
        assert "JANE Q EXAMPLE" in resp.text  # the live app still gets the real answer
        await client.aclose()
        await rec.call("fetch_quotes", lambda symbols: [{"symbol": "ACME", "price": 3.0}], ["ACME"])
    asyncio.run(scenario())
    rec.close()

    assert stat.S_IMODE(os.stat(rec.recording).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(rec.recording.parent).st_mode) == 0o700
    entries = lines(rec.recording)
    assert entries[0]["format"] == "market-pulse-recording" and entries[0]["state"]["watchlist"] == ["ACME"]
    assert [e["k"] for e in entries[1:]] == ["GET https://www.globenewswire.com/rss",
                                             "GET https://www.sec.gov/Archives/edgar/data/1/x/0001-26-1.txt",
                                             'call fetch_quotes [["ACME"]]']
    with gzip.open(rec.recording, "rt") as f:
        raw = f.read()
    for leaked in [*PERSONAL, AGENT, "secret-token-q8", "session=xyz"]:
        assert leaked not in raw


def test_only_the_newest_recordings_are_kept(tmp_path):
    folder = tmp_path / "recordings"
    folder.mkdir()
    for day in range(10, 15):
        (folder / f"2026-10-{day}_093000.jsonl.gz").write_bytes(b"")
    prune(folder, 2)
    assert sorted(p.name for p in folder.iterdir()) == ["2026-10-13_093000.jsonl.gz", "2026-10-14_093000.jsonl.gz"]


# --- replay -------------------------------------------------------------------------------------

T0 = datetime(2026, 10, 13, 9, 30, tzinfo=ET).timestamp()


def write_recording(path, entries, state=None):
    with gzip.open(path, "wt") as f:
        f.write(json.dumps({"format": "market-pulse-recording", "version": 1, "started": T0,
                            "state": state or {"watchlist": ["ACME"]}}) + "\n")
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.write('{"t": 1, "k": "cut off mid-wri')  # a recording stopped while writing


def quotes(t, price):
    return {"t": t, "k": 'call fetch_quotes [["ACME", "ZZZ"]]',
            "r": [{"symbol": "ACME", "price": price}, {"symbol": "ZZZ", "price": 1.0}]}


def test_player_serves_what_was_recorded_at_the_replayed_time(tmp_path):
    path = tmp_path / "day.jsonl.gz"
    write_recording(path, [
        quotes(T0 + 10, 3.0), quotes(T0 + 600, 2.5),
        {"t": T0 + 5, "k": "GET https://feed.example.test/rss", "s": 200, "ct": "text/xml", "b": "<rss>9:30</rss>"},
        {"t": T0 + 5, "k": "GET https://feed.example.test/old", "s": 301, "ct": "", "b": "",
         "loc": "https://feed.example.test/rss"},
        {"t": T0 + 20, "k": 'call fetch_chart ["ACME", "1d"]', "r": {"bars": [1]}},
    ])
    player = Player(path)
    assert player.started == T0 and player.ended == T0 + 600

    async def at(minute_offset):
        clock.start_replay(T0 + minute_offset)
        client = httpx.AsyncClient(transport=ReplayTransport(player), follow_redirects=True)
        try:
            feed = await client.get("https://feed.example.test/old")
            missing = await client.get("https://feed.example.test/never")
            return (feed.status_code, feed.text, missing.status_code,
                    await player.call("fetch_quotes", None, ["ACME"]),
                    await player.call("fetch_chart", None, "ACME", "1d"))
        finally:
            await client.aclose()
    status, text, missing, q, chart = asyncio.run(at(300))
    assert (status, text, missing) == (200, "<rss>9:30</rss>", 503)
    assert q == [{"symbol": "ACME", "price": 3.0}] and chart == {"bars": [1]}
    assert asyncio.run(at(700))[3] == [{"symbol": "ACME", "price": 2.5}]

    async def not_yet():
        clock.start_replay(T0 + 1)
        await player.call("fetch_chart", None, "ACME", "1d")
    with pytest.raises(SourceError, match="isn't in the recording"):
        asyncio.run(not_yet())
    assert player.start_at("15:45") == datetime(2026, 10, 13, 15, 45, tzinfo=ET).timestamp()


def test_replay_clock_runs_at_its_speed():
    clock.start_replay(T0, speed=60)
    assert clock.replaying and abs(clock.time() - T0) < 5
    assert clock.real_seconds(30) == 0.5
    assert clock.now(ET).date().isoformat() == "2026-10-13"


def test_replay_settings_send_and_fetch_nothing_for_real():
    s = replay_settings(Settings(ntfy_topic="mp-topic-123456", telegram_bot_token="1:x", telegram_chat_id="5",
                                 finnhub_api_key="abc", record=True, access_token="keep-this-token"))
    assert (s.ntfy_topic, s.telegram_bot_token, s.telegram_chat_id, s.finnhub_api_key, s.record) == ("", "", "", "", False)
    assert s.access_token == "keep-this-token"  # the replay is protected like the live app


def test_engine_replays_prices_and_filings_offline_without_phone_alerts(tmp_path):
    feed = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>424B5 - Acme Corp (0000000001) (Filer)</title>
      <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"/>
      <updated>2026-10-13T09:40:00-04:00</updated><id>urn:tag:sec.gov,2008:accession-number=0000000001-26-000001</id>
      </entry></feed>"""
    entries = [quotes(T0, 3.0),
               {"t": T0, "k": "GET https://www.sec.gov/files/company_tickers.json", "s": 200, "ct": "application/json",
                "b": json.dumps({"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme Corp"}})}]
    from urllib.parse import quote

    from app.filings import FEED_TYPES, FEED_URL
    for t in FEED_TYPES:  # keys made the way the recorder makes them
        body = feed if t == "424B" else "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"
        key = url_key(httpx.Request("GET", FEED_URL.format(type=quote(t), start=0)))
        entries.append({"t": T0, "k": key, "s": 200, "ct": "application/atom+xml", "b": body})
    path = tmp_path / "day.jsonl.gz"
    write_recording(path, entries)
    player = Player(path)
    state = UserState(tmp_path / "state.json")
    state.watchlist = ["ACME", "ZZZ"]
    engine = Engine(replay_settings(Settings(ntfy_topic="mp-topic-123456")), state, None, io=player)
    sent = []
    engine.phone.deliver = lambda note: sent.append(note)

    class Hub:
        async def broadcast(self, msg):
            pass
    engine.hub = Hub()

    async def scenario():
        clock.start_replay(T0 + 60)
        try:
            await engine._update_quotes()
            fresh = await engine.filings.poll(["ACME"], {})
            return fresh
        finally:
            await engine.news.aclose()
            await engine.sec.aclose()
            await engine.halts.aclose()
            await engine.phone.aclose()
            engine.reactions.close()
    fresh = asyncio.run(scenario())
    assert [q["symbol"] for q in engine.yahoo_quotes] == ["ACME", "ZZZ"]
    assert [i["title"] for i in fresh] == ["Acme Corp: offering from shelf (424B5)"]
    assert engine.filings.failed == []  # every EDGAR feed was found in the recording
    assert engine.phone.channels == [] and sent == []
    assert engine.clock_info()["replay"] is True and engine.clock_info()["recording"] == "day.jsonl.gz"


# --- command line ---------------------------------------------------------------------------------

def test_cli_lists_and_validates(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("app.config.STATE_PATH", tmp_path / "state.json")
    assert replay.main(["list"]) == 0 and "No recordings" in capsys.readouterr().out
    path = tmp_path / "day.jsonl.gz"
    write_recording(path, [quotes(T0 + 3600, 3.0)])
    assert replay.main(["play", str(path), "--from", "4pm"]) == 1
    assert replay.main(["play", str(path), "--speed", "0"]) == 1
    assert replay.main(["play", str(tmp_path / "missing.jsonl.gz")]) == 1
    (tmp_path / "recordings").mkdir()
    path.rename(tmp_path / "recordings" / "day.jsonl.gz")
    replay.main(["list"])
    assert "Tue Oct 13 09:30–10:30 ET" in capsys.readouterr().out


def test_a_recording_cut_off_mid_write_still_plays(tmp_path):
    rec = make_recorder(tmp_path)
    rec.record('call fetch_chart ["ACME", "1d"]', {"r": {"bars": [1]}})
    # No close(): as after a crash, the gzip stream has no end marker.
    player = Player(rec.recording)
    clock.start_replay(player.ended + 1)
    assert asyncio.run(player.call("fetch_chart", None, "ACME", "1d")) == {"bars": [1]}
    rec.close()


def test_feeds_are_stored_item_by_item_and_rebuilt_exactly(tmp_path):
    from app.replay import split_feed
    def feed(stamp, *items):
        return (f"<feed><updated>{stamp}</updated>\n"
                + "\n".join(f"<entry><id>{i}</id></entry>" for i in items) + "\n</feed>")
    assert "".join(split_feed(feed("t1", 1, 2))) == feed("t1", 1, 2)
    assert split_feed('{"json": true}') is None

    rec = make_recorder(tmp_path)
    clock.start_replay(T0)
    key = "GET https://feed.example.test/atom"
    rec.record(key, {"s": 200, "ct": "application/atom+xml", "b": feed("09:30:00", 1, 2)})
    rec.record(key, {"s": 200, "ct": "application/atom+xml", "b": feed("09:30:30", 1, 2)})   # only the time moved
    clock.start_replay(T0 + 60)
    rec.record(key, {"s": 200, "ct": "application/atom+xml", "b": feed("09:31:00", 3, 1, 2)})  # a new item
    rec.close()
    written = lines(rec.recording)
    responses = [e for e in written if e.get("k") == key]
    item_blobs = [e for e in written if "blob" in e and "<entry>" in e["b"]]
    assert len(responses) == 2 and len(item_blobs) == 3  # each item stored once

    player = Player(rec.recording)
    clock.start_replay(T0 + 30)
    assert player.at(key)["b"] == feed("09:30:00", 1, 2)
    clock.start_replay(T0 + 90)
    assert player.at(key)["b"] == feed("09:31:00", 3, 1, 2)


def test_a_replay_never_uses_the_real_data_folder(tmp_path, monkeypatch):
    from app import config, main
    path = tmp_path / "day.jsonl.gz"
    write_recording(path, [quotes(T0, 3.0)], state={"watchlist": ["ACME", "ZZZ"], "keywords": ["FDA"]})
    monkeypatch.setattr(main, "REPLAY", str(path))
    engine = main.build_engine()
    try:
        real = config.STATE_PATH.parent.resolve()
        assert real not in engine.state.path.resolve().parents
        assert engine.reactions.path.parent == engine.dashboard.path.parent == engine.state.path.parent
        assert engine.state.watchlist == ["ACME", "ZZZ"] and engine.phone.channels == []
        engine.state.save()  # e.g. a ticker added during the replay
        assert not (real / "state.json").exists() or (real / "state.json").stat().st_mtime < T0
    finally:
        async def close():
            await engine.news.aclose()
            await engine.sec.aclose()
            await engine.halts.aclose()
            await engine.phone.aclose()
            engine.reactions.close()
        asyncio.run(close())
        import shutil
        shutil.rmtree(engine.state.path.parent, ignore_errors=True)



def test_other_filing_documents_keep_only_their_warrant_facts():
    from app.warrants import detect
    cover = ("<p>Acme Rocket Corp. Signed: JANE Q EXAMPLE, Chief Executive Officer, 1 PRIVATE LANE.</p>"
             "<p>Up to 1,000,000 Shares of Common Stock underlying the Series A Warrants, exercise price of $1.25</p>")
    kept = sanitize("https://www.sec.gov/Archives/edgar/data/1/000000000126000001/acme-424b5.htm", cover.encode()).decode()
    assert "JANE" not in kept and "PRIVATE" not in kept and "Acme" not in kept
    assert detect(kept) == detect(cover)  # a replay sees the same warrants
    plain = sanitize("https://www.sec.gov/Archives/edgar/data/1/000000000126000002/0001-26-2.txt",
                     b"<p>An 8-K about a new office lease, signed by JANE Q EXAMPLE.</p>").decode()
    assert "JANE" not in plain and detect(plain) is None
