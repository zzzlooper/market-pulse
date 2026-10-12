from datetime import datetime, timedelta

from app.alerts import COOLDOWN_SECONDS, AlertMonitor, new_rule
from app.market import ET


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 9, 10, 0, tzinfo=ET)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def q(symbol="AAPL", price=100.0, change_pct=0.0, rvol=1.0):
    return {"symbol": symbol, "price": price, "change_pct": change_pct, "rvol": rvol}


def make():
    clock = Clock()
    return AlertMonitor(clock), clock


def test_fires_on_crossing_not_on_first_reading():
    mon, _ = make()
    rule = new_rule("AAPL", "above", 105)
    assert mon.check_quotes([q(price=110)], [rule]) == []          # already above at startup: baseline
    assert mon.check_quotes([q(price=104)], [rule]) == []          # drops below: re-armed
    [event] = mon.check_quotes([q(price=106)], [rule])             # crosses up
    assert event["symbol"] == "AAPL" and event["kind"] == "above"
    assert "crossed above 105" in event["message"]
    assert mon.check_quotes([q(price=107)], [rule]) == []          # stays above: no repeat
    assert list(mon.recent) == [event]


def test_below_rule():
    mon, _ = make()
    rule = new_rule("AAPL", "below", 95)
    mon.check_quotes([q(price=100)], [rule])
    assert len(mon.check_quotes([q(price=94.5)], [rule])) == 1


def test_cooldown_suppresses_flapping():
    mon, clock = make()
    rule = new_rule("AAPL", "above", 100)
    mon.check_quotes([q(price=99)], [rule])
    assert len(mon.check_quotes([q(price=101)], [rule])) == 1
    mon.check_quotes([q(price=99)], [rule])
    assert mon.check_quotes([q(price=101)], [rule]) == []          # within cooldown
    clock.advance(COOLDOWN_SECONDS)
    mon.check_quotes([q(price=99)], [rule])
    assert len(mon.check_quotes([q(price=101)], [rule])) == 1


def test_all_symbols_rule_tracks_each_symbol_separately():
    mon, _ = make()
    rule = new_rule("", "move", 5)
    mon.check_quotes([q("AAPL", change_pct=1), q("TSLA", change_pct=-2)], [rule])
    fired = mon.check_quotes([q("AAPL", change_pct=2), q("TSLA", change_pct=-6)], [rule])
    assert [e["symbol"] for e in fired] == ["TSLA"]
    assert "TSLA down 6.0% today" in fired[0]["message"]


def test_symbol_rule_ignores_other_symbols_and_missing_data():
    mon, _ = make()
    rule = new_rule("AAPL", "rvol", 3)
    mon.check_quotes([q("TSLA", rvol=1)], [rule])
    assert mon.check_quotes([q("TSLA", rvol=5)], [rule]) == []
    mon.check_quotes([{"symbol": "AAPL"}], [rule])                 # no data yet: not a baseline
    mon.check_quotes([q("AAPL", rvol=1)], [rule])
    assert len(mon.check_quotes([q("AAPL", rvol=3.5)], [rule])) == 1


def test_forget_rule_resets_baseline():
    mon, _ = make()
    rule = new_rule("AAPL", "above", 100)
    mon.check_quotes([q(price=99)], [rule])
    mon.forget_rule(rule["id"])
    assert mon.check_quotes([q(price=101)], [rule]) == []          # fresh baseline after forget


def test_unusual_primes_then_fires_once_per_day():
    mon, clock = make()
    a, b = q("AAA", rvol=4, change_pct=12), q("BBB", rvol=3, change_pct=-8)
    assert mon.check_unusual([a], enabled=True) == []              # startup list is the baseline
    [event] = mon.check_unusual([a, b], enabled=True)
    assert event["symbol"] == "BBB" and "unusual volume 3.0×" in event["message"]
    assert mon.check_unusual([a], enabled=True) == []
    assert mon.check_unusual([a, b], enabled=True) == []           # drops out and back: still once a day
    clock.advance(24 * 3600)
    assert mon.check_unusual([a, b], enabled=True) == []           # new day re-baselines


def test_unusual_disabled_still_tracks_seen():
    mon, _ = make()
    mon.check_unusual([], enabled=False)
    assert mon.check_unusual([q("AAA", rvol=4)], enabled=False) == []
    assert mon.check_unusual([q("AAA", rvol=4)], enabled=True) == []  # enabling later doesn't replay it
