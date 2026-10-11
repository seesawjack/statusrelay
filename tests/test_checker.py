from datetime import UTC, datetime, timedelta

import pytest

from statusrelay.checker import check, fetch_status, parse_time
from statusrelay.client import ApiError
from statusrelay.config import Filters, SessionSelector, Watch
from statusrelay.resolver import Resolution, ResolvedArea, resolve
from tests.helpers import ACTIVITY_URL, NOW, TZ, FakeClient, make_status


def setup(status=None, filters=Filters()):
    w = Watch("w", ACTIVITY_URL, SessionSelector(date="2026-10-31"), filters)
    client = FakeClient(status=status if status is not None else make_status())
    return w, resolve(w, client, NOW), client


def test_all_on_sale_single_request():
    w, r, client = setup()
    available = check(w, r, client, NOW)
    assert list(available) == ["a000000101", "a000000102"]
    a = available["a000000101"]
    assert (a.name, a.price, a.purchase_limit, a.status) == ("搖滾A區", 5880, 2, "onsale")
    assert a.sale_start == datetime(2026, 5, 23, 11, 0, tzinfo=TZ)
    assert available["a000000102"].price == 2940  # cheapest on-sale product
    assert client.status_calls == [{
        "session_ids": ("s000000001",),
        "area_ids": ("a000000101", "a000000102"),
        "product_ids": ("p000001011", "p000001021", "p000001022"),
    }]


def test_session_not_on_sale():
    w, r, client = setup(make_status(session_status="soldout"))
    assert check(w, r, client, NOW) == {}


@pytest.mark.parametrize(
    "status_kwargs",
    [
        {"area_status": {"a000000101": "soldout"}},
        {"area_overrides": {"a000000101": {"hidden": True}}},
        {"product_status": {"p000001011": "soldout"}},
        {"product_overrides": {"p000001011": {"hidden": True}}},
        {"area_overrides": {"a000000101": {"exposeEnd": "2026-10-01T00:00:00+08:00"}}},
        {"area_overrides": {"a000000101": {"exposeStart": "2026-10-12T00:00:00+08:00"}}},
    ],
)
def test_area_a_excluded(status_kwargs):
    w, r, client = setup(make_status(**status_kwargs))
    assert list(check(w, r, client, NOW)) == ["a000000102"]


def test_area_missing_from_response_is_excluded():
    status = make_status()
    status["ticketArea"] = [rec for rec in status["ticketArea"] if rec["id"] != "a000000101"]
    w, r, client = setup(status)
    assert list(check(w, r, client, NOW)) == ["a000000102"]


def test_session_missing_is_api_error():
    status = make_status()
    status["session"] = []
    w, r, client = setup(status)
    with pytest.raises(ApiError, match="missing"):
        check(w, r, client, NOW)


def test_name_filter():
    w, r, client = setup(filters=Filters(area_name_contains=("A區",)))
    assert list(check(w, r, client, NOW)) == ["a000000101"]


def test_price_filters_pick_matching_product():
    w, r, client = setup(filters=Filters(min_price=3000))
    assert check(w, r, client, NOW)["a000000102"].price == 5880
    w, r, client = setup(filters=Filters(max_price=3000))
    assert list(check(w, r, client, NOW)) == ["a000000102"]


def test_batches_above_100_ids():
    areas = {f"a{i:09d}": ResolvedArea(f"Area {i}", (f"p{i:09d}",)) for i in range(250)}
    r = Resolution(NOW, "e" * 32, "e000000001", "f" * 32, "s000000001", "url", "n", "t", "v", areas)
    client = FakeClient(status={"session": [{"id": "s000000001", "status": "onsale"}], "ticketArea": [], "product": []})
    w = Watch("w", ACTIVITY_URL, SessionSelector(date="x"))
    assert check(w, r, client, NOW) == {}
    assert [len(c["area_ids"]) for c in client.status_calls] == [100, 100, 50]
    assert [len(c["product_ids"]) for c in client.status_calls] == [100, 100, 50]
    assert [c["session_ids"] for c in client.status_calls] == [("s000000001",), (), ()]


def test_fetch_status_with_no_ids_makes_one_call():
    client = FakeClient()
    maps = fetch_status(client, ["s000000001"], [], [])
    assert set(maps["session"]) == {"s000000001"}
    assert len(client.status_calls) == 1


def test_parse_time():
    assert parse_time(None) is None
    assert parse_time("garbage") is None
    assert parse_time(1779172560000) == datetime(2026, 5, 19, 6, 36, tzinfo=UTC)
    assert parse_time("2026-05-23T11:00:00+08:00").utcoffset() == timedelta(hours=8)
