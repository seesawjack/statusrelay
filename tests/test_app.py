import json
import logging
from urllib.parse import urlparse

from statusrelay.app import RoundResult, run_round
from statusrelay.client import ApiError, RateLimited
from statusrelay.state import JsonFileStore
from statusrelay import app
from statusrelay.config import parse_config
from tests.helpers import (ACTIVITY_URL, Clock, FakeClient, RecordingNotifier, make_config, make_status,
                           static_payloads)

PLATFORM_HOST = urlparse(ACTIVITY_URL).hostname.split(".")[0]


def setup(tmp_path):
    return FakeClient(), JsonFileStore(tmp_path / "state.json"), RecordingNotifier(), Clock()


def saved(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def kinds(notifier):
    return [e.kind for e in notifier.events]


def test_first_round_sends_onsale_and_saves(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    assert run_round(make_config(), store, client, notifier, clock) == RoundResult(0, False)
    assert kinds(notifier) == ["onsale"]
    assert [a.area_id for a in notifier.events[0].areas] == ["a000000101", "a000000102"]
    entry = saved(tmp_path)["watches"]["Show Day 1"]
    assert entry["available"] == ["a000000101", "a000000102"]
    assert entry["resolved"]["session_short"] == "s000000001"
    assert entry["config_hash"].startswith("sha256:")
    assert len(client.static_calls) == 3 and len(client.status_calls) == 1


def test_logs_are_de_identified(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    client, store, notifier, clock = setup(tmp_path)
    run_round(make_config(), store, client, notifier, clock)
    run_round(make_config(session={"date": "2030-01-01"}), store, client, notifier, clock)
    text = caplog.text
    assert "watch #1: ok, 2 matched, 1 event(s)" in text
    assert "watch #1: failed (resolve error)" in text
    for leak in ("Show Day 1", "Sample", PLATFORM_HOST, "搖滾", "s000000001"):
        assert leak not in text


def test_cached_resolution_and_reminder(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    config = make_config()
    run_round(config, store, client, notifier, clock)
    clock.advance(minutes=5)
    run_round(config, store, client, notifier, clock)
    assert kinds(notifier) == ["onsale"] and len(client.static_calls) == 3
    clock.advance(minutes=5)
    run_round(config, store, client, notifier, clock)
    assert kinds(notifier) == ["onsale", "reminder"]


def test_failed_delivery_is_retried(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    notifier.fail = True
    assert run_round(make_config(), store, client, notifier, clock).exit_code == 1
    assert saved(tmp_path)["watches"]["Show Day 1"]["available"] == []
    notifier.fail = False
    run_round(make_config(), store, client, notifier, clock)
    assert kinds(notifier) == ["onsale"]


def test_api_failures_alert_then_recover(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    client.status = ApiError("HTTP 500")
    for _ in range(4):
        assert run_round(make_config(), store, client, notifier, clock).exit_code == 0
    assert kinds(notifier) == ["error"]
    assert notifier.events[0].detail == "API error: HTTP 500 (3 consecutive failures)"
    client.status = make_status()
    run_round(make_config(), store, client, notifier, clock)
    assert kinds(notifier) == ["error", "recovered", "onsale"]


def test_rate_limit_is_reported(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    client.status = RateLimited("HTTP 429")
    assert run_round(make_config(), store, client, notifier, clock).rate_limited is True


def test_unresolvable_session_alerts_immediately(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    run_round(make_config(session={"date": "2030-01-01"}), store, client, notifier, clock)
    assert kinds(notifier) == ["error"]
    assert "matched 0 sessions" in notifier.events[0].detail


def test_stale_resolution_refreshes_and_falls_back(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    config = make_config()
    run_round(config, store, client, notifier, clock)
    clock.advance(hours=25)
    client.static = dict(static_payloads(), sessions=ApiError("down"))
    run_round(config, store, client, notifier, clock)
    assert len(client.static_calls) == 4  # one failed refresh attempt
    assert len(client.status_calls) == 2  # still checked with cached IDs
    client.static = static_payloads()
    clock.advance(minutes=1)
    run_round(config, store, client, notifier, clock)
    assert len(client.static_calls) == 7


def test_config_change_forces_reresolve(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    run_round(make_config(), store, client, notifier, clock)
    run_round(make_config(filters={"max_price": 3000}), store, client, notifier, clock)
    assert len(client.static_calls) == 6
    assert saved(tmp_path)["watches"]["Show Day 1"]["available"] == ["a000000102"]


def test_corrupt_state_sends_notice(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    (tmp_path / "state.json").write_text("garbage", encoding="utf-8")
    run_round(make_config(), store, client, notifier, clock)
    assert kinds(notifier) == ["error", "onsale"]
    assert notifier.events[0].watch_name == "statusrelay"


def test_removed_watches_are_pruned(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"version": 1, "watches": {"Old": {}}}), encoding="utf-8")
    run_round(make_config(), store, client, notifier, clock)
    assert list(saved(tmp_path)["watches"]) == ["Show Day 1"]


def test_corrupt_watch_entry_is_treated_as_fresh(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    entry = {"available": ["a000000101"], "last_notified_at": "not-a-date", "resolved": {"bogus": 1}}
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "watches": {"Show Day 1": entry}}), encoding="utf-8")
    assert run_round(make_config(), store, client, notifier, clock).exit_code == 0
    assert kinds(notifier) == ["onsale"]
    assert saved(tmp_path)["watches"]["Show Day 1"]["resolved"]["session_short"] == "s000000001"


def test_malformed_upstream_record_is_an_api_error(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    client.static = dict(static_payloads(), sessions={"sessions": "nope"})
    assert run_round(make_config(), store, client, notifier, clock).exit_code == 0
    assert kinds(notifier) == []
    assert saved(tmp_path)["watches"]["Show Day 1"]["consecutive_errors"] == 1


FIXTURE_NAMES = ("Show Day 1", "Sample", PLATFORM_HOST, "搖滾", "s000000001", "e000000001", "Arena", "HTTP")


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name.startswith("statusrelay")]


def test_failure_paths_log_only_generic_labels(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    client, store, notifier, clock = setup(tmp_path)
    client.status = RateLimited("HTTP 429 Show Day 1")
    run_round(make_config(), store, client, notifier, clock)
    client.status = ApiError(f"HTTP 500 Sample {PLATFORM_HOST}")
    run_round(make_config(), store, client, notifier, clock)
    client.status = make_status()
    notifier.fail = True
    clock.advance(minutes=5)
    run_round(make_config(), store, client, notifier, clock)
    lines = _lines(caplog)
    assert "watch #1: failed (rate limited)" in lines
    assert "watch #1: failed (api error)" in lines
    assert any(line.startswith("notification failed") for line in lines)
    for line in lines:
        for leak in FIXTURE_NAMES:
            assert leak not in line, line


def _two_watch_config():
    second = {"name": "Show Day 2", "activity_url": ACTIVITY_URL, "session": {"date": "2026-10-31"}}
    return parse_config({
        "poll": {"reminder_minutes": 10},
        "notifiers": [{"type": "stdout"}],
        "watches": [{"name": "Show Day 1", "activity_url": ACTIVITY_URL, "session": {"date": "2026-10-31"}},
                    second],
    })


def test_unexpected_error_is_isolated_per_watch(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    client, store, notifier, clock = setup(tmp_path)
    real_check = app.check
    calls = []

    def flaky(watch, *args, **kwargs):
        calls.append(watch.name)
        if len(calls) == 1:
            raise RuntimeError("secret-name")
        return real_check(watch, *args, **kwargs)

    monkeypatch.setattr(app, "check", flaky)
    result = run_round(_two_watch_config(), store, client, notifier, clock)
    assert result == RoundResult(1, False)
    assert calls == ["Show Day 1", "Show Day 2"]
    assert [e.watch_name for e in notifier.events] == ["Show Day 2"]
    assert "watch #1: failed (internal error: RuntimeError)" in caplog.text
    assert "secret-name" not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


def test_base_exception_still_propagates(tmp_path, monkeypatch):
    client, store, notifier, clock = setup(tmp_path)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "check", interrupt)
    try:
        run_round(make_config(), store, client, notifier, clock)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt was swallowed")


def test_naive_stored_times_are_treated_as_corrupt(tmp_path):
    client, store, notifier, clock = setup(tmp_path)
    run_round(make_config(), store, client, notifier, clock)
    entry = saved(tmp_path)["watches"]["Show Day 1"]
    entry["resolved"]["resolved_at"] = "2026-10-11T11:00:00"
    entry["last_notified_at"] = "2026-10-11T11:00:00"
    entry["last_change_at"] = "2026-10-11T11:00:00"
    entry["last_onsale_at"] = "2026-10-11T11:00:00"
    (tmp_path / "state.json").write_text(json.dumps({"version": 1, "watches": {"Show Day 1": entry}}),
                                         encoding="utf-8")
    clock.advance(minutes=5)
    assert run_round(make_config(), store, client, notifier, clock).exit_code == 0
    healed = saved(tmp_path)["watches"]["Show Day 1"]
    assert healed["resolved"]["resolved_at"].endswith("+08:00")
    assert healed["last_notified_at"].endswith("+08:00")
