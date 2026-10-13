"""Record a live day's data, and replay it later to test alerts, the recap and the UI without
waiting for market hours.

    record:  set `record = true` (config.toml, Setup, or MARKET_PULSE_RECORD=1) and run as usual.
             Each run writes data/recordings/<date>_<time>.jsonl.gz; the newest `record_keep`
             are kept.
    replay:  python -m app.replay list
             python -m app.replay play data/recordings/2026-10-13_092500.jsonl.gz --from 15:45 --speed 10
             then open http://localhost:8001

What's recorded is what the data sources answered: Yahoo results, news and halt feeds, SEC
feeds and documents. Never the requests' headers, so your SEC contact address and tokens can't
end up in a file, and URLs lose any credential-like query parameter.

SEC insider (Form 4) and Schedule 13D documents name private people, often with addresses and
phone numbers. They're cut down when recorded to the fields the app reads, so no names
(except firms the app itself would show), addresses, signatures or free text are kept. Other
SEC responses keep only the fields the app uses.

A replay never touches the network: anything not in the recording fails as a data outage. It
never sends phone alerts, runs on its own copy of the recorded watchlist and settings in a
temporary folder, and shows a REPLAY banner with the replayed time.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import bisect
import gzip
import hashlib
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from .clock import clock
from .filings import EMBEDDED_XML, ENTITY_NAME, _find, _local, _text
from .health import SourceError
from .market import ET as ET_ZONE
from .warrants import digest as warrant_digest

log = logging.getLogger(__name__)

FORMAT = "market-pulse-recording"
SECRET_PARAM = re.compile(r"token|key|secret|pass|sig|auth", re.IGNORECASE)
DEFAULT_PORT = 8001
PER_SYMBOL_CALLS = ("fetch_quotes", "fetch_premarket", "fetch_earnings", "fetch_daily", "fetch_floats")  # Yahoo calls returning one row per symbol
START_AFTER_FIRST = 15  # seconds


class LiveIO:
    """Live data, untouched: the default."""

    recording: Path | None = None

    def transport(self, name: str) -> httpx.AsyncBaseTransport | None:
        return None

    async def call(self, name: str, fn: Callable, *args):
        return await asyncio.to_thread(fn, *args)

    def close(self) -> None:
        pass


# --- keys and privacy -----------------------------------------------------------------------

def url_key(request: httpx.Request) -> str:
    """Method and URL, minus any query parameter that looks like a credential."""
    url = urlparse(str(request.url))
    query = urlencode([(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if not SECRET_PARAM.search(k)])
    return f"{request.method} {urlunparse(url._replace(query=query, fragment=''))}"


def call_key(name: str, args: tuple) -> str:
    return f"call {name} {json.dumps(args, default=str)}"


def _xml(tag: str, children: list | None = None, text: str | None = None) -> ET.Element:
    el = ET.Element(tag)
    if text is not None:
        el.text = text
    el.extend(children or [])
    return el


def _value(tag: str, text: str) -> ET.Element:
    return _xml(tag, [_xml("value", text=text)])


def minimal_form4(doc: ET.Element) -> ET.Element:
    """A Form 4 with only what filings.parse_form4 reads: issuer, roles, 10b5-1 flag, and each
    non-derivative trade's code, shares, price and holding after. No names or addresses."""
    issuer = _find(doc, "issuer")
    owners = []
    for owner in (o for o in doc if _local(o.tag) == "reportingOwner"):
        rel = _find(owner, "reportingOwnerRelationship")
        flags = [_xml(name, text=_text(rel, name)) for name in
                 ("isDirector", "isOfficer", "isTenPercentOwner", "isOther", "officerTitle")]
        owners.append(_xml("reportingOwner", [_xml("reportingOwnerRelationship", flags)]))
    trades = []
    table = _find(doc, "nonDerivativeTable")
    for tx in (t for t in (table if table is not None else []) if _local(t.tag) == "nonDerivativeTransaction"):
        trades.append(_xml("nonDerivativeTransaction", [
            _xml("transactionCoding", [_xml("transactionCode", text=_text(tx, "transactionCoding", "transactionCode"))]),
            _xml("transactionAmounts", [
                _value("transactionShares", _text(tx, "transactionAmounts", "transactionShares", "value")),
                _value("transactionPricePerShare", _text(tx, "transactionAmounts", "transactionPricePerShare", "value")),
            ]),
            _xml("postTransactionAmounts", [_value("sharesOwnedFollowingTransaction", _text(
                tx, "postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"))]),
        ]))
    return _xml("ownershipDocument", [
        _xml("issuer", [_xml(name, text=_text(issuer, name)) for name in
                        ("issuerCik", "issuerName", "issuerTradingSymbol")]),
        *owners,
        _xml("aff10b5One", text=_text(doc, "aff10b5One")),
        _xml("nonDerivativeTable", trades),
    ])


