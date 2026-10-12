from app.health import SourceHealth


class Clock:
    def __init__(self):
        self.t = 1_000.0

    def __call__(self):
        return self.t


def make(interval=15):
    clock = Clock()
    health = SourceHealth(clock)
    health.register("quotes", "Yahoo prices", interval)
    return health, clock


def test_starting_until_the_first_result_then_ok():
    health, clock = make()
    assert health.state("quotes") == "starting"
    clock.t += 10
    health.ok("quotes")
    assert health.status() == [{"name": "quotes", "label": "Yahoo prices", "state": "ok",
                                "last_ok": 1_010.0, "reason": None}]


def test_partial_success_is_degraded_with_its_note():
    health, _ = make()
    health.ok("quotes", "no data for XYZ")
    assert health.status_of("quotes")["state"] == "degraded"
    assert health.status_of("quotes")["reason"] == "no data for XYZ"


def test_a_failure_after_recent_data_is_degraded_until_the_data_is_too_old():
    health, clock = make(interval=15)  # stale after max(3 * 15, 60) = 60 s
    health.ok("quotes")
    clock.t += 15
    health.fail("quotes", "Yahoo returned no prices")
    assert health.status_of("quotes")["state"] == "degraded"
    clock.t += 46  # 61 s since the last good data
    assert health.status_of("quotes") | {"last_ok": None} == {
        "name": "quotes", "label": "Yahoo prices", "state": "down",
        "last_ok": None, "reason": "Yahoo returned no prices"}


def test_a_hung_source_goes_down_without_raising_anything():
    health, clock = make()
    health.ok("quotes")
    clock.t += 60
    assert health.state("quotes") == "ok"
    clock.t += 1
    assert health.status_of("quotes")["state"] == "down"
    assert health.status_of("quotes")["reason"] == "no update in over 60s"


def test_a_failure_before_any_data_is_down_at_once():
    health, _ = make()
    health.fail("quotes", "HTTP 503")
    assert health.state("quotes") == "down"


def test_staleness_scales_with_the_polling_interval():
    health, clock = make(interval=60)  # stale after 180 s
    health.ok("quotes")
    clock.t += 180
    assert health.state("quotes") == "ok"
    clock.t += 1
    assert health.state("quotes") == "down"


def test_an_outage_alerts_once_and_then_reports_recovery():
    health, clock = make()
    health.ok("quotes")
    clock.t += 61
    assert health.outages(alert_after=300) == []  # down, but not for long enough yet
    clock.t += 299
    assert health.outages(300) == []
    clock.t += 1
    assert health.outages(300) == [{"kind": "down", "label": "Yahoo prices",
                                    "reason": "no update in over 60s", "minutes": 6}]
    clock.t += 120
    assert health.outages(300) == []  # still down: no repeat
    health.ok("quotes")
    assert health.outages(300) == [{"kind": "recovered", "label": "Yahoo prices", "reason": None,
                                    "minutes": 7}]
    assert health.outages(300) == []


def test_a_short_outage_never_alerts_and_the_next_one_starts_its_own_timer():
    health, clock = make()
    health.ok("quotes")
    clock.t += 61
    assert health.outages(300) == []  # down from here
    clock.t += 200
    health.ok("quotes")
    assert health.outages(300) == []  # back after 200 s: no alert, no recovery message
    clock.t += 61
    assert health.outages(300) == []  # a new outage
    clock.t += 299
    assert health.outages(300) == []  # 499 s since the first one began, but this one is 299 s old
    clock.t += 1
    assert [e["kind"] for e in health.outages(300)] == ["down"]
