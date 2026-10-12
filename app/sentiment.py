"""Rule-based sentiment for market news, tuned for headline and press-release language.

Phrases are weighted from -3 (very bearish) to +3 (very bullish). The longest phrase at
each position wins, so "public offering" beats "offering" and "initial public offering"
(neutral) beats both. A negator shortly before a phrase flips and dampens it ("did not
meet its primary endpoint"). The raw sum is squashed into -1..1 like VADER does.
"""
from __future__ import annotations

import math
import re
from itertools import product

# Each space-separated word may list alternatives: "raise|raises guidance|outlook" expands to
# four phrases. Alternatives are single words; give multi-word variants their own entry.
# Text is lowercased and punctuation dropped before matching ("non-reliance" -> "non reliance").
LEXICON_SOURCE: dict[str, float] = {
    # earnings & guidance
    "beat|beats|tops|top|exceeds|exceeded|surpasses estimates|expectations|consensus": 2.5,
    "beat|beats|tops|exceeds|exceeded": 1.5,
    "miss|misses|missed estimates|expectations|consensus": -2.5,
    "miss|misses|missed": -1.5,
    "falls short": -1.5,
    "raise|raises|raised|raising|boosts|lifts guidance|outlook|forecast": 3,
    "lower|lowers|lowered|cut|cuts|slashes|reduces guidance|outlook|forecast": -3,
    "reaffirm|reaffirms|reaffirmed|reiterates|maintains guidance|outlook": 0.5,
    "profit warning": -3,
    "record revenue|sales|profit|earnings|quarter|results": 2.5,
    "record date": 0,
    "record": 1,
    "revenue|sales|earnings|profit growth": 1.5,
    "net loss": -1.5,
    "loss|losses": -1,
    "profit|profitable|profitability": 1,
    "strong|robust|solid results|demand|quarter": 1.5,
    "weak|weaker|soft results|demand|quarter": -1.5,
    "growth": 1,
    "decline|declines|declined|decrease|decreased": -1.5,
    # analysts
    "upgrade|upgrades|upgraded": 2,
    "downgrade|downgrades|downgraded": -2,
    "raise|raises|raised|lifts|boosts price target": 1.5,
    "lower|lowers|lowered|cut|cuts|slashes price target": -1.5,
    "price target raised|increased": 1.5,
    "price target lowered|cut": -1.5,
    "outperform|overweight": 1.5,
    "underperform|underweight": -1.5,
    # capital & corporate actions
    "public|registered|direct|secondary|underwritten offering": -2,
    "follow on offering": -2,
    "at the market offering": -2,
    "initial public offering": 0,
    "offering": -1,
    "dilution|dilutive": -2,
    "reverse stock|share split": -2,
    "stock|share split": 1,
    "buyback|buybacks": 2,
    "share|stock repurchase|repurchases": 2,
    "raise|raises|raised|increase|increases|increased|boosts dividend": 2,
    "special dividend": 2,
    "cut|cuts|suspend|suspends|suspended|eliminates dividend": -3,
    "to be acquired": 3,
    "takeover bid|offer": 2,
    "acquire|acquires|acquisition|merger": 1,
    "partnership|collaboration": 1,
    "strategic alliance": 1,
    "awarded|wins|won|secures|receives|lands contract|contracts|order|orders": 1.5,
    "new|additional orders|contracts": 1.5,
    "contract|award": 0.5,
    "expands|expansion|launches|launch": 0.5,
    "layoff|layoffs": -1.5,
    "job cuts": -1.5,
    "restructuring": -1,
    "resign|resigns|resigned|departure": -1,
    "steps down": -1,
    # biotech / regulatory
    "fda approval|approves|approved|clearance|clears": 3,
    "approval|approves|approved": 2,
    "fast track": 2,
    "breakthrough therapy": 2,
    "orphan drug": 1.5,
    "met|meets|meet|achieved primary endpoint|endpoints": 3,
    "primary|secondary endpoint|endpoints": 2,
    "positive topline|interim results|data": 2.5,
    "positive top line results|data": 2.5,
    "positive results|data": 2,
    "complete response letter": -3,
    "clinical hold": -3,
    "recall|recalls": -2,
    "warning letter": -2,
    # distress & legal
    "bankruptcy|insolvency|receivership": -3,
    "chapter 11": -3,
    "going concern": -3,
    "default|defaults|defaulted": -2.5,
    "delisting|delisted": -3,
    "deficiency notice": -2.5,
    "non reliance": -3,
    "restatement|restate|restates": -3,
    "investigation|probe|subpoena": -2,
    "class action": -2,
    "securities fraud": -2,
    "lawsuit|sues|sued|litigation": -1.5,
    "sec charges": -3,
    "short seller|report": -2,
    "settlement|settles": 0.5,
    "halt|halted|halts": -1.5,
    "warning|warns": -1.5,
    # price action
    "soar|soars|soaring|surge|surges|surging|jump|jumps|jumping|skyrocket|skyrockets|rally|rallies|spikes": 2,
    "rise|rises|rising|gain|gains|climb|climbs|climbing|higher": 1,
    "plunge|plunges|plunging|tumble|tumbles|crash|crashes|sink|sinks|plummet|plummets|cratering": -2,
    "fall|falls|falling|drop|drops|dropping|slide|slides|sliding|slump|slumps|lower": -1,
    "sell off|offs": -1.5,
    "selloff|selloffs": -1.5,
}

