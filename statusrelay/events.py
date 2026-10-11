"""Structured, channel-independent notification events."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

UPSTREAM_TZ = timezone(timedelta(hours=8))
type EventKind = Literal["onsale", "reminder", "soldout", "error", "recovered"]


@dataclass(frozen=True)
class AreaInfo:
    area_id: str
    name: str
    price: int
    status: str
    sale_start: datetime | None = None
    sale_end: datetime | None = None
    purchase_limit: int | None = None
    is_new: bool = False


@dataclass(frozen=True)
class Event:
    kind: EventKind
    watch_name: str
    session_name: str
    session_time: str
    venue: str
    order_url: str
    areas: tuple[AreaInfo, ...]
    detected_at: datetime
    detail: str | None = None