def minimal_13d(doc: ET.Element) -> ET.Element:
    """A Schedule 13D with only what filings.parse_13d reads. A reporting person keeps a name
    only when the app would show it: a firm that isn't declared an individual."""
    form = _find(doc, "formData")
    persons = []
    group = _find(form, "reportingPersons") if form is not None else None
    for p in (p for p in (group if group is not None else []) if _local(p.tag) == "reportingPersonInfo"):
        kind, name = _text(p, "typeOfReportingPerson"), _text(p, "reportingPersonName")
        shown = name if kind != "IN" and ENTITY_NAME.search(name) else ""
        persons.append(_xml("reportingPersonInfo", [
            _xml("reportingPersonName", text=shown), _xml("percentOfClass", text=_text(p, "percentOfClass")),
            _xml("typeOfReportingPerson", text=kind)]))
    info = _find(form, "coverPageHeader", "issuerInfo") if form is not None else None
    return _xml("edgarSubmission", [
        _xml("headerData", [_xml("submissionType", text=_text(doc, "headerData", "submissionType"))]),
        _xml("formData", [
            _xml("coverPageHeader", [_xml("issuerInfo", [
                _xml("issuerCIK", text=_text(info, "issuerCIK")), _xml("issuerName", text=_text(info, "issuerName"))])]),
            _xml("reportingPersons", persons),
        ]),
    ])


def minimal_submission(text: str) -> str:
    """An EDGAR full-submission .txt cut to a minimal primary XML document. The SGML header
    (names and addresses of everyone involved) and every other document are dropped."""
    m = EMBEDDED_XML.search(text)
    if not m:
        return ""
    try:
        doc = ET.fromstring(m.group(1).encode())
    except ET.ParseError:
        return ""
    kind = _local(doc.tag)
    if kind == "ownershipDocument":
        kept = minimal_form4(doc)
    elif kind == "edgarSubmission" and _find(doc, "formData") is not None:
        kept = minimal_13d(doc)
    else:
        return ""
    return f"<XML>\n{ET.tostring(kept, encoding='unicode')}\n</XML>\n"


FEED_TITLE = re.compile(r"(<title>[^<]*? - )([^<]*?)( \(\d{10}\) \((?:Reporting|Filed by)\)</title>)")


def sanitize(url: str, body: bytes) -> bytes:
    """What gets written for an SEC response; other sources are public and kept as they are."""
    parsed = urlparse(url)
    host, path = parsed.hostname or "", parsed.path
    if host not in ("www.sec.gov", "data.sec.gov"):
        return body
    text = body.decode("utf-8", "replace")
    if path == "/cgi-bin/browse-edgar":
        # Feed entries name the insider or investor filing ("4 - DOE JANE (…) (Reporting)").
        return FEED_TITLE.sub(r"\1[redacted]\3", text).encode()
    if path.startswith("/Archives/"):
        # Insider and 13D filings: the fields the app reads. Any other filing document (an
        # offering, an 8-K): only the warrant facts found in it, never its text.
        if path.endswith(".txt") and (kept := minimal_submission(text)):
            return kept.encode()
        return warrant_digest(text).encode()
    if path.startswith("/submissions/"):
        data = json.loads(text)
        recent = data.get("filings", {}).get("recent", {})
        keep = {k: recent.get(k, []) for k in ("form", "filingDate", "items", "accessionNumber", "primaryDocument")}
        return json.dumps({"cik": data.get("cik"), "filings": {"recent": keep, "files": []}}).encode()
    if "/api/xbrl/companyconcept/" in path:
        return json.dumps({"units": json.loads(text).get("units", {})}).encode()
    if path == "/files/company_tickers.json":  # only what SecClient reads
        rows = json.loads(text)
        return json.dumps({k: {"cik_str": r["cik_str"], "ticker": r["ticker"]} for k, r in rows.items()}).encode()
    return b""  # anything else from SEC: not needed for a replay


# --- recording ------------------------------------------------------------------------------

FEED_ITEM = re.compile(r"<entry\b.*?</entry>|<item\b.*?</item>", re.DOTALL)


def split_feed(text: str) -> list[str] | None:
    """[head, gap, item, gap, item, ..., tail] for an Atom/RSS feed, or None for anything else.
    Joined back together it's the original text."""
    items = list(FEED_ITEM.finditer(text))
    if not items:
        return None
    parts, pos = [], 0
    for m in items:
        parts += [text[pos:m.start()], m.group(0)]
        pos = m.end()
    return [*parts, text[pos:]]


