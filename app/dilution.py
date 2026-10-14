"""Dilution risk for one company, from free SEC data.

- Filing history (data.sec.gov submissions): shelf registrations, pending S-1s, offerings,
  private share sales (8-K item 3.02), delisting notices (3.01) and reverse-split signals (3.03).
- XBRL financials (data.sec.gov company concepts): cash, operating cash burn, and the share
  count a year apart.

The verdict is a transparent points score, and every point comes with the reason behind it.

Privacy: SEC's responses also carry company addresses and phone numbers, and the filing
history includes insiders' Form 4s. None of that is passed on: only the aggregates computed
here leave this module.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import date, timedelta

import httpx

from .clock import clock
from .filings import DILUTION, ITEMS_8K
from .health import describe
from .market import ET
from .sec import SecClient
from .splits import current as split_current
from .splits import detect as detect_split
from .warrants import HEAD_BYTES, describe_sold, detect

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.htm"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{doc}"
WARRANT_FILINGS = 3          # newest offering-type filings read for warrants, per profile
SPLIT_FILINGS = 2            # newest 8-Ks with item 3.03 or 5.03 read for a reverse split
SPLIT_LOOKBACK = timedelta(days=60)
WARRANT_OVERHANG_PCT = 10.0  # outstanding warrant shares at least this share of the float scores
WARRANT_LEVELS = 6           # exercise prices watched per stock
COMPANY_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&owner=include&count=40"
CACHE_SECONDS = 6 * 3600  # filings and financials change a few times a quarter
CACHE_SIZE = 200

# The first concept a company reports is used. Foreign filers report under IFRS.
CASH = (("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        ("ifrs-full", "CashAndCashEquivalents"))
OPERATING_CASH_FLOW = (("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
                       ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
                       ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"))
SHARES = (("dei", "EntityCommonStockSharesOutstanding"),)

SHELF_FORMS = {"S-3", "S-3/A", "S-3ASR", "F-3", "F-3/A", "F-3ASR"}
REGISTRATION_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A"}
PRICED_FORMS = {"424B1", "424B4"}
# After any of these an S-1 is no longer "being prepared": it priced, went effective (a resale
# registration gets a 424B3, not a 424B4), or was withdrawn.
PAST_PENDING = PRICED_FORMS | {"424B3", "EFFECT", "RW"}
OFFERING_FORMS = {"424B1", "424B4", "424B5"}
SHELF_LIFE = timedelta(days=3 * 365)  # a shelf registration can be used for 3 years
PENDING_WINDOW = timedelta(days=180)
YEAR = timedelta(days=365)
STALE = timedelta(days=550)  # financials older than this say little about today
DAYS_PER_MONTH = 30.44

HIGH, MEDIUM = 4, 2  # points for each level


def _rows(submissions: dict) -> list[dict]:
    recent = submissions["filings"]["recent"]
    cik = int(submissions["cik"])
    rows = []
    docs = recent.get("primaryDocument") or [""] * len(recent["form"])
    for form, filed, items, accession, doc in zip(recent["form"], recent["filingDate"], recent["items"],
                                                  recent["accessionNumber"], docs):
        folder = accession.replace("-", "")
        rows.append({
            "form": form,
            "filed": date.fromisoformat(filed),
            "items": [i for i in items.split(",") if i],
            "accession": accession,
            "url": INDEX_URL.format(cik=cik, folder=folder, accession=accession),
            "doc": DOC_URL.format(cik=cik, folder=folder, doc=doc) if doc else None,
        })
    return sorted(rows, key=lambda r: r["filed"], reverse=True)


def filing_history(submissions: dict, today: date) -> dict:
    """Dilution-related facts from a company's recent filings (SEC lists up to ~1,000)."""
    rows = _rows(submissions)

    def within(row: dict, span: timedelta) -> bool:
        return today - row["filed"] <= span

    def with_item(item: str, span: timedelta) -> list[dict]:
        return [r for r in rows if r["form"] == "8-K" and item in r["items"] and within(r, span)]

    shelf = None
    shelves = [r for r in rows if r["form"] in SHELF_FORMS and within(r, SHELF_LIFE)]
    if shelves:
        # The base registration starts the 3 years; amendments don't restart them.
        base = next((r for r in shelves if not r["form"].endswith("/A")), shelves[0])
        effective = base["form"].endswith("ASR") or any(
            r["form"] == "EFFECT" and r["filed"] >= base["filed"] for r in rows)
        shelf = {"form": base["form"], "filed": base["filed"], "effective": effective,
                 "expires": base["filed"] + SHELF_LIFE}

    pending = None
    registration = next((r for r in rows if r["form"] in REGISTRATION_FORMS and within(r, PENDING_WINDOW)), None)
    if registration and not any(r["form"] in PAST_PENDING and r["filed"] >= registration["filed"] for r in rows):
        pending = {"form": registration["form"], "filed": registration["filed"]}

    offerings = sorted({r["filed"] for r in rows if r["form"] in OFFERING_FORMS and within(r, YEAR)}, reverse=True)
    events = []
    for r in rows:
        if not within(r, 2 * YEAR):
            break
        if r["form"] in DILUTION:
            label = DILUTION[r["form"]].label
        elif r["form"] == "8-K" and (notable := [ITEMS_8K[i].label for i in r["items"]
                                                 if i in ITEMS_8K and ITEMS_8K[i].notable]):
            label = ", ".join(notable)
        else:
            continue
        events.append({"filed": r["filed"], "form": r["form"], "label": label, "url": r["url"]})

    return {
        "covered_since": rows[-1]["filed"] if rows else None,
        "shelf": shelf,
        "pending": pending,
        "offerings_12m": offerings,
        "private_sales_12m": [r["filed"] for r in with_item("3.02", YEAR)],
        "delisting_notices_12m": [r["filed"] for r in with_item("3.01", YEAR)],
        "rights_changes_24m": [r["filed"] for r in with_item("3.03", 2 * YEAR)],
        "events": events[:15],
        # Offerings and private sales of the last year whose first pages may state warrants.
        "warrant_candidates": [
            {"form": r["form"], "filed": r["filed"], "accession": r["accession"], "doc": r["doc"]}
            for r in rows if r["doc"] and within(r, YEAR)
            and (r["form"] in OFFERING_FORMS | REGISTRATION_FORMS | {"424B3", "424B7"}
                 or (r["form"] == "8-K" and "3.02" in r["items"]))][:WARRANT_FILINGS],
        # Recent 8-Ks that may announce a reverse split: a rights change or a charter amendment.
        "split_candidates": [
            {"form": r["form"], "filed": r["filed"], "accession": r["accession"], "doc": r["doc"]}
            for r in rows if r["doc"] and r["form"] == "8-K" and within(r, SPLIT_LOOKBACK)
            and {"3.03", "5.03"} & set(r["items"])][:SPLIT_FILINGS],
    }


