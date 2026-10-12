import pytest

from app.sentiment import LEXICON, raw_score, score


@pytest.mark.parametrize("headline", [
    "Acme beats Q3 estimates and raises full-year guidance",
    "BioCo receives FDA approval for lead candidate",
    "BioCo Phase 3 trial met its primary endpoint",
    "Acme agrees to be acquired by MegaCorp for $40 per share",
    "Analyst upgrades Acme to Buy, raises price target",
    "Acme announces $500 million share repurchase program",
    "Acme reports record revenue, shares surge",
    "Gilat secures over $8 million in additional orders",
])
def test_bullish(headline):
    assert score(headline)["label"] == "bullish"


@pytest.mark.parametrize("headline", [
    "Acme misses estimates, cuts outlook",
    "BioCo receives complete response letter from FDA",
    "BioCo trial did not meet its primary endpoint",
    "BioCo trial failed to meet primary endpoint",
    "Acme announces pricing of $50 million public offering",
    "Acme files for Chapter 11 bankruptcy protection",
    "Acme receives Nasdaq delisting notice",
    "ROSEN LAW FIRM encourages Acme investors to secure counsel in securities class action",
    "Acme downgraded to Sell as shares plunge",
    "Acme announces 1-for-20 reverse stock split",
    "Is it time to sell this dropping drone stock as industrials sell off?",
    "Acme suspends dividend amid going concern doubts",
])
def test_bearish(headline):
    assert score(headline)["label"] == "bearish"


@pytest.mark.parametrize("headline", [
    "Acme to present at investor conference next week",
    "Acme announces pricing of initial public offering",
    "Acme announces record date for quarterly dividend",
    "Acme appoints new chief financial officer",
])
def test_neutral(headline):
    assert score(headline)["label"] == "neutral"


def test_longest_phrase_wins_and_is_counted_once():
    assert raw_score("public offering") == (-2, ["public offering"])
    assert raw_score("initial public offering") == (0, [])


def test_negation_flips_and_reports_term():
    total, hits = raw_score("did not receive FDA approval")
    assert total < 0
    assert hits == ["not fda approval"]
    assert raw_score("Acme didn’t beat estimates")[0] < 0  # curly apostrophe from feeds


def test_summary_counts_less_than_title():
    assert score("Acme raises guidance")["score"] > score("Acme update", "Acme raises guidance")["score"] > 0


def test_score_is_bounded_and_terms_listed():
    s = score("Acme beats estimates, raises guidance, record revenue, upgrade, buyback, shares soar")
    assert 0.9 < s["score"] < 1
    assert "raises guidance" in s["terms"]


def test_lexicon_patterns_expand_to_single_word_alternatives():
    assert ("falls", "short") in LEXICON
    assert ("class", "action") in LEXICON
    assert ("miss", "short") not in LEXICON
