"""The morning game plan: today's gappers, each with everything the app knows about it, in one list.

The order is meant to be easy to follow, not a score: gappers with a catalyst (a headline or a
filing since yesterday's close) first, then the rest, each group by the size of its gap.

Each entry gathers what the dashboard otherwise spreads over badges, tooltips and the ticker
panel: the gap and pre-market activity, the catalyst, float and market cap, the short sale
restriction, a reverse split, day 2, dilution risk, the price levels from filings and the
pre-market high (with how far each is from the price), same-industry stocks moving with it,
today's halts and an earnings report. Flags are short and say whether they help or warn.
Market data only, plus which stocks are on your watchlist.
"""
from __future__ import annotations


def _away(level: float, price: float | None) -> float | None:
    return round((level / price - 1) * 100, 1) if price else None


def _levels(price: float | None, pre_high: float | None, risk: dict | None) -> list[dict]:
    """Price levels around the stock, highest first: the pre-market high and the filings' levels."""
    out = []
    if pre_high:
        out.append({"label": "pre-market high", "price": pre_high, "kind": "pmh"})
    for lv in (risk or {}).get("warrant_levels") or []:
        what = "warrants (average)" if lv["kind"] == "outstanding" else f"{lv['kind']} warrants".replace("common ", "")
        out.append({"label": what, "price": lv["price"], "kind": "warrant"})
    for lv in (risk or {}).get("offering_levels") or []:
        out.append({"label": f"offering ({lv['form']})", "price": lv["price"], "kind": "offering"})
    for lv in out:
        lv["away_pct"] = _away(lv["price"], price)
    return sorted(out, key=lambda lv: -lv["price"])


def entry(gap: dict, *, watchlist: list[str], floats: dict, market_caps: dict, ssr: dict, risk: dict,
          day2: dict, pre_highs: dict, sympathy: dict, industries: dict, halts: list[dict],
          earnings: dict, today: str) -> dict:
    symbol, price = gap["symbol"], gap.get("price")
    summary = risk.get(symbol) or None
    flags: list[dict] = []

    def flag(text: str, tone: str, why: str) -> None:  # tone: "good" (a tailwind), "warn" or "info"
        flags.append({"text": text, "tone": tone, "why": why})

    float_shares = floats.get(symbol)
    if float_shares and float_shares < 10_000_000:
        flag("low float", "good", "Fewer than 10M shares in the public float: it can move fast")
    if ssr.get(symbol):
        flag("SSR", "good" if gap["gap_pct"] > 0 else "info",
             "Short sale restriction in effect today: shorts can't sell at or below the bid")
    if d := day2.get(symbol):
        flag(f"day 2 (+{d['change_pct']:.0f}%)", "info", f"Up {d['change_pct']:.0f}% in the last session")
    if (rs := (summary or {}).get("reverse_split")) and rs.get("effective") and rs["effective"] <= today:
        flag(f"RS 1:{rs['ratio']}", "info", "A reverse split took effect recently: a small float, often a runner")
    elif rs:
        flag(f"RS 1:{rs['ratio']} ahead", "warn", "A reverse split is coming: often an offering follows")
    level = (summary or {}).get("level")
    if level in ("high", "medium"):
        flag(f"dilution {level}", "warn", "; ".join((summary or {}).get("reasons", [])[:2]) or "Dilution risk")
    if (e := earnings.get(symbol)) and e.get("date") == today:
        flag(f"earnings {e.get('when') or 'today'}", "info", "Reports today")
    todays_halts = [h for h in halts if h["symbol"] == symbol.replace("-", ".") and h["halted_at"][:10] == today]
    if todays_halts:
        flag(f"halted ×{len(todays_halts)}", "warn", "Halted today: " + ", ".join(h["reason_code"] for h in todays_halts))
    if symbol in watchlist:
        flag("watchlist", "info", "On your watchlist")

    peers = None
    if industry := industries.get(symbol):
        theme = sympathy.get(symbol)
        followers = sorted((s for s, v in sympathy.items() if v["leader"] == symbol), key=lambda s: -sympathy[s]["change_pct"])
        peers = {"industry": industry,
                 "leader": {"symbol": theme["leader"], "change_pct": theme["leader_pct"]} if theme else None,
                 "followers": [{"symbol": s, "change_pct": sympathy[s]["change_pct"]} for s in followers[:5]]}
    return {
        "symbol": symbol,
        "gap_pct": round(gap["gap_pct"], 1),
        "price": price,
        "prev_close": gap.get("prev_close"),
        "at_open": gap.get("at_open", False),
        "pre_minutes": gap.get("pre_minutes"),
        "why": gap.get("why"),
        "catalyst": gap.get("catalyst"),
        "float_shares": float_shares,
        "market_cap": market_caps.get(symbol),
        "flags": flags,
        "levels": _levels(price, pre_highs.get(symbol), summary),
        "peers": peers,
    }


def tags(e: dict) -> list[str]:
    """What a gapper's follow-through is grouped by in the Reactions tab: its catalyst and flags."""
    cat = e["catalyst"]
    out = ["with a filing" if cat and cat.get("filing") else "with a headline" if cat else "no catalyst"]
    for f in e["flags"]:
        text = f["text"]
        if text.startswith("day 2"):
            out.append("day 2")
        elif text.startswith("RS "):
            out.append("reverse split ahead" if text.endswith("ahead") else "reverse split done")
        elif text.startswith("halted"):
            out.append("halted")
        elif text.startswith("earnings"):
            out.append("earnings today")
        elif text in ("low float", "SSR", "dilution high", "dilution medium"):
            out.append(text)
    return out


def direction(e: dict) -> str:
    return "gap up" if e["gap_pct"] > 0 else "gap down"


def build(gaps: list[dict], **context) -> list[dict]:
    """The plan, in order: with a catalyst first, then by gap size (either way)."""
    entries = [entry(g, **context) for g in gaps]
    return sorted(entries, key=lambda e: (e["catalyst"] is None, -abs(e["gap_pct"])))


def brief_extras(e: dict) -> list[str]:
    """The flags worth a word in the 09:15 phone brief (day 2 and dilution risk it says already)."""
    keep = ("low float", "SSR", "RS ", "halted")
    return [f["text"] for f in e["flags"] if f["text"].startswith(keep)]