def _facts(concept: dict | None, prefer: str = "USD") -> tuple[str, list[dict]]:
    """(unit, facts), in the preferred unit when the company reports several currencies."""
    units = (concept or {}).get("units") or {}
    if not units:
        return "", []
    unit = prefer if prefer in units else next(iter(units))
    return unit, units[unit]


def cash_runway(cash: dict | None, operating: dict | None, today: date) -> dict | None:
    """Latest cash, monthly operating burn and the months of cash left at that burn."""
    currency, cash_facts = _facts(cash)
    _, flow_facts = _facts(operating, prefer=currency)  # burn in the same currency as the cash
    if not cash_facts:
        return None
    latest = max(cash_facts, key=lambda f: (f["end"], f.get("filed", "")))
    as_of = date.fromisoformat(latest["end"])
    if today - as_of > STALE:
        return None
    result = {"cash": latest["val"], "as_of": as_of, "currency": currency,
              "monthly_burn": None, "burn_months": None, "months_left": None}
    periods = [f for f in flow_facts if f.get("start")]
    if periods:
        # 10-Qs report cash flow year-to-date; the longest period to the latest date is steadiest.
        flow = max(periods, key=lambda f: (f["end"], date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])))
        months = (date.fromisoformat(flow["end"]) - date.fromisoformat(flow["start"])).days / DAYS_PER_MONTH
        if months >= 2 and today - date.fromisoformat(flow["end"]) <= STALE:
            burn = max(-flow["val"] / months, 0.0)
            result["monthly_burn"], result["burn_months"] = burn, round(months)
            if burn:
                elapsed = (today - as_of).days / DAYS_PER_MONTH
                result["months_left"] = latest["val"] / burn - elapsed
    return result


