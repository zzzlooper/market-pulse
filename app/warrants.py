"""Warrant overhang signals from SEC filings: warrants sold in an offering, and warrants already
outstanding, as the documents state them near their start. Also the price an offering was sold at.

Read from the beginning of a filing (HEAD_BYTES): an offering's cover page, its summary and
the dilution section, or an 8-K's description of a private placement. Prospectuses are long,
and SEC ignores partial-download requests, so the document is streamed and reading stops there.

- Sold: "Up to 400,000 Pre-Funded Warrants", "Up to 43,636,328 Shares of Common Stock underlying
  the Series D Warrants", "warrants to purchase up to 1,500,000 shares … exercise price of $1.25".
  Searched in the first SOLD_CHARS of text only, where the offering is described.
- Outstanding: "349,930 shares of common stock issuable upon the exercise of outstanding
  warrants, having a weighted average exercise price of $7.21" (one or several tranches).
- Shelf boilerplate ("we may offer common stock, warrants, units…") and "Description of
  Warrants" headings don't match: they name no quantity.
- Offering price: "public offering price of $2.10 per share", "the combined public offering price
  for each share and accompanying warrant is $1.00", "at a purchase price of $2.00 per share" (a
  private placement), "US$1.44 per Unit", also on the first SOLD_CHARS. Assumed prices (a
  registration statement's illustration) and aggregate amounts ("aggregate offering price of up
  to $50,000,000") don't count.

Best effort: wording varies, so a miss means "not found on the first pages", not "none".
Only numbers and kinds come out of here; no text from the filing is kept or shown, as filings
can name people (signatories, selling stockholders).
"""
from __future__ import annotations

import html
import json
import re
from datetime import date

HEAD_BYTES = 400_000  # the first pages: cover, summary, dilution
SOLD_CHARS = 40_000   # where an offering describes what it sells
NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
PRICE = r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
KIND = r"(pre-?funded|series [a-z]{1,2}|common|placement agent(?:'s|s'|s)?|underwriter(?:'s|s'|s)?)"
TAG = re.compile(r"<[^>]+>")
DIGEST = re.compile(r"<!--market-pulse-warrants (\{.*?\})-->", re.DOTALL)

SOLD_PATTERNS = [
    # "Up to 43,636,328 Shares of Common Stock underlying the Series D Warrants"
    re.compile(rf"up to {NUM} (?:shares of (?:our )?common stock|(?:ordinary |common )?shares)\s+"
               rf"(?:issuable upon (?:the )?exercise of|underlying) (?:the |such )?{KIND}?\s*warrants", re.IGNORECASE),
    # "Up to 400,000 Pre-Funded Warrants", "1,500,000 Series A Warrants"
    re.compile(rf"(?:up to )?{NUM} {KIND} warrants", re.IGNORECASE),
    # "Series A warrants to purchase up to 1,500,000 shares", "warrants to purchase an aggregate of …"
    re.compile(rf"{KIND}?\s*warrants to purchase (?:up to )?(?:an aggregate of )?{NUM}", re.IGNORECASE),
]
EXERCISE = re.compile(rf"exercise price (?:of|equal to) {PRICE}", re.IGNORECASE)
OFFERING_PRICE = re.compile(
    rf"(?:offering|purchase) price(?: per share| (?:for|of) each share[^$;]{{0,120}}?)?(?: of| is| equal to| was)?\s*(?:US)?{PRICE}",
    re.IGNORECASE)
