"""News aggregation from RSS/Atom feeds, with ticker tagging and keyword alerts."""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from calendar import timegm
from collections import OrderedDict
from datetime import datetime, timezone

import feedparser
import httpx

from . import sentiment
from .config import SEC_8K_FEED, Settings
from .sec import SecClient, is_sec_url

log = logging.getLogger(__name__)

YAHOO_TICKER_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) market-pulse/0.1"
MAX_SEEN = 20_000
FAILING_AFTER = 3  # consecutive failed polls before a feed is reported; single misses are common

# "(NASDAQ: ABCD)", "NYSE American: XYZ", "(OTCQB:ABCD)", "NYSE: BRK.B"
EXCHANGE_TICKER = re.compile(
    r"\b(?i:nasdaq|nyse(?:\s+american|\s+arca)?|nyseamerican|amex|cboe|otc(?:qx|qb|\s+markets)?|tsxv?|cse)"
    r"\s*:\s*([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b"
)
CASHTAG = re.compile(r"(?<![\w$])\$([A-Z]{1,5})\b")
# EDGAR current-filings titles: "8-K - Apple Inc. (0000320193) (Filer)"
SEC_TITLE = re.compile(r"^(?P<form>[\w/\-]+)\s+-\s+(?P<company>.+?)\s+\((?P<cik>\d{10})\)")
TAG = re.compile(r"<[^>]+>")


def extract_tickers(text: str) -> list[str]:
    found = [m.group(1) for m in EXCHANGE_TICKER.finditer(text)]
    found += [m.group(1) for m in CASHTAG.finditer(text)]
    return list(dict.fromkeys(found))


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text, re.IGNORECASE)]


def parse_sec_title(title: str) -> tuple[str, str, str] | None:
    m = SEC_TITLE.match(title)
    return (m["form"], m["company"], m["cik"]) if m else None


def _clean(text: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", text)).split())


def _published(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    dt = datetime.fromtimestamp(timegm(parsed), timezone.utc) if parsed else datetime.now(timezone.utc)
    return dt.isoformat()


class NewsFeed:
    """Polls feeds, tags tickers, dedupes by headline and keeps a bounded history."""

    def __init__(self, settings: Settings, sec: SecClient | None = None):
        self.settings = settings
        self._own_sec = sec is None
        self.sec = sec or SecClient(settings.sec_user_agent)
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._seen: OrderedDict[str, None] = OrderedDict()  # outlives _items so trimmed news can't re-alert
        self._primed: set[str] = set()  # feeds fetched at least once; their backlog never alerts
        self.failed: list[str] = []  # feeds that failed in the last poll
        self.source_count = 0         # feeds tried in the last poll
        self._fail_streak: dict[str, int] = {}
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": BROWSER_UA})

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._own_sec:
            await self.sec.aclose()

    def latest(self, limit: int = 300) -> list[dict]:
        return sorted(self._items.values(), key=lambda n: n["published"], reverse=True)[:limit]

    async def poll(self, watchlist: list[str], keywords: list[str]) -> list[dict]:
        """Fetch every source once and return the items not seen before, newest first."""
        sources = [(label, url, None) for label, url in self.settings.feeds.items()]
        if not self.settings.sec_filings:  # otherwise FilingsFeed covers 8-Ks, with item alerts
            sources.append(("SEC 8-K", SEC_8K_FEED, None))
        if self.settings.ticker_news:
            sources += [("Yahoo Finance", YAHOO_TICKER_FEED.format(symbol=s), s) for s in watchlist]
        results = await asyncio.gather(*(self._fetch(url) for _, url, _ in sources), return_exceptions=True)

        watch = set(watchlist)
        fresh = []
        self.failed, self.source_count = [], len(sources)
        for (label, url, hint), entries in zip(sources, results):
            name = f"{label} {hint}" if hint else label
            if isinstance(entries, BaseException):
                log.warning("feed %s failed: %s", label, entries)
                self.failed.append(name)
                self._fail_streak[name] = self._fail_streak.get(name, 0) + 1
                continue
            self._fail_streak.pop(name, None)
            primed = url in self._primed
            self._primed.add(url)
            for entry in entries:
                item = await self._make_item(entry, label, hint)
                if item is None:
                    continue
                if item["id"] in self._seen:
                    if existing := self._items.get(item["id"]):
                        existing["tickers"] = list(dict.fromkeys(existing["tickers"] + item["tickers"]))
                    continue
                item["matched"] = match_keywords(f"{item['title']} {item['summary']}", keywords)
                item["alert"] = primed and bool(item["matched"] or watch & set(item["tickers"]))
                self._items[item["id"]] = item
                self._seen[item["id"]] = None
                fresh.append(item)

        while len(self._items) > self.settings.news_history:
            self._items.popitem(last=False)
        while len(self._seen) > MAX_SEEN:
            self._seen.popitem(last=False)
        return sorted(fresh, key=lambda n: n["published"], reverse=True)

    @property
    def failing(self) -> list[str]:
        """Feeds that failed their last FAILING_AFTER polls in a row."""
        return [name for name, streak in self._fail_streak.items() if streak >= FAILING_AFTER]

    async def _fetch(self, url: str) -> list:
        # SEC feeds go through the SEC client: it alone carries the contact address.
        resp = await self.sec.get(url) if is_sec_url(url) else await self._client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content).entries

    async def _make_item(self, entry, source: str, hint: str | None) -> dict | None:
        title = _clean(entry.get("title", ""))
        if not title:
            return None
        url = entry.get("link", "")
        summary = _clean(entry.get("summary", ""))
        tickers = extract_tickers(f"{title} {summary}")
        tags = []
        key = title.lower()  # same press release arrives via several feeds

        if sec := parse_sec_title(title):
            form, company, cik = sec
            title, tags, key = f"{company} filed {form}", [form], url
            # EDGAR summaries are filing metadata followed by the 8-K items, e.g. "Item 2.02: ..."
            summary = summary[summary.find("Item "):] if "Item " in summary else ""
            if ticker := (await self.sec.tickers()).get(cik):
                tickers.insert(0, ticker)
        if hint:
            tickers = list(dict.fromkeys([hint, *tickers]))
        summary = summary[:400]

        return {
            "id": hashlib.sha1(key.encode()).hexdigest()[:16],
            "title": title,
            "summary": summary,
            "url": url,
            "source": source,
            "published": _published(entry),
            "tickers": tickers,
            "tags": tags,
            "sentiment": sentiment.score(title, summary),
        }