def share_growth(shares: dict | None, today: date) -> dict | None:
    """Shares outstanding now vs. about a year earlier, from cover-page counts."""
    _, facts = _facts(shares)
    if not facts:
        return None
    facts = sorted(facts, key=lambda f: f["end"])
    latest = facts[-1]
    as_of = date.fromisoformat(latest["end"])
    if today - as_of > STALE:
        return None
    earlier = [f for f in facts if date.fromisoformat(f["end"]) <= as_of - timedelta(days=300)]
    result = {"shares": latest["val"], "as_of": as_of, "year_ago": None, "change_pct": None}
    if earlier and earlier[-1]["val"]:
        prev = earlier[-1]
        result["year_ago"] = prev["val"]
        result["change_pct"] = (latest["val"] / prev["val"] - 1) * 100
    return result


def _date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def warrant_summary(checked: list[tuple[dict, dict | None]]) -> dict:
    """From (candidate filing, warrant facts) pairs, newest first."""
    sold = [(c, f) for c, f in checked if f and f["sold"]]
    outstanding = next(({**f["outstanding"], "as_of": c["filed"], "form": c["form"]}
                        for c, f in checked if f and f["outstanding"]), None)
    # Exercise prices to watch the stock against, newest filings first. Pre-funded warrants are
    # left out: their price is next to nothing, so they're effectively shares already. So are
    # warrants a registration statement proposes: not sold yet, and priced only provisionally.
    levels: list[dict] = []
    for c, f in checked:
        if c["form"] in REGISTRATION_FORMS:
            f = {"sold": []}
        for s in (f or {}).get("sold", []):
            if s["kind"] != "pre-funded" and s["exercise_price"]:
                levels.append({"kind": s["kind"], "shares": s["shares"], "price": s["exercise_price"],
                               "form": c["form"], "filed": c["filed"]})
    if outstanding and outstanding["exercise_price"]:
        levels.append({"kind": "outstanding", "shares": outstanding["shares"], "price": outstanding["exercise_price"],
                       "form": outstanding["form"], "filed": outstanding["as_of"]})
    unique: dict[tuple, dict] = {}
    for lv in levels:
        # A series is one set of warrants: a newer filing restating it has the current price.
        # Plain warrants from different deals are told apart by price.
        series = lv["kind"].startswith("Series ")
        unique.setdefault((lv["kind"], None if series else round(lv["price"], 4)), lv)
    # The prices recent offerings sold at: where the new shares' buyers break even.
    offering_levels: dict[float, dict] = {}
    for c, f in checked:
        price = (f or {}).get("offering_price")
        if price and c["form"] not in REGISTRATION_FORMS:
            offering_levels.setdefault(round(price, 4), {"kind": "offering", "price": price, "shares": None,
                                                          "form": c["form"], "filed": c["filed"]})
    return {
        "checked": len(checked),
        "levels": list(unique.values())[:WARRANT_LEVELS],
        "offering_levels": list(offering_levels.values()),
        "offerings_with_warrants": len(sold),
        "latest_sold": {"filed": sold[0][0]["filed"], "form": sold[0][0]["form"], "text": describe_sold(sold[0][1])}
        if sold else None,
        "outstanding": outstanding,
    }


