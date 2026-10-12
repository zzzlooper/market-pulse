from datetime import date, datetime

from app.market import ET, find_unusual, pace, relative_volume, session_fraction


def et(*args):
    return datetime(*args, tzinfo=ET)


def test_session_fraction_outside_hours_is_full_day():
    assert session_fraction(et(2026, 10, 7, 8, 0)) == 1.0    # pre-market
    assert session_fraction(et(2026, 10, 7, 16, 30)) == 1.0  # after close
    assert session_fraction(et(2026, 10, 10, 12, 0)) == 1.0  # Saturday


def test_session_fraction_midday_and_floor():
    assert session_fraction(et(2026, 10, 7, 12, 45)) == 0.5  # 195 of 390 minutes
    assert session_fraction(et(2026, 10, 7, 9, 31)) == 0.05  # floored right after the open


def test_pace_only_applies_to_todays_volume():
    now = et(2026, 10, 7, 12, 45)
    assert pace(date(2026, 10, 7), now) == 0.5
    assert pace(date(2026, 10, 6), now) == 1.0
    assert pace(None, now) == 1.0


def test_relative_volume():
    assert relative_volume(2_000_000, 1_000_000) == 2.0
    assert relative_volume(1_000_000, 1_000_000, fraction=0.5) == 2.0
    assert relative_volume(0, 1_000_000) is None
    assert relative_volume(1_000_000, 0) is None


def test_find_unusual_filters_dedupes_and_sorts():
    rows = [
        {"symbol": "AAA", "rvol": 3.0, "volume": 1_000_000},
        {"symbol": "BBB", "rvol": 5.0, "volume": 2_000_000},
        {"symbol": "AAA", "rvol": 3.0, "volume": 1_000_000},  # same symbol from another screener
        {"symbol": "CCC", "rvol": 9.0, "volume": 10_000},      # too thin
        {"symbol": "DDD", "rvol": 1.2, "volume": 5_000_000},   # normal
        {"symbol": "EEE", "rvol": None, "volume": 5_000_000},
    ]
    assert [r["symbol"] for r in find_unusual(rows, 2.0, 300_000)] == ["BBB", "AAA"]
