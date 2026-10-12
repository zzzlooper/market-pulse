"""Trading halts from Nasdaq Trader's free RSS feed (covers Nasdaq, NYSE and other US venues).

The feed lists today's halts plus older halts that are still in effect (some regulatory
halts last for years). Times are US/Eastern. Volatility pauses (LUDP) carry a resumption
time from the start; news halts (T1/T12...) get one only when the halt is about to lift.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET_XML
from datetime import datetime

import httpx

from .market import ET

log = logging.getLogger(__name__)

HALTS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
NS = "{http://www.nasdaqtrader.com/}"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) market-pulse/0.1"

# code -> (label, category). Categories drive the UI colour.
REASONS: dict[str, tuple[str, str]] = {
    "T1": ("News pending", "news"),
    "T2": ("News released", "news"),
    "T3": ("News and resumption times", "news"),
    "T5": ("Single-stock trading pause", "volatility"),
    "T6": ("Extraordinary market activity", "regulatory"),
    "T7": ("Quotation-only period", "volatility"),
    "T8": ("ETF halt", "regulatory"),
    "T12": ("Additional information requested", "regulatory"),
    "H4": ("Non-compliance", "regulatory"),
    "H9": ("Not current in filings", "regulatory"),
    "H10": ("SEC trading suspension", "regulatory"),
    "H11": ("Regulatory concern", "regulatory"),
    "O1": ("Operations halt", "other"),
    "IPO1": ("IPO not yet trading", "other"),
    "IPOQ": ("IPO quotation-only period", "other"),
    "IPOE": ("IPO positioning window extension", "other"),
    "M": ("Volatility trading pause", "volatility"),
    "M1": ("Corporate action", "other"),
    "M2": ("Quotation not available", "other"),
    "LUDP": ("Volatility pause (limit up/down)", "volatility"),
    "LUDS": ("Volatility pause (straddle)", "volatility"),
    "MWC0": ("Market-wide circuit breaker carried over", "volatility"),
    "MWC1": ("Market-wide circuit breaker level 1", "volatility"),
    "MWC2": ("Market-wide circuit breaker level 2", "volatility"),
    "MWC3": ("Market-wide circuit breaker level 3", "volatility"),
    "D": ("Security deleted / delisted", "regulatory"),
    "R4": ("Qualification issues resolved", "other"),
    "R9": ("Filing requirements satisfied", "other"),
    "C3": ("Issuer news not forthcoming", "other"),
    "C4": ("Qualifications halt ended", "other"),
    "C9": ("Qualifications halt concluded", "other"),
    "C11": ("Halt concluded by other regulator", "other"),
}


def parse_et(date_s: str, time_s: str) -> datetime | None:
    """'10/09/2026' + '13:28:54.286' (or '13:28:54') -> aware ET datetime."""
    date_s, time_s = date_s.strip(), time_s.strip()
    if not date_s or not time_s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt).replace(tzinfo=ET)
        except ValueError:
            continue
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _field(item, tag: str) -> str:
    return (item.findtext(NS + tag) or "").strip()


def parse_halts(content: bytes) -> list[dict]:
    root = ET_XML.fromstring(content)
    halts = []
    for item in root.iter("item"):
        def field(tag: str, item=item) -> str:
            return _field(item, tag)
        symbol = field("IssueSymbol") or (item.findtext("title") or "").strip()
        halted_at = parse_et(field("HaltDate"), field("HaltTime"))
        if not symbol or halted_at is None:
            continue
        code = field("ReasonCode")
        label, category = REASONS.get(code, (code or "Unknown", "other"))
        resume_date = field("ResumptionDate")
        try:
            pause_price = float(field("PauseThresholdPrice")) if field("PauseThresholdPrice") else None
        except ValueError:
            pause_price = None
        halts.append({
            "id": f"{symbol}|{field('HaltDate')}|{field('HaltTime')}",
            "symbol": symbol,
            "name": field("IssueName"),
            "market": field("Market"),
            "reason_code": code,
            "reason": label,
            "category": category,
            "halted_at": halted_at.isoformat(),
            "resume_quote_at": _iso(parse_et(resume_date, field("ResumptionQuoteTime"))),
            "resume_trade_at": _iso(parse_et(resume_date, field("ResumptionTradeTime"))),
            "pause_price": pause_price,
        })
    return sorted(halts, key=lambda h: h["halted_at"], reverse=True)


def _hhmm(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M")


def halt_messages(new: list[dict], resumption_set: list[dict], watchlist: list[str],
                  mode: str) -> list[tuple[dict, str, str]]:
    """(halt, kind, message) for alert-worthy changes. mode: "watchlist" | "all" | "off"."""
    if mode == "off":
        return []
    watched = {s.replace("-", ".") for s in watchlist}  # Yahoo BRK-B is BRK.B at Nasdaq
    relevant = lambda h: mode == "all" or h["symbol"] in watched
    out = []
    for h in new:
        if relevant(h):
            resume = f" · resumes {_hhmm(h['resume_trade_at'])} ET" if h["resume_trade_at"] else ""
            out.append((h, "halt", f"{h['symbol']} halted at {_hhmm(h['halted_at'])} ET · {h['reason']}{resume}"))
    for h in resumption_set:
        if relevant(h):
            out.append((h, "resume", (f"{h['symbol']} trading resumes {_hhmm(h['resume_trade_at'])} ET "
                                      f"(halted {_hhmm(h['halted_at'])} · {h['reason']})")))
    return out


class HaltFeed:
    def __init__(self, url: str = HALTS_URL, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self._halts: dict[str, dict] = {}
        self._primed = False
        self._client = httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": BROWSER_UA},
                                         transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def latest(self) -> list[dict]:
        return sorted(self._halts.values(), key=lambda h: h["halted_at"], reverse=True)

    async def poll(self) -> tuple[bool, list[dict], list[dict]]:
        resp = await self._client.get(self.url)
        resp.raise_for_status()
        return self.apply(parse_halts(resp.content))

    def apply(self, halts: list[dict]) -> tuple[bool, list[dict], list[dict]]:
        """Swap in the latest list. Returns (changed, new halts, halts that just got a resumption
        time). The first list only sets the baseline, so startup reports nothing as new."""
        current = {h["id"]: h for h in halts}
        if not self._primed:
            self._primed = True
            self._halts = current
            return True, [], []
        new = [h for hid, h in current.items() if hid not in self._halts]
        resumption_set = [
            h for hid, h in current.items()
            if hid in self._halts and h["resume_trade_at"] and not self._halts[hid]["resume_trade_at"]
        ]
        changed = current != self._halts
        self._halts = current
        return changed, new, resumption_set