def _payload(status: int, ctype: str, body: bytes) -> dict:
    try:
        return {"s": status, "ct": ctype, "b": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"s": status, "ct": ctype, "b64": base64.b64encode(body).decode()}


class Recorder(LiveIO):
    """Live data, with what each source answers appended to a gzip'd JSON-lines file.

    Feeds are stored item by item: each entry is written once as a blob, and a response is a
    list of blob ids. A response whose items match the previous one for the same request isn't
    written again, even if its timestamps changed; that's what keeps a day to a few MB."""

    def __init__(self, folder: Path, state: dict, keep: int = 10):
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        prune(folder, keep - 1)
        self.recording = folder / f"{clock.now(ET_ZONE):%Y-%m-%d_%H%M%S}.jsonl.gz"
        fd = os.open(self.recording, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._file = gzip.GzipFile(fileobj=os.fdopen(fd, "wb"), mode="wb")
        self._last: dict[str, str] = {}
        self._blobs: set[str] = set()
        self._write({"format": FORMAT, "version": 1, "started": clock.time(), "state": state})

    def _write(self, entry: dict) -> None:
        self._file.write(json.dumps(entry, separators=(",", ":"), default=str).encode() + b"\n")
        self._file.flush()  # a crash loses at most the line being written

    def record(self, key: str, payload: dict) -> None:
        parts = split_feed(payload["b"]) if "b" in payload else None
        if parts is not None:
            ids = [self._blob(part) for part in parts]
            payload = {k: v for k, v in payload.items() if k != "b"} | {"parts": ids}
            # Feed heads and tails carry the time of the request: compare items only.
            digest = hashlib.sha1(json.dumps([payload["s"], ids[1:-1]]).encode()).hexdigest()
        else:
            digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        if self._last.get(key) == digest:
            return
        self._last[key] = digest
        self._write({"t": clock.time(), "k": key, **payload})

    def _blob(self, text: str) -> str:
        blob_id = hashlib.sha1(text.encode()).hexdigest()[:16]
        if blob_id not in self._blobs:
            self._blobs.add(blob_id)
            self._write({"blob": blob_id, "b": text})
        return blob_id

    def transport(self, name: str, inner: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncBaseTransport:
        return RecordingTransport(self, inner)

    async def call(self, name: str, fn: Callable, *args):
        result = await asyncio.to_thread(fn, *args)
        self.record(call_key(name, args), {"r": result})
        return result

    def close(self) -> None:
        self._file.close()


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, recorder: Recorder, inner: httpx.AsyncBaseTransport | None = None):
        self._recorder = recorder
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        body = await response.aread()  # decoded
        await response.aclose()
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding", "set-cookie")}
        ctype = response.headers.get("content-type", "")
        if response.status_code < 500:
            payload = _payload(response.status_code, ctype, sanitize(str(request.url), body))
            if response.is_redirect:  # e.g. PR Newswire's feed, so a replay can follow it too
                payload["loc"] = response.headers.get("location", "")
            self._recorder.record(url_key(request), payload)
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def prune(folder: Path, keep: int) -> None:
    """Delete all but the newest `keep` recordings."""
    files = sorted(folder.glob("*.jsonl.gz"))
    for old in files[:max(0, len(files) - keep)]:
        old.unlink()


# --- replay -----------------------------------------------------------------------------------

def read_lines(path: Path):
    """The lines of a recording, including one still being written or cut off by a crash:
    each line is flushed as it's written, so everything before the cut is readable."""
    with gzip.open(path, "rt") as f:
        try:
            yield from f
        except (EOFError, gzip.BadGzipFile):
            return


class Player(LiveIO):
    """Serves a recording at the replay clock's time: for each request, the latest answer
    recorded at or before now."""

    def __init__(self, path: Path):
        self.recording = path
        self._entries: dict[str, list[tuple[float, dict]]] = {}
        self._blobs: dict[str, str] = {}
        lines = read_lines(path)
        header = json.loads(next(lines, "{}"))
        if header.get("format") != FORMAT:
            raise ValueError(f"{path.name} isn't a Market Pulse recording")
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                break  # the line being written when the recording stopped
            if "blob" in entry:
                self._blobs[entry["blob"]] = entry["b"]
                continue
            if "parts" in entry:
                entry["b"] = "".join(self._blobs.get(i, "") for i in entry.pop("parts"))
            self._entries.setdefault(entry.pop("k"), []).append((entry.pop("t"), entry))
        self.header = header
        self.started = header["started"]
        self.ended = max((t for items in self._entries.values() for t, _ in items), default=self.started)

    def start_at(self, hhmm: str | None) -> float:
        """Epoch seconds to start replaying at: HH:MM (ET) on the recorded day, or by default
        just after the first data arrived, so the first polls find answers."""
        if not hhmm:
            first = min((items[0][0] for items in self._entries.values()), default=self.started)
            return first + START_AFTER_FIRST
        day = datetime.fromtimestamp(self.started, ET_ZONE).date()
        hour, minute = (int(x) for x in hhmm.split(":"))
        return datetime.combine(day, dtime(hour, minute), ET_ZONE).timestamp()

    def at(self, key: str) -> dict | None:
        items = self._entries.get(key)
        if not items:
            return None
        i = bisect.bisect_right([t for t, _ in items], clock.time())
        return items[i - 1][1] if i else None

    def transport(self, name: str) -> httpx.AsyncBaseTransport:
        return ReplayTransport(self)

    async def call(self, name: str, fn: Callable, *args):
        if name in PER_SYMBOL_CALLS:
            return self._rows(name, args[0])
        entry = self.at(call_key(name, args))
        if entry is None:
            raise SourceError(f"{name}{list(args)} isn't in the recording at this time")
        return entry["r"]

    def _rows(self, name: str, symbols: list[str]) -> list[dict]:
        """The latest recorded row for each symbol, from any call of `name`: the symbols asked
        for change (the watchlist is edited, the gap scanner's candidates come and go)."""
        now, latest = clock.time(), {}
        for key, items in self._entries.items():
            if key.startswith(f"call {name} "):
                for t, entry in items:
                    if t <= now:
                        for row in entry["r"]:
                            if row["symbol"] in symbols and t >= latest.get(row["symbol"], (0, None))[0]:
                                latest[row["symbol"]] = (t, row)
        return [latest[s][1] for s in symbols if s in latest]


class ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, player: Player):
        self._player = player

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        entry = self._player.at(url_key(request))
        if entry is None:  # never recorded, or not yet at this time: offline, like an outage
            return httpx.Response(503, text="not in the recording", request=request)
        body = base64.b64decode(entry["b64"]) if "b64" in entry else entry["b"].encode()
        headers = {"content-type": entry["ct"]}
        if entry.get("loc"):
            headers["location"] = entry["loc"]
        return httpx.Response(entry["s"], headers=headers, content=body, request=request)


