from datetime import timedelta

from statusrelay.rules import EventContext, WatchState, on_failure, on_success, rollback_notifications
from tests.helpers import NOW, make_area

CTX = EventContext("Show Day 1", "Sample Live Tour（Day 1）", "2026-10-31 19:00", "Sample Arena", "https://order")
A = make_area("A", "Area A")
B = make_area("B", "Area B")
NAMES = {"A": "Area A", "B": "Area B"}


def cur(*areas):
    return {a.area_id: a for a in areas}


def succeed(prev, *areas, now=NOW, reminder=10):
    return on_success(prev, cur(*areas), CTX, now, reminder, NAMES)


def test_empty_to_empty():
    events, state = succeed(WatchState())
    assert events == [] and state.available == ()


def test_empty_to_onsale():
    events, state = succeed(WatchState(), A)
    assert [e.kind for e in events] == ["onsale"]
    assert events[0].areas == (A,)
    assert events[0].order_url == "https://order"
    assert state.available == ("A",)
    assert state.last_notified_at == state.last_onsale_at == state.last_change_at == NOW
    assert state.last_onsale_signature == "A"


def test_new_area_marks_is_new():
    prev = WatchState(available=("A",), last_notified_at=NOW - timedelta(minutes=1))
    events, state = succeed(prev, A, B)
    assert [e.kind for e in events] == ["onsale"]
    assert [(a.area_id, a.is_new) for a in events[0].areas] == [("A", False), ("B", True)]
    assert state.last_onsale_signature == "A,B"


def test_same_set_reminds_only_after_interval():
    prev = WatchState(available=("A",), last_notified_at=NOW - timedelta(minutes=9))
    events, state = succeed(prev, A)
    assert events == [] and state.last_notified_at == prev.last_notified_at
    prev = WatchState(available=("A",), last_notified_at=NOW - timedelta(minutes=10))
    events, state = succeed(prev, A)
    assert [e.kind for e in events] == ["reminder"] and state.last_notified_at == NOW


def test_reminders_disabled():
    prev = WatchState(available=("A",), last_notified_at=NOW - timedelta(hours=5))
    events, _ = succeed(prev, A, reminder=0)
    assert events == []


def test_subset_updates_state_without_event():
    prev = WatchState(available=("A", "B"), last_notified_at=NOW - timedelta(minutes=1))
    events, state = succeed(prev, A)
    assert events == [] and state.available == ("A",) and state.last_change_at == NOW


def test_onsale_to_empty_is_soldout():
    prev = WatchState(available=("A", "B"), last_notified_at=NOW - timedelta(minutes=1))
    events, state = succeed(prev)
    assert [e.kind for e in events] == ["soldout"]
    assert [(a.area_id, a.name, a.status) for a in events[0].areas] == [("A", "Area A", "soldout"), ("B", "Area B", "soldout")]
    assert state.available == () and state.last_notified_at == NOW


def test_debounce_suppresses_identical_flicker():
    prev = WatchState(last_onsale_signature="A", last_onsale_at=NOW - timedelta(minutes=1))
    events, state = succeed(prev, A)
    assert events == [] and state.available == ("A",)
    prev = WatchState(last_onsale_signature="A", last_onsale_at=NOW - timedelta(minutes=3))
    events, _ = succeed(prev, A)
    assert [e.kind for e in events] == ["onsale"]


def test_failures_alert_once_at_threshold():
    state = WatchState()
    kinds = []
    for _ in range(5):
        events, state = on_failure(state, CTX, NOW, "API error: HTTP 500")
        kinds.append([e.kind for e in events])
    assert kinds == [[], [], ["error"], [], []]
    assert state.consecutive_errors == 5 and state.error_alerted


def test_error_detail_mentions_count():
    events, _ = on_failure(WatchState(consecutive_errors=2), CTX, NOW, "API error: HTTP 500")
    assert events[0].detail == "API error: HTTP 500 (3 consecutive failures)"


def test_immediate_failure_alerts_at_once():
    events, state = on_failure(WatchState(), CTX, NOW, "session not found", immediate=True)
    assert [e.kind for e in events] == ["error"] and state.error_alerted


def test_failure_keeps_available():
    _, state = on_failure(WatchState(available=("A",)), CTX, NOW, "x")
    assert state.available == ("A",)


def test_recovery_after_alert():
    prev = WatchState(consecutive_errors=4, error_alerted=True)
    events, state = succeed(prev, A)
    assert [e.kind for e in events] == ["recovered", "onsale"]
    assert state.consecutive_errors == 0 and not state.error_alerted


def test_rollback_restores_notification_fields():
    prev = WatchState()
    _, new = succeed(prev, A)
    rolled = rollback_notifications(prev, new)
    assert rolled.available == () and rolled.last_notified_at is None and rolled.last_onsale_signature is None
    assert rolled.consecutive_errors == new.consecutive_errors


def test_state_round_trip():
    _, state = succeed(WatchState(), A)
    assert WatchState.from_dict(state.to_dict()) == state
    assert WatchState.from_dict({"config_hash": "x"}) == WatchState()
