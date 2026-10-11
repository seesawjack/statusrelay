"""Shared test helpers. Later tasks append to this module."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from statusrelay.config import parse_config
from statusrelay.events import AreaInfo, Event
from statusrelay.notifier import Notifier, NotifyError

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


FIXTURES = Path(__file__).parent / "fixtures"
EVENT_LONG = "8c47f9efe47df17d6381bcdb71309bfc"      # e000000001
SESSION_DAY1 = "26ab2893376c2821a1e1efbfdb28c4f4"    # s000000001
SESSION_DAY2 = "a0cd115cc2af7ef784fb6ee2414841c5"    # s000000002
ACTIVITY_URL = f"https://ticketplus.com.tw/activity/{EVENT_LONG}"

# area_id -> (session short id, name, price); product_id -> (area_id, price)
AREAS = {
    "a000000101": ("s000000001", "搖滾A區", 5880),
    "a000000102": ("s000000001", "搖滾B區", 5880),
    "a000000201": ("s000000002", "看台2樓", 2880),
}
PRODUCTS = {
    "p000001011": ("a000000101", 5880),
    "p000001021": ("a000000102", 5880),
    "p000001022": ("a000000102", 2940),
    "p000002011": ("a000000201", 2880),
}
WINDOW = {"exposeStart": "2026-05-19T14:36:00+08:00", "exposeEnd": "2026-11-02T00:00:00+08:00"}


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def static_payloads():
    return {
        "sessions": load_fixture("sessions.json"),
        "ticketAreas": load_fixture("ticketAreas.json"),
        "products": load_fixture("products.json"),
    }


def make_status(session_status="onsale", area_status=None, product_status=None,
                area_overrides=None, product_overrides=None):
    """Build a full status-API `result` for the fixture event."""
    area_status = area_status or {}
    product_status = product_status or {}
    area_overrides = area_overrides or {}
    product_overrides = product_overrides or {}
    sessions = [{"id": s, "status": session_status, "hidden": False} for s in ("s000000001", "s000000002")]
    areas = []
    for aid, (sid, name, price) in AREAS.items():
        rec = {"id": aid, "sessionId": sid, "ticketAreaName": name, "price": price,
               "status": area_status.get(aid, "onsale"), "hidden": False,
               "saleStart": "2026-05-23T11:00:00+08:00", "saleEnd": "2026-05-23T14:00:00+08:00", **WINDOW}
        rec.update(area_overrides.get(aid, {}))
        areas.append(rec)
    products = []
    for pid, (aid, price) in PRODUCTS.items():
        rec = {"id": pid, "ticketAreaId": aid, "sessionId": AREAS[aid][0], "price": price,
               "status": product_status.get(pid, "onsale"), "hidden": False,
               "purchaseLimit": 2, "userLimit": 2, **WINDOW}
        rec.update(product_overrides.get(pid, {}))
        products.append(rec)
    return {"session": sessions, "ticketArea": areas, "product": products}


class FakeClient:
    """Stands in for UpstreamClient. Values that are exceptions are raised."""

    def __init__(self, static=None, status=None):
        self.static = static if static is not None else static_payloads()
        self.status = status if status is not None else make_status()
        self.static_calls = []
        self.status_calls = []

    def get_static(self, event_long, name):
        self.static_calls.append((event_long, name))
        value = self.static[name]
        if isinstance(value, Exception):
            raise value
        return value

    def get_status(self, *, event_ids=(), session_ids=(), area_ids=(), product_ids=()):
        call = {"session_ids": tuple(session_ids), "area_ids": tuple(area_ids), "product_ids": tuple(product_ids)}
        self.status_calls.append(call)
        if isinstance(self.status, Exception):
            raise self.status
        return {
            "session": [r for r in self.status.get("session", []) if r["id"] in call["session_ids"]],
            "ticketArea": [r for r in self.status.get("ticketArea", []) if r["id"] in call["area_ids"]],
            "product": [r for r in self.status.get("product", []) if r["id"] in call["product_ids"]],
        }


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kwargs):
        self.t += timedelta(**kwargs)


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.events = []
        self.fail = False

    def notify(self, event):
        if self.fail:
            raise NotifyError("boom")
        self.events.append(event)

    def send(self, message):
        raise AssertionError("RecordingNotifier records events, not messages")


def make_config(reminder_minutes=10, **watch_overrides):
    watch = {"name": "Show Day 1", "activity_url": ACTIVITY_URL, "session": {"date": "2026-10-31"}}
    watch.update(watch_overrides)
    return parse_config({
        "poll": {"reminder_minutes": reminder_minutes},
        "notifiers": [{"type": "stdout"}],
        "watches": [watch],
    })