NEGATORS = {
    "no", "not", "never", "without", "fail", "fails", "failed", "unable", "denies", "denied",
    "didn't", "doesn't", "isn't", "wasn't", "won't", "can't",
}
NEGATION_WINDOW = 3     # tokens before a phrase that can negate it
NEGATION_FACTOR = -0.75
SUMMARY_WEIGHT = 0.5    # the headline carries the signal; summaries add context
ALPHA = 10              # squash constant: raw 2 -> 0.53, raw 3 -> 0.69
THRESHOLD = 0.35        # |score| at or above this is bullish/bearish


def _expand(source: dict[str, float]) -> dict[tuple[str, ...], float]:
    lexicon = {}
    for pattern, weight in source.items():
        for phrase in product(*(word.split("|") for word in pattern.split())):
            lexicon.setdefault(phrase, weight)  # an earlier, more specific entry wins
    return lexicon


LEXICON = _expand(LEXICON_SOURCE)
MAX_PHRASE = max(map(len, LEXICON))
TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower().replace("’", "'"))


def raw_score(text: str) -> tuple[float, list[str]]:
    """Sum of phrase weights in text, plus the phrases that matched (negated ones prefixed 'not')."""
    tokens = tokenize(text)
    total, hits, i = 0.0, [], 0
    while i < len(tokens):
        for n in range(min(MAX_PHRASE, len(tokens) - i), 0, -1):
            phrase = tuple(tokens[i:i + n])
            if phrase not in LEXICON:
                continue
            weight = LEXICON[phrase]
            if weight:
                negated = bool(NEGATORS.intersection(tokens[max(0, i - NEGATION_WINDOW):i]))
                total += weight * NEGATION_FACTOR if negated else weight
                hits.append(("not " if negated else "") + " ".join(phrase))
            i += n
            break
        else:
            i += 1
    return total, hits


def score(title: str, summary: str = "") -> dict:
    title_raw, title_hits = raw_score(title)
    summary_raw, summary_hits = raw_score(summary)
    raw = title_raw + SUMMARY_WEIGHT * summary_raw
    value = raw / math.sqrt(raw * raw + ALPHA)
    label = "bullish" if value >= THRESHOLD else "bearish" if value <= -THRESHOLD else "neutral"
    return {
        "score": round(value, 2),
        "label": label,
        "terms": list(dict.fromkeys(title_hits + summary_hits)),
    }
