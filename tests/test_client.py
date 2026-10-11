import random

import pytest
import requests
import responses
from responses import matchers

from statusrelay.client import BASE, ApiError, RateLimited, Throttle, UpstreamClient

EVENT_LONG = "8c47f9efe47df17d6381bcdb71309bfc"
GET = f"{BASE}/get"
GETS3 = f"{BASE}/getS3"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def make_client(sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return UpstreamClient(
        throttle=Throttle(low=0, high=0, sleep=lambda s: None),
        sleep=sleeps.append,
        rng=random.Random(0),
    )


def test_throttle_spacing_is_random_between_1_and_2_seconds():
    clock = FakeClock()
    throttle = Throttle(rng=random.Random(7), sleep=clock.sleep, clock=clock)
    for _ in range(50):
        throttle.wait()
    assert len(clock.sleeps) == 49
    assert all(1.0 <= s <= 2.0 for s in clock.sleeps)
    assert len(set(clock.sleeps)) > 1


def test_throttle_skips_sleep_when_enough_time_passed():
    clock = FakeClock()
    throttle = Throttle(rng=random.Random(1), sleep=clock.sleep, clock=clock)
    throttle.wait()
    clock.t += 5
    throttle.wait()
    assert clock.sleeps == []


def test_allowlist_rejects_other_urls():
    with pytest.raises(ValueError, match="^URL not allowed$"):
        make_client().get_json("https://example.com/x", {})


@responses.activate
def test_get_static_sends_path_and_honest_user_agent_without_cookies():
    responses.add(
        responses.GET, GETS3, json={"sessions": []},
        headers={"Set-Cookie": "sid=abc; Path=/"},
        match=[matchers.query_param_matcher({"path": f"event/{EVENT_LONG}/sessions.json"})],
    )
    responses.add(responses.GET, GETS3, json={"products": []})
    client = make_client()
    assert client.get_static(EVENT_LONG, "sessions") == {"sessions": []}
    assert len(client.session.cookies) == 0
    client.get_static(EVENT_LONG, "products")
    first, second = responses.calls[0].request, responses.calls[1].request
    assert first.headers["User-Agent"] == "statusrelay/0.1.0 (+personal status relay)"
    assert "Cookie" not in second.headers
    assert "Authorization" not in first.headers


def test_get_static_validates_inputs():
    client = make_client()
    with pytest.raises(ValueError, match="^bad event id$"):
        client.get_static("../../etc", "sessions")
    with pytest.raises(ValueError):
        client.get_static(EVENT_LONG, "seats")


@responses.activate
def test_get_static_rejects_trailing_newline_before_any_request():
    client = make_client()
    with pytest.raises(ValueError, match="^bad event id$"):
        client.get_static(EVENT_LONG + "\n", "sessions")
    assert len(responses.calls) == 0


@responses.activate
def test_get_status_requires_at_least_one_id_before_any_request():
    client = make_client()
    with pytest.raises(ValueError, match="^no ids given$"):
        client.get_status()
    assert len(responses.calls) == 0


@responses.activate
def test_redirects_are_not_followed():
    responses.add(responses.GET, GET, status=302, headers={"Location": "https://other.example/evil"})
    with pytest.raises(ApiError, match="HTTP 302"):
        make_client().get_status(session_ids=["s1"])
    assert len(responses.calls) == 1


def test_default_session_ignores_ambient_credentials_and_proxies():
    assert UpstreamClient().session.trust_env is False


@responses.activate
def test_get_status_joins_ids_and_returns_result():
    responses.add(
        responses.GET, GET,
        json={"errCode": "00", "result": {"session": [{"id": "s000000001"}]}},
        match=[matchers.query_param_matcher({"sessionId": "s000000001", "ticketAreaId": "a1,a2"})],
    )
    result = make_client().get_status(session_ids=["s000000001"], area_ids=["a1", "a2"])
    assert result == {"session": [{"id": "s000000001"}]}


@responses.activate
@pytest.mark.parametrize("payload", [{"errCode": "999"}, {"errCode": "00"}, ["not", "a", "dict"]])
def test_get_status_rejects_bad_payload(payload):
    responses.add(responses.GET, GET, json=payload)
    with pytest.raises(ApiError):
        make_client().get_status(session_ids=["s1"])


@responses.activate
def test_invalid_json_is_api_error():
    responses.add(responses.GET, GET, body="<html>")
    with pytest.raises(ApiError, match="invalid JSON"):
        make_client().get_status(session_ids=["s1"])


@responses.activate
def test_retries_5xx_with_backoff_then_succeeds():
    responses.add(responses.GET, GET, status=502)
    responses.add(responses.GET, GET, status=503)
    responses.add(responses.GET, GET, json={"errCode": "00", "result": {}})
    sleeps = []
    assert make_client(sleeps).get_status(session_ids=["s1"]) == {}
    assert len(sleeps) == 2
    assert 2 <= sleeps[0] <= 3 and 4 <= sleeps[1] <= 5


@responses.activate
def test_gives_up_after_retries():
    for _ in range(3):
        responses.add(responses.GET, GET, body=requests.ConnectionError("boom"))
    with pytest.raises(ApiError, match="network error"):
        make_client().get_status(session_ids=["s1"])
    assert len(responses.calls) == 3


@responses.activate
@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_raises_immediately(status):
    responses.add(responses.GET, GET, status=status)
    with pytest.raises(RateLimited):
        make_client().get_status(session_ids=["s1"])
    assert len(responses.calls) == 1


@responses.activate
def test_other_4xx_not_retried():
    responses.add(responses.GET, GET, status=404)
    with pytest.raises(ApiError, match="HTTP 404"):
        make_client().get_status(session_ids=["s1"])
    assert len(responses.calls) == 1
