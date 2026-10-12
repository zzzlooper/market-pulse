"""SEC filings that move stocks: offerings (dilution), insider buys and sells (Form 4) and
activist stakes (Schedule 13D), from EDGAR's current-filings feeds.

They become news items, so they show in the news panel and the ticker detail. They alert
when the company is on the watchlist or on today's movers lists: an offering filed right
after a run-up is the classic case.

Privacy: Form 4 and 13D filings name the insiders and individual investors behind them, often
with home addresses and phone numbers. Only roles ("CEO", "Director"), share counts, prices
and stake sizes are read here. A 13D filer is named only when it is an entity (a fund or a
company); individual people are never named, stored or sent to the phone.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import feedparser
import httpx

from . import sentiment
from .config import Settings
from .health import SourceError
from .news import FAILING_AFTER, _published

log = logging.getLogger(__name__)

SOURCE = "SEC filings"
FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={type}&count=100&start={start}&output=atom"
# EDGAR's type filter matches form families: "424B" returns 424B1…424B8, "4" returns 4 and 4/A.
FEED_TYPES = ("424B", "S-1", "S-3", "F-1", "F-3", "4", "SCHEDULE 13D")
MAX_PAGES = 3          # a page that is all new may mean a burst; read up to this many
MIN_REQUEST_GAP = 0.125  # SEC's fair-access limit is 10 requests/s; stay under it
MAX_ATTEMPTS = 3       # a filing whose document keeps failing to download is skipped after this
MAX_SEEN = 20_000

# "4 - Acme Corp (0000123456) (Issuer)", "SCHEDULE 13D/A - Some Fund LP (0000654321) (Filed by)"
TITLE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$")
ACCESSION = re.compile(r"accession-number=([\d-]+)")
EMBEDDED_XML = re.compile(r"<XML>\s*(.*?)\s*</XML>", re.DOTALL)
TICKER = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")
# A 13D filer is named only if its name looks like a firm. The declared reporting-person type
# can't be relied on: people file as "IA" or "OO" too. Trusts and foundations are left out,
# since they are often a person's ("Jane Doe Revocable Trust").
ENTITY_NAME = re.compile(
    r"\b(?:L\.?L\.?C|L\.?L\.?P|L\.?P|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|FUNDS?|"
    r"CAPITAL|PARTNERS|MANAGEMENT|HOLDINGS?|GROUP|ADVIS[EO]RS?|INVESTMENTS?|VENTURES|BANK|SECURITIES|"
    r"ASSET|AG|S\.?A|N\.?V|B\.?V|GMBH|PLC|PTE)\b\.?", re.IGNORECASE)


@dataclass(frozen=True)
class FormInfo:
    label: str
    explain: str
    score: float  # fixed sentiment: what the filing usually means for the share price


SHELF = "Lets the company sell shares later without another filing. No shares are sold yet."
DILUTION = {
    "S-1": FormInfo("offering registration", "Registers new shares to sell; an offering usually prices within days or weeks.", -0.5),
    "S-1/A": FormInfo("amended offering registration", "An offering is being prepared; amendments often come just before pricing.", -0.5),
    "S-1MEF": FormInfo("offering upsized", "Registers extra shares on top of an S-1, usually on pricing day.", -0.6),
    "S-3": FormInfo("shelf registration", SHELF, -0.35),
    "S-3/A": FormInfo("amended shelf registration", SHELF, -0.35),
    "S-3ASR": FormInfo("automatic shelf registration", "Effective at once for large companies: shares can be sold any time.", -0.35),
    "424B1": FormInfo("offering priced", "Final prospectus: the number of shares and the price are set.", -0.6),
    "424B3": FormInfo("prospectus supplement", "Often a resale of shares by existing holders, or an update to an offering.", -0.35),
    "424B4": FormInfo("offering priced", "Final prospectus: the number of shares and the price are set.", -0.6),
    "424B5": FormInfo("offering from shelf", "Shares are being sold off a shelf: a public offering, registered direct or ATM program.", -0.6),
    "424B7": FormInfo("resale prospectus", "Existing holders register shares to sell.", -0.35),
}
# Foreign issuers file F-1/F-3, the same forms under another name.
DILUTION |= {"F" + form[1:]: info for form, info in DILUTION.items() if form.startswith("S-")}
# 424B2 is left out on purpose: banks file thousands of them a day for structured notes.

INSIDER_BUY = FormInfo("insider buy", "Open-market purchase reported on Form 4.", 0.6)
INSIDER_SELL = FormInfo("insider sale", "Sale reported on Form 4.", -0.2)
PLAN_NOTE = " Made under a pre-arranged 10b5-1 trading plan, so it says less about the insider's view."
STAKE = FormInfo("13D stake", "Schedule 13D: an investor with more than 5% who may push for changes "
                 "such as board seats or a sale of the company.", 0.0)
STAKE_UPDATE = FormInfo("13D update", "Amended Schedule 13D: the holder's stake or plans changed.", 0.0)

TITLE_ABBREVIATIONS = {
    "chief executive officer": "CEO", "chief financial officer": "CFO", "chief operating officer": "COO",
    "chief technology officer": "CTO", "chief medical officer": "CMO", "chief scientific officer": "CSO",
}


def parse_feed_title(title: str) -> tuple[str, str, str, str] | None:
    """(form, name, cik, role) from an EDGAR current-filings entry title."""
    m = TITLE.match(title.strip())
    return (m["form"], m["name"], m["cik"], m["role"]) if m else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: ET.Element, *path: str) -> ET.Element | None:
    """Follow child tags by local name, ignoring XML namespaces."""
    for name in path:
        node = next((c for c in node if _local(c.tag) == name), None)
        if node is None:
            return None
    return node


def _text(node: ET.Element | None, *path: str) -> str:
    found = _find(node, *path) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def _number(node: ET.Element | None, *path: str) -> float | None:
    try:
        return float(_text(node, *path).replace(",", ""))
    except ValueError:
        return None


def _flag(node: ET.Element | None, *path: str) -> bool:
    return _text(node, *path).lower() in ("1", "true")


def embedded_xml(submission: str) -> ET.Element | None:
    """The primary XML document inside an EDGAR full-submission .txt file."""
    m = EMBEDDED_XML.search(submission)
    if not m:
        return None
    try:
        return ET.fromstring(m.group(1).encode())
    except ET.ParseError:
        return None


def _role(owner: ET.Element) -> str:
    rel = _find(owner, "reportingOwnerRelationship")
    title = " ".join(_text(rel, "officerTitle").split())
    if _flag(rel, "isOfficer") and title:
        short = TITLE_ABBREVIATIONS.get(title.lower(), title)
        return short if len(short) <= 30 else short[:29] + "…"
    if _flag(rel, "isDirector"):
        return "Director"
    if _flag(rel, "isOfficer"):
        return "Officer"
    if _flag(rel, "isTenPercentOwner"):
        return "10% owner"
    return "Insider"


def _trades(doc: ET.Element, code: str) -> dict | None:
    """Total shares and value of non-derivative trades with this transaction code."""
    shares = value = 0.0
    priced = 0.0  # shares that came with a price, for the average
    owned_after = None
    table = _find(doc, "nonDerivativeTable")
    for tx in table if table is not None else []:
        if _local(tx.tag) != "nonDerivativeTransaction" or _text(tx, "transactionCoding", "transactionCode") != code:
            continue
        n = _number(tx, "transactionAmounts", "transactionShares", "value") or 0.0
        price = _number(tx, "transactionAmounts", "transactionPricePerShare", "value")
        shares += n
        if price:
            value += n * price
            priced += n
        owned_after = _number(tx, "postTransactionAmounts", "sharesOwnedFollowingTransaction", "value")
    if not shares:
        return None
    return {"shares": shares, "value": value, "price": value / priced if priced else None, "owned_after": owned_after}


def parse_form4(doc: ET.Element) -> dict | None:
    """Open-market buys (code P) and sales (code S) from a Form 4 ownership document.
    Awards, option exercises, tax withholding and gifts are ignored. Names and addresses
    are never read."""
    if _local(doc.tag) != "ownershipDocument":
        return None
    owners = [o for o in doc if _local(o.tag) == "reportingOwner"]
    roles = list(dict.fromkeys(_role(o) for o in owners)) or ["Insider"]
    ticker = _text(doc, "issuer", "issuerTradingSymbol").upper()
    return {
        "ticker": ticker if TICKER.match(ticker) and ticker != "NONE" else "",
        "issuer_cik": _text(doc, "issuer", "issuerCik").zfill(10),
        "issuer": _text(doc, "issuer", "issuerName"),
        "role": " & ".join(roles[:2]),
        "plan": _flag(doc, "aff10b5One"),
        "buy": _trades(doc, "P"),
        "sell": _trades(doc, "S"),
    }


def parse_13d(doc: ET.Element) -> dict | None:
    """Stake size and filer from a structured Schedule 13D. filer is set only for a firm:
    individuals are never named (see ENTITY_NAME)."""
    form = _find(doc, "formData")
    if form is None:
        return None
    group = _find(form, "reportingPersons")
    persons = [p for p in (group if group is not None else []) if _local(p.tag) == "reportingPersonInfo"]
    entities = [name for p in persons
                if _text(p, "typeOfReportingPerson") != "IN"
                and ENTITY_NAME.search(name := _text(p, "reportingPersonName"))]
    percents = [v for p in persons if (v := _number(p, "percentOfClass")) is not None]
    return {
        "issuer": _text(form, "coverPageHeader", "issuerInfo", "issuerName"),
        "issuer_cik": _text(form, "coverPageHeader", "issuerInfo", "issuerCIK").zfill(10),
        "filer": next((e for e in entities if e), None),
        # Group members each report the group's holding, so the largest is the group's.
        "percent": max(percents) if percents else None,
        "amendment": _text(doc, "headerData", "submissionType").endswith("/A"),
    }


def _trim(value: float, places: int) -> str:
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def _money(value: float) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= size:
            return f"${_trim(value / size, 1)}{suffix}"
    return f"${value:,.0f}"


def _shares(n: float) -> str:
    return f"{_trim(n / 1e6, 2)}M" if n >= 1e6 else f"{n:,.0f}"


def _sentiment(info: FormInfo) -> dict:
    label = ("bullish" if info.score >= sentiment.THRESHOLD else
             "bearish" if info.score <= -sentiment.THRESHOLD else "neutral")
    return {"score": info.score, "label": label, "terms": [f"{info.label} (SEC filing)"]}


def insider_item(trade: dict, side: str) -> tuple[FormInfo, str, str]:
    """(info, title, summary) for one side ("buy"/"sell") of a parsed Form 4."""
    t = trade[side]
    info = INSIDER_BUY if side == "buy" else INSIDER_SELL
    verb = "bought" if side == "buy" else "sold"
    price = f" at ${t['price']:,.2f}" if t["price"] else ""
    value = f" ({_money(t['value'])})" if t["value"] else ""
    title = f"{trade['issuer']}: {trade['role']} {verb} {_shares(t['shares'])} shares{price}{value}"
    summary = info.explain
    if (after := t["owned_after"]) is not None:
        summary += f" Holds {_shares(after)} shares after"
        before = after - t["shares"] if side == "buy" else after + t["shares"]
        if before > 0:
            summary += f" ({(after / before - 1) * 100:+.0f}%)."
        else:
            summary += " (a new position)." if side == "buy" else "."
    if trade["plan"]:
        summary += PLAN_NOTE
    return info, title, summary


class FilingsFeed:
    """Polls EDGAR's current-filings feeds and turns relevant filings into news items."""

    def __init__(self, settings: Settings, ticker_map: Callable[[], Awaitable[dict[str, str]]]):
        self.settings = settings
        self._ticker_map = ticker_map
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._seen: OrderedDict[str, None] = OrderedDict()  # accession numbers already handled
        self._attempts: dict[str, int] = {}
        self._was_primed: dict[str, bool] = {}  # for filings being retried: new, or backlog?
        self._primed: set[str] = set()  # feed types fetched once; their backlog never alerts
        self.failed: list[str] = []
        self._fail_streak: dict[str, int] = {}
        self._gate = asyncio.Lock()
        self._next_request = 0.0
        # Only ever used for www.sec.gov, the one place the contact address in the User-Agent
        # should go. No redirects, so it can't be carried to another host.
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=False,
                                         headers={"User-Agent": settings.sec_user_agent})

    async def aclose(self) -> None:
        await self._client.aclose()

    def latest(self) -> list[dict]:
        return list(self._items.values())

    @property
    def failing(self) -> list[str]:
        return [name for name, streak in self._fail_streak.items() if streak >= FAILING_AFTER]

    async def poll(self, watchlist: list[str], movers: dict[str, str]) -> list[dict]:
        """Fetch every feed once and return the new filings worth showing, newest first.
        movers maps a symbol on today's movers lists to why it's there, e.g. "top gainer +45%"."""
        tickers = await self._ticker_map()
        if not tickers:
            # Without it every offering would look unlisted and be dropped for good.
            raise SourceError("SEC ticker map unavailable, retrying")
        results = await asyncio.gather(*(self._entries(t) for t in FEED_TYPES), return_exceptions=True)
        self.failed = []
        groups: dict[str, list[tuple[str, str, str, str, dict]]] = {}
        primed: dict[str, bool] = {}
        for feed_type, entries in zip(FEED_TYPES, results):
            name = f"EDGAR {feed_type}"
            if isinstance(entries, BaseException):
                log.warning("filings feed %s failed: %s", feed_type, entries)
                self.failed.append(name)
                self._fail_streak[name] = self._fail_streak.get(name, 0) + 1
                continue
            self._fail_streak.pop(name, None)
            for entry in entries:
                parsed = parse_feed_title(entry.get("title", ""))
                accession = _accession(entry)
                if parsed and accession and accession not in self._seen:
                    groups.setdefault(accession, []).append((*parsed, entry))
                    primed[accession] = self._was_primed.pop(accession, feed_type in self._primed)
            self._primed.add(feed_type)
        if len(self.failed) == len(FEED_TYPES):
            raise SourceError(_failure_reason(results))

        built = await asyncio.gather(*(self._build(acc, rows, tickers) for acc, rows in groups.items()),
                                     return_exceptions=True)
        watch = set(watchlist)
        fresh = []
        for accession, item in zip(groups, built):
            if isinstance(item, BaseException):
                log.warning("filing %s failed: %s", accession, item)
                self._attempts[accession] = self._attempts.get(accession, 0) + 1
                if self._attempts[accession] < MAX_ATTEMPTS:
                    self._was_primed[accession] = primed[accession]
                    continue  # retried next poll
                item = None
            self._attempts.pop(accession, None)
            self._seen[accession] = None
            if item is None:
                continue
            ticker = item["tickers"][0]
            # Insider sales are routine; only the watchlist's are worth a line.
            if item["filing"]["kind"] == "insider_sell" and ticker not in watch:
                continue
            hot = "watchlist" if ticker in watch else movers.get(ticker)
            item["filing"]["hot"] = hot
            item["alert"] = primed[accession] and hot is not None
            self._items[item["id"]] = item
            fresh.append(item)

        while len(self._items) > self.settings.news_history:
            self._items.popitem(last=False)
        while len(self._seen) > MAX_SEEN:
            self._seen.popitem(last=False)
        return sorted(fresh, key=lambda n: n["published"], reverse=True)

    async def _get(self, url: str) -> httpx.Response:
        if urlparse(url).hostname != "www.sec.gov":
            raise ValueError("filings client only talks to www.sec.gov")
        async with self._gate:  # space request starts out under SEC's rate limit
            wait = self._next_request - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_request = time.monotonic() + MIN_REQUEST_GAP
        resp = await self._client.get(url)
        if resp.status_code == 403:
            raise SourceError("SEC refused the request (403). Set sec_user_agent in config.toml "
                              "to an app name and contact email")
        resp.raise_for_status()
        return resp

    async def _entries(self, feed_type: str) -> list:
        """Newest entries of one feed. When a whole page is new (a burst of filings since the
        last poll), keep reading pages so none are skipped."""
        entries: list = []
        for page in range(MAX_PAGES):
            url = FEED_URL.format(type=quote(feed_type), start=page * 100)
            batch = feedparser.parse((await self._get(url)).content).entries
            entries += batch
            if (len(batch) < 100 or feed_type not in self._primed
                    or any(_accession(e) in self._seen for e in batch)):
                break
        return entries

    async def _build(self, accession: str, rows: list, tickers: dict[str, str]) -> dict | None:
        form = rows[0][0]
        entry = rows[0][4]
        if form in DILUTION:
            # Co-registrants (subsidiaries guaranteeing the offering) file under the same
            # accession number; the listed parent is the one with a ticker.
            listed = next(((name, cik) for _, name, cik, _, _ in rows if cik in tickers), None)
            if listed is None:
                return None  # not a listed company, e.g. an IPO
            name, cik = listed
            info = DILUTION[form]
            return self._item(accession, entry, tickers[cik], "dilution", info, form,
                              f"{name}: {info.label} ({form})", info.explain)
        if form == "4":
            doc = embedded_xml(await self._document(entry))
            trade = parse_form4(doc) if doc is not None else None
            if trade is None:
                return None
            # The CIK map is current; the symbol typed into the filing can be stale or "NONE".
            ticker = tickers.get(trade["issuer_cik"]) or trade["ticker"]
            side = next((s for s in ("buy", "sell")
                         if trade[s] and trade[s]["value"] >= self.settings.insider_min_value), None)
            if not ticker or side is None:
                return None
            info, title, summary = insider_item(trade, side)
            return self._item(accession, entry, ticker, f"insider_{side}", info, form, title, summary)
        if form in ("SCHEDULE 13D", "SCHEDULE 13D/A"):
            subject = next(((name, cik) for _, name, cik, role, _ in rows if role == "Subject"), None)
            doc = embedded_xml(await self._document(entry))
            stake = parse_13d(doc) if doc is not None else None
            cik = (stake or {}).get("issuer_cik") or (subject[1] if subject else "")
            if not (ticker := tickers.get(cik)):
                return None
            issuer = (stake and stake["issuer"]) or (subject[0] if subject else ticker)
            info = STAKE_UPDATE if form.endswith("/A") else STAKE
            who = (stake and stake["filer"]) or "An investor"
            whose = "its" if stake and stake["filer"] else "a"
            verb = f"updated {whose} stake in" if form.endswith("/A") else "disclosed a stake in"
            size = f": {stake['percent']:.1f}%" if stake and stake["percent"] is not None else ""
            short_form = form.replace("SCHEDULE ", "")
            return self._item(accession, entry, ticker, "stake", info, short_form,
                              f"{who} {verb} {issuer}{size}", info.explain)
        return None  # 424B2, 4/A and other forms the feeds also return

    async def _document(self, entry) -> str:
        # ".../0001234567-26-000001-index.htm" -> ".../0001234567-26-000001.txt", the whole
        # submission in one request.
        link = entry.get("link", "")
        if not link.endswith("-index.htm"):
            raise ValueError(f"unexpected filing link {link!r}")
        return (await self._get(link.removesuffix("-index.htm") + ".txt")).text

    def _item(self, accession: str, entry, ticker: str, kind: str, info: FormInfo, form: str,
              title: str, summary: str) -> dict:
        return {
            "id": hashlib.sha1(f"sec:{accession}".encode()).hexdigest()[:16],
            "title": title,
            "summary": summary,
            "url": entry.get("link", ""),
            "source": SOURCE,
            "published": _published(entry),
            "tickers": [ticker],
            "tags": [form],
            "sentiment": _sentiment(info),
            "matched": [],
            "alert": False,
            "filing": {"kind": kind, "form": form, "label": info.label, "hot": None},
        }


def _accession(entry) -> str | None:
    m = ACCESSION.search(entry.get("id", ""))
    return m.group(1) if m else None


def _failure_reason(results: list) -> str:
    errors = [r for r in results if isinstance(r, BaseException)]
    first = next((e for e in errors if isinstance(e, SourceError)), None)
    return str(first) if first else f"all {len(errors)} EDGAR feeds failed"