def replay_settings(settings):
    """Settings for a replay: nothing that sends or fetches anything for real."""
    return replace(settings, ntfy_topic="", telegram_bot_token="", telegram_chat_id="", finnhub_api_key="",
                   record=False)


# --- command line ------------------------------------------------------------------------------

def _describe(path: Path) -> str:
    player = Player(path)
    start = datetime.fromtimestamp(player.started, ET_ZONE)
    end = datetime.fromtimestamp(player.ended, ET_ZONE)
    return f"{path}  {start:%a %b %d %H:%M}–{end:%H:%M} ET  {path.stat().st_size / 1e6:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    from .config import STATE_PATH

    parser = argparse.ArgumentParser(prog="python -m app.replay", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list recordings")
    play = sub.add_parser("play", help="replay a recording")
    play.add_argument("file", type=Path)
    play.add_argument("--from", dest="start", metavar="HH:MM", help="start at this ET time (default: recording start)")
    play.add_argument("--speed", type=float, default=1.0, help="replay speed, e.g. 10 (default 1)")
    play.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    folder = STATE_PATH.parent / "recordings"
    if args.command == "list":
        files = sorted(folder.glob("*.jsonl.gz"))
        for f in files:
            print(_describe(f))
        if not files:
            print(f"No recordings in {folder}. Set record = true and run Market Pulse for a while.")
        return 0

    if not args.file.exists():
        print(f"No such recording: {args.file}", file=sys.stderr)
        return 1
    if args.start and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", args.start):
        print("--from takes HH:MM (ET)", file=sys.stderr)
        return 1
    if not 0.1 <= args.speed <= 1000:
        print("--speed must be between 0.1 and 1000", file=sys.stderr)
        return 1
    print(_describe(args.file))
    # app.main gives the replay its own throwaway data folder.
    os.environ["MARKET_PULSE_REPLAY"] = str(args.file.resolve())
    os.environ["MARKET_PULSE_REPLAY_SPEED"] = str(args.speed)
    os.environ["MARKET_PULSE_REPLAY_FROM"] = args.start or ""
    import uvicorn
    print(f"Replaying on http://localhost:{args.port}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
