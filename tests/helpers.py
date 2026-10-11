"""Shared test helpers. Later tasks append to this module."""

from datetime import datetime, timedelta, timezone

from statusrelay.events import AreaInfo, Event

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=TZ)
ORDER_URL = (
    "https://ticketplus.com.tw/order/"
    "8c47f9efe47df17d6381bcdb71309bfc/26ab2893376c2821a1e1efbfdb28c4f4"
)


def make_area(area_id="a000000101", name="搖滾A區", price=5880, **kw) -> AreaInfo:
    kw.setdefault("status", "onsale")
    kw.setdefault("purchase_limit", 2)
    return AreaInfo(area_id=area_id, name=name, price=price, **kw)


def make_event(kind="onsale", areas=None, watch_name="Show Day 1", **kw) -> Event:
    return Event(
        kind=kind,
        watch_name=watch_name,
        session_name=kw.pop("session_name", "Sample Live Tour（Day 1）"),
        session_time=kw.pop("session_time", "2026-10-31 19:00"),
        venue=kw.pop("venue", "Sample Arena"),
        order_url=kw.pop("order_url", ORDER_URL),
        areas=tuple(areas if areas is not None else [make_area()]),
        detected_at=kw.pop("detected_at", NOW),
        **kw,
    )