NOT_A_SALE_PRICE = re.compile(r"(?:assumed|aggregate|maximum|illustrat\w*)\W+(?:\w+\W+){0,2}$", re.IGNORECASE)
MAX_SHARE_PRICE = 10_000  # anything above is an amount of money, not a price per share
OUTSTANDING = re.compile(
    rf"{NUM} (?:shares|ordinary shares|common shares|shares of (?:our )?common stock)(?: of common stock)?\s+"
    rf"issuable upon (?:the )?exercise of (?:our )?(?:outstanding )?(?:[\w-]+ )?warrants"
    rf"[^;•●·]{{0,90}}?exercise price of {PRICE}", re.IGNORECASE)


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def text_of(raw: str) -> str:
    """Visible text: tags dropped, entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(TAG.sub(" ", raw)).split())


def _kind(word: str | None) -> str:
    word = (word or "").lower().replace("-", "")
    if word.startswith("prefunded"):
        return "pre-funded"
    if word.startswith("series"):
        return "Series " + word.split()[-1].upper()
    if word.startswith(("placement", "underwriter")):
        return "agent"
    return "common"


def detect(raw: str) -> dict | None:
    """Warrant facts and the offering price from the start of a filing, or None if it states none.
    {"sold": [{"kind", "shares", "exercise_price"}], "outstanding": {"shares", "exercise_price"} | None,
     "offering_price": float | None}"""
    if m := DIGEST.search(raw):  # a recording keeps only these facts (see digest())
        return json.loads(m.group(1)) or None
    text = text_of(raw)
    sold: dict[str, dict] = {}
    for pattern in SOLD_PATTERNS:
        for m in pattern.finditer(text[:SOLD_CHARS]):
            groups = m.groups()
            number = next(g for g in groups if g and re.fullmatch(r"[\d,]+", g))
            kind = _kind(next((g for g in groups if g and not re.fullmatch(r"[\d,]+", g)), None))
            shares = int(_number(number))
            price = EXERCISE.search(text, m.end(), m.end() + 400)
            entry = sold.setdefault(kind, {"kind": kind, "shares": 0, "exercise_price": None})
            entry["shares"] = max(entry["shares"], shares)  # the cover repeats numbers; keep the largest
            if price and kind != "pre-funded" and entry["exercise_price"] is None:
                entry["exercise_price"] = _number(price.group(1))
    offering_price = None
    for m in OFFERING_PRICE.finditer(text[:SOLD_CHARS]):
        price = _number(m.group(1))
        if 0 < price < MAX_SHARE_PRICE and not NOT_A_SALE_PRICE.search(text[max(0, m.start() - 40):m.start()]):
            offering_price = price
            break
    tranches = [(int(_number(n)), _number(p)) for n, p in OUTSTANDING.findall(text)]
    outstanding = None
    if tranches:
        # Prospectuses repeat the dilution list (e.g. supplement and base prospectus): count each once.
        unique = list(dict.fromkeys(tranches))
        total = sum(n for n, _ in unique)
        weighted = sum(n * p for n, p in unique) / total if total else None
        outstanding = {"shares": total, "exercise_price": round(weighted, 4) if weighted is not None else None}
    if not sold and not outstanding and offering_price is None:
        return None
    return {"sold": sorted(sold.values(), key=lambda s: (s["kind"] != "pre-funded", s["kind"])),
            "outstanding": outstanding, "offering_price": offering_price}


def digest(raw: str) -> str:
    """What a recording keeps of a filing document: the warrant facts only, nothing else."""
    return f"<!--market-pulse-warrants {json.dumps(detect(raw) or {})}-->"


def shares_text(n: float) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{n:,.0f}"


def describe_sold(facts: dict | None) -> str:
    """'warrants for 43.6M shares (Series D) at $0.55, pre-funded for 400K', or ''."""
    if not facts or not facts["sold"]:
        return ""
    parts = []
    for s in facts["sold"]:
        if s["kind"] == "pre-funded":
            parts.append(f"pre-funded warrants for {shares_text(s['shares'])} shares")
        else:
            label = "" if s["kind"] == "common" else f" ({s['kind']})"
            price = f" at ${s['exercise_price']:,.2f}" if s["exercise_price"] else ""
            parts.append(f"warrants for {shares_text(s['shares'])} shares{label}{price}")
    return ", ".join(parts)


def describe_outstanding(facts: dict | None) -> str:
    out = (facts or {}).get("outstanding")
    if not out:
        return ""
    price = f", average exercise price ${out['exercise_price']:,.2f}" if out["exercise_price"] else ""
    return f"warrants for {shares_text(out['shares'])} shares outstanding{price}"


def in_the_money_message(symbol: str, price: float, level: dict) -> str:
    """The alert when a stock crosses up through a warrant level (dilution.warrant_summary)."""
    kind = level["kind"]
    what = ("the outstanding warrants' average exercise price" if kind == "outstanding"
            else f"the {'' if kind == 'common' else kind + ' '}warrants' exercise price")
    filed = date.fromisoformat(level["filed"])
    return (f"{symbol} crossed ${level['price']:,.2f} (now ${price:,.2f}), {what}: {shares_text(level['shares'])} "
            f"shares can now be exercised at a profit (per the {level['form']} of {filed:%b} {filed.day}, {filed.year})")


def offering_message(symbol: str, price: float, level: dict, up: bool) -> str:
    """The alert when a stock crosses the price a recent offering was sold at."""
    filed = date.fromisoformat(level["filed"])
    deal = f"the price of its {level['form']} offering of {filed:%b} {filed.day}, {filed.year}"
    if up:
        return f"{symbol} climbed back above ${level['price']:,.2f} (now ${price:,.2f}), {deal}"
    return f"{symbol} fell below ${level['price']:,.2f} (now ${price:,.2f}), {deal}: the deal's buyers are now under water"
