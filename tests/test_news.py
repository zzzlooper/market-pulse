from app.news import extract_tickers, match_keywords, parse_sec_title


def test_extract_exchange_tickers():
    text = "Acme Corp. (NASDAQ: ACME) and Beta Inc (NYSE:BETA) announce merger; also NYSE American: XYZ"
    assert extract_tickers(text) == ["ACME", "BETA", "XYZ"]


def test_extract_handles_class_shares_otc_and_cashtags():
    assert extract_tickers("(NYSE: BRK.B)") == ["BRK.B"]
    assert extract_tickers("Foo (OTCQB: FOOO) rallies, $TSLA too") == ["FOOO", "TSLA"]


def test_extract_ignores_dollar_amounts_and_dupes():
    assert extract_tickers("raises $5 million; $ACME $ACME (Nasdaq: ACME)") == ["ACME"]


def test_match_keywords_whole_words_case_insensitive():
    text = "Company receives FDA approval and announces share buyback"
    assert match_keywords(text, ["fda", "buyback", "merger"]) == ["fda", "buyback"]
    assert match_keywords("Offerings update", ["offering"]) == []


def test_parse_sec_title():
    assert parse_sec_title("8-K - Apple Inc. (0000320193) (Filer)") == ("8-K", "Apple Inc.", "0000320193")
    assert parse_sec_title("Apple announces results") is None
