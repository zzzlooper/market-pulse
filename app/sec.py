"""The one HTTP client for SEC hosts: news, filings and dilution profiles all go through it.

SEC asks automated clients to put a contact address in their User-Agent. That address is
personal, so this client is the only place it goes: it talks to SEC hosts only, refuses
redirects (so the header can't be carried elsewhere), and never logs it. It also spaces out
requests to stay under SEC's fair-access limit of 10 per second.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx

from .health import SourceError, describe

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MIN_REQUEST_GAP = 0.125  # seconds between request starts: at most 8 per second
TICKER_MAP_RETRY = 600   # seconds before retrying a failed ticker-map download


def is_sec_url(url: str) -> bool:
    return urlparse(url).hostname in ALLOWED_HOSTS


class SecClient:
    def __init__(self, user_agent: str):
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": user_agent})
        self._gate = asyncio.Lock()
        self._next_request = 0.0
        self._tickers: dict[str, str] = {}  # CIK (10 digits) -> ticker
        self._ciks: dict[str, str] = {}     # ticker -> CIK
        self._tickers_at: float | None = None  # when the last download was tried
        self._tickers_lock = asyncio.Lock()    # news, filings and dilution all ask at startup
        self.tickers_error: str | None = None  # why the last download failed

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str) -> httpx.Response:
        if not is_sec_url(url):
            raise ValueError("the SEC client only talks to SEC hosts")
        async with self._gate:
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

    async def tickers(self) -> dict[str, str]:
        """CIK -> ticker for every listed company, loaded once. After a failure it's retried
        every TICKER_MAP_RETRY seconds and is empty until then."""
        async with self._tickers_lock:
            due = self._tickers_at is None or (
                not self._tickers and time.monotonic() - self._tickers_at >= TICKER_MAP_RETRY)
            if due:
                self._tickers_at = time.monotonic()
                try:
                    rows = (await self.get(TICKERS_URL)).json().values()
                    tickers: dict[str, str] = {}
                    for row in rows:
                        tickers.setdefault(f"{row['cik_str']:010d}", row["ticker"])
                    self._tickers = tickers
                    self.tickers_error = None
                    self._ciks = {}
                    for row in rows:
                        self._ciks.setdefault(row["ticker"].upper(), f"{row['cik_str']:010d}")
                except Exception as exc:
                    self.tickers_error = describe(exc)
                    log.warning("could not load SEC ticker map: %s", self.tickers_error)
        return self._tickers

    async def require_tickers(self) -> dict[str, str]:
        """The ticker map, or SourceError saying why it's missing."""
        if not (tickers := await self.tickers()):
            raise SourceError(f"SEC ticker map unavailable ({self.tickers_error or 'empty'}), retrying")
        return tickers

    async def cik(self, ticker: str) -> str | None:
        await self.tickers()
        # SEC writes class shares with a dash (BRK-B); Yahoo and the feeds often use a dot.
        return self._ciks.get(ticker.upper()) or self._ciks.get(ticker.upper().replace(".", "-"))