def assess(history: dict, runway: dict | None, shares: dict | None, warrants: dict | None = None) -> dict:
    """Points for each warning sign; HIGH or more is high risk. Reasons come strongest first."""
    reasons: list[tuple[int, str]] = []
    if p := history["pending"]:
        reasons.append((2, f"{p['form']} filed {_date(p['filed'])}, not effective yet: an offering is being prepared"))
    # Only large, well-known issuers can file an automatic shelf (S-3ASR). Their prospectus
    # supplements are mostly debt, so for them the share count is the evidence that counts.
    seasoned = bool(history["shelf"] and history["shelf"]["form"].endswith("ASR"))
    if s := history["shelf"]:
        if seasoned:
            reasons.append((0, (f"Automatic shelf ({s['form']}): a large, well-known issuer that can sell "
                                "shares any time; its offerings are usually debt, so see the share count")))
        else:
            state = "effective" if s["effective"] else "filed, not yet effective"
            reasons.append((1, f"Shelf registration ({s['form']}) {state}, usable until about {_date(s['expires'])}"))
    if n := len(history["offerings_12m"]):
        text = f"{n} offering{'s' * (n > 1)} in the last 12 months, latest {_date(history['offerings_12m'][0])}"
        reasons.append((0 if seasoned else 2 if n >= 2 else 1, text + (" (likely debt)" if seasoned else "")))
    if n := len(history["private_sales_12m"]):
        reasons.append((1, f"{n} private share sale{'s' * (n > 1)} (8-K item 3.02) in the last 12 months"))
    if d := history["delisting_notices_12m"]:
        reasons.append((1, f"Delisting notice (8-K item 3.01) on {_date(d[0])}"))
    if runway and runway["months_left"] is not None:
        left = runway["months_left"]
        text = ("Cash likely ran out already at the last reported burn rate" if left <= 0 else
                f"About {left:.0f} month{'s' * (round(left) != 1)} of cash left at the last reported burn rate")
        reasons.append((2 if left < 6 else 1 if left < 12 else 0, text))
    elif runway and runway["monthly_burn"] == 0:
        reasons.append((-1, "Operating cash flow is positive: no cash burn to fund"))
    if shares and (pct := shares["change_pct"]) is not None:
        # SEC share counts aren't split-adjusted: a reverse split after the count makes it stale.
        later = [d for d in history["rights_changes_24m"] if d > shares["as_of"]]
        caveat = (f" (counted {_date(shares['as_of'])}, before the {_date(later[0])} "
                  "shareholder-rights change; not split-adjusted)") if later else ""
        shares["before_split"] = later[0] if later else None
        if pct >= 20:
            reasons.append((2 if pct >= 50 else 1, f"Share count up {pct:.0f}% in about a year{caveat}"))
        elif pct <= -50:
            reasons.append((0, f"Share count down {-pct:.0f}% in about a year, likely a reverse split"))
    if warrants and (n := warrants["offerings_with_warrants"]):
        latest = warrants["latest_sold"]
        reasons.append((1, (f"Warrants sold with {n} offering{'s' * (n > 1)} in the last 12 months; latest "
                            f"{latest['form']} on {_date(latest['filed'])}: {latest['text']}")))
    if warrants and (out := warrants["outstanding"]):
        pct = out["shares"] / shares["shares"] * 100 if shares and shares["shares"] else None
        share = f" ({pct:.0f}% of shares)" if pct is not None else ""
        price = f", average exercise price ${out['exercise_price']:,.2f}" if out["exercise_price"] else ""
        reasons.append((1 if pct is not None and pct >= WARRANT_OVERHANG_PCT else 0,
                        (f"Warrants for {out['shares']:,} shares outstanding{share}{price}, "
                         f"per the {out['form']} of {_date(out['as_of'])}")))
    if r := history["rights_changes_24m"]:
        reasons.append((0, f"Shareholder-rights change (8-K item 3.03, usually a reverse split) on {_date(r[0])}"))

    points = sum(p for p, _ in reasons)
    level = "high" if points >= HIGH else "medium" if points >= MEDIUM else "low"
    if runway is None and shares is None:
        # Without financials a quiet filing history isn't evidence of low risk.
        reasons.append((0, ("No recent XBRL financials (common for ETFs, trusts and foreign filers): "
                            "cash and share count unknown")))
        if level == "low":
            level = "unknown"
    reasons.sort(key=lambda r: -r[0])
    return {"level": level, "points": points, "reasons": [{"points": p, "text": t} for p, t in reasons]}


