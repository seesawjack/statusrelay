import pytest

from statusrelay.client import ApiError
from statusrelay.config import SessionSelector, Watch
from statusrelay.resolver import Resolution, ResolveError, parse_activity_url, records, resolve
from tests.helpers import (
    ACTIVITY_URL,
    EVENT_LONG,
    NOW,
    SESSION_DAY1,
    SESSION_DAY2,
    FakeClient,
    static_payloads,
)


def watch(**selector):
    kw = {k: selector.pop(k) for k in ("event_short", "session_short") if k in selector}
    return Watch("w", ACTIVITY_URL, SessionSelector(**selector), **kw)


def test_parse_activity_url():
    assert parse_activity_url(ACTIVITY_URL) == EVENT_LONG
    assert parse_activity_url(ACTIVITY_URL + "/") == EVENT_LONG
    with pytest.raises(ResolveError, match="not a supported activity URL"):
        parse_activity_url("https://example.com/activity/" + EVENT_LONG)


@pytest.mark.parametrize("suffix", ["?utm_source=x", "/?utm_source=x", "#section", "/#section"])
def test_parse_activity_url_accepts_query_and_fragment(suffix):
    assert parse_activity_url(ACTIVITY_URL + suffix) == EVENT_LONG


def test_parse_activity_url_rejects_extra_path_segment():
    with pytest.raises(ResolveError, match="not a supported activity URL"):
        parse_activity_url(ACTIVITY_URL + "/extra")


def test_resolve_by_date():
    client = FakeClient()
    r = resolve(watch(date="2026-10-31"), client, NOW)
    assert (r.event_short, r.session_long, r.session_short) == ("e000000001", SESSION_DAY1, "s000000001")
    assert r.order_url == f"https://ticketplus.com.tw/order/{EVENT_LONG}/{SESSION_DAY1}"
    assert r.session_time == "2026-10-31 19:00"
    assert r.venue == "Sample Arena"
    assert list(r.areas) == ["a000000101", "a000000102"]  # sorted by sortedIndex
    assert r.areas["a000000102"].product_ids == ("p000001021", "p000001022")
    assert r.resolved_at == NOW
    assert [name for _, name in client.static_calls] == ["sessions", "ticketAreas", "products"]


def test_resolve_by_name_and_by_id():
    assert resolve(watch(name_contains="Day 2"), FakeClient(), NOW).session_short == "s000000002"
    r = resolve(watch(session_id=SESSION_DAY2), FakeClient(), NOW)
    assert list(r.areas) == ["a000000201"]


def test_no_match_lists_candidates():
    with pytest.raises(ResolveError) as info:
        resolve(watch(date="2030-01-01"), FakeClient(), NOW)
    assert "matched 0 sessions" in str(info.value)
    assert SESSION_DAY1 in str(info.value)
    assert "ffffffff" not in str(info.value)  # hidden sessions are not candidates


def test_multiple_matches():
    with pytest.raises(ResolveError, match="matched 2 sessions"):
        resolve(watch(name_contains="Sample Live"), FakeClient(), NOW)


def test_manual_short_ids_override_decoding():
    r = resolve(watch(date="2026-10-31", event_short="e000000009", session_short="s000000009"), FakeClient(), NOW)
    assert (r.event_short, r.session_short) == ("e000000009", "s000000009")


def test_undecodable_session_id_is_resolve_error():
    client = FakeClient()
    client.static["sessions"] = {"sessions": [dict(client.static["sessions"]["sessions"][0], sessionId="0" * 32)]}
    with pytest.raises(ResolveError, match="session_short"):
        resolve(watch(date="2026-10-31"), client, NOW)


def test_session_without_areas():
    client = FakeClient()
    client.static["ticketAreas"] = {"ticketAreas": []}
    with pytest.raises(ResolveError, match="no ticket areas"):
        resolve(watch(date="2026-10-31"), client, NOW)


def test_records_validates_payload():
    assert records({"sessions": [{"a": 1}]}, "sessions") == [{"a": 1}]
    with pytest.raises(ApiError):
        records({"oops": []}, "sessions")


def test_records_requires_keys():
    payload = {"sessions": [{"sessionId": "x"}, {"name": "secret session name"}]}
    with pytest.raises(ApiError) as info:
        records(payload, "sessions", required=("sessionId",))
    assert str(info.value) == "malformed sessions payload"


@pytest.mark.parametrize(
    ("key", "required"),
    [
        ("sessions", ("sessionId",)),
        ("ticketAreas", ("ticketAreaId",)),
        ("products", ("productId", "ticketAreaId")),
    ],
)
def test_resolve_rejects_malformed_upstream_record(key, required):
    static = static_payloads()
    victim = static[key][key][0]
    for field in required:
        victim.pop(field, None)
    with pytest.raises(ApiError) as info:
        resolve(watch(date="2026-10-31"), FakeClient(static=static), NOW)
    assert str(info.value) == f"malformed {key} payload"


def test_round_trip():
    r = resolve(watch(date="2026-10-31"), FakeClient(), NOW)
    assert Resolution.from_dict(r.to_dict()) == r