class DilutionProfiles:
    """Builds and caches dilution profiles on demand for the ticker detail panel."""

    def __init__(self, sec: SecClient):
        self.sec = sec
        self._cache: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
        self._loading: dict[str, asyncio.Future] = {}
        self._docs: dict[str, dict] = {}  # accession -> {"warrants": facts, "split": facts}

    def is_fresh(self, symbol: str) -> bool:
        hit = self._cache.get(symbol.upper())
        return hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS

    async def get(self, symbol: str) -> dict | None:
        """None when the symbol has no SEC filer (ETFs, indices, many foreign stocks)."""
        symbol = symbol.upper()
        if (hit := self._cache.get(symbol)) and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        # Concurrent requests for one symbol (several browsers) share one download.
        if symbol not in self._loading:
            self._loading[symbol] = asyncio.ensure_future(self._load(symbol))
        task = self._loading[symbol]
        try:
            profile = await asyncio.shield(task)
        finally:
            if task.done():
                self._loading.pop(symbol, None)
        self._cache[symbol] = (time.monotonic(), profile)
        self._cache.move_to_end(symbol)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return profile

    async def _load(self, symbol: str) -> dict | None:
        await self.sec.require_tickers()
        if not (cik := await self.sec.cik(symbol)):
            return None
        submissions, cash, operating, shares = await asyncio.gather(
            self.sec.get(SUBMISSIONS_URL.format(cik=cik)),
            self._concept(cik, CASH), self._concept(cik, OPERATING_CASH_FLOW), self._concept(cik, SHARES))
        today = clock.today(ET)
        history = filing_history(submissions.json(), today)
        runway = cash_runway(cash, operating, today)
        growth = share_growth(shares, today)
        candidates = history.pop("warrant_candidates")
        warrants = warrant_summary([(c, (await self._doc_facts(c))["warrants"]) for c in candidates])
        reverse_split = None
        for c in history.pop("split_candidates"):  # newest first
            split = (await self._doc_facts(c))["split"]
            if split_current(split, c["filed"], today):
                reverse_split = {**split, "form": c["form"], "filed": c["filed"]}
                break
        return {
            "symbol": symbol,
            "sec_url": COMPANY_URL.format(cik=int(cik)),
            **assess(history, runway, growth, warrants),
            "history": history,
            "cash": runway,
            "shares": growth,
            "warrants": warrants,
            "reverse_split": reverse_split,
        }

    async def _doc_facts(self, candidate: dict) -> dict:
        """What a filing's first pages say about warrants and a reverse split, read once: filings
        don't change. Unreadable now: nothing, and tried again with the next profile."""
        key = candidate["accession"]
        if key not in self._docs:
            try:
                head = await self.sec.get_head(candidate["doc"], HEAD_BYTES)
            except Exception as exc:
                log.info("first pages of %s %s unreadable: %s", candidate["form"], candidate["filed"], describe(exc))
                return {"warrants": None, "split": None}
            self._docs[key] = {"warrants": detect(head), "split": detect_split(head)}
            while len(self._docs) > 2000:
                self._docs.pop(next(iter(self._docs)))
        return self._docs[key]

    async def _concept(self, cik: str, candidates: tuple[tuple[str, str], ...]) -> dict | None:
        for taxonomy, concept in candidates:
            try:
                return (await self.sec.get(CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, concept=concept))).json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:  # 404: the company doesn't report this one
                    raise
        return None


def summary(profile: dict | None) -> dict | None:
    """What tables and alerts show: the level and the top reasons. None for non-filers."""
    if profile is None:
        return None
    top = [r["text"] for r in profile["reasons"] if r["points"] > 0][:3]
    warrants = profile.get("warrants") or {}

    def levels(key: str) -> list[dict]:
        return [{**lv, "filed": lv["filed"].isoformat()} for lv in warrants.get(key, [])]
    split = profile.get("reverse_split")
    return {"level": profile["level"], "points": profile["points"], "reasons": top,
            "warrant_levels": levels("levels"), "offering_levels": levels("offering_levels"),
            "reverse_split": {**split, "filed": split["filed"].isoformat()} if split else None}
