"""Pure transition rules: (previous state, current availability, now) -> (events, new state)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from statusrelay.events import AreaInfo, Event, EventKind
from statusrelay.resolver import Resolution

ERROR_THRESHOLD = 3
DEBOUNCE = timedelta(minutes=2)
_TIME_FIELDS = ("last_change_at", "last_notified_at", "last_onsale_at")


@dataclass(frozen=True)
class WatchState:
    available: tuple[str, ...] = ()
    last_change_at: datetime | None = None
    last_notified_at: datetime | None = None
    last_onsale_at: datetime | None = None
    last_onsale_signature: str | None = None
    consecutive_errors: int = 0
    error_alerted: bool = False

    def to_dict(self) -> dict:
        data = {
            "available": list(self.available),
            "last_onsale_signature": self.last_onsale_signature,
            "consecutive_errors": self.consecutive_errors,
            "error_alerted": self.error_alerted,
        }
        for name in _TIME_FIELDS:
            value = getattr(self, name)
            data[name] = value.isoformat() if value else None
        return data

    @classmethod
    def from_dict(cls, d: Mapping) -> "WatchState":
        times = {name: datetime.fromisoformat(d[name]) if d.get(name) else None for name in _TIME_FIELDS}
        return cls(
            available=tuple(d.get("available") or ()),
            last_onsale_signature=d.get("last_onsale_signature"),
            consecutive_errors=int(d.get("consecutive_errors") or 0),
            error_alerted=bool(d.get("error_alerted")),
            **times,
        )


@dataclass(frozen=True)
class EventContext:
    watch_name: str
    session_name: str = ""
    session_time: str = ""
    venue: str = ""
    order_url: str = ""

    @classmethod
    def from_resolution(cls, watch_name: str, resolution: Resolution) -> "EventContext":
        return cls(watch_name, resolution.session_name, resolution.session_time, resolution.venue, resolution.order_url)


def _event(ctx: EventContext, kind: EventKind, areas: Iterable[AreaInfo], now: datetime,
           detail: str | None = None) -> Event:
    return Event(kind, ctx.watch_name, ctx.session_name, ctx.session_time, ctx.venue, ctx.order_url,
                 tuple(areas), now, detail)


def _signature(ids: Iterable[str]) -> str:
    return ",".join(sorted(ids))


def on_success(
    prev: WatchState,
    current: Mapping[str, AreaInfo],
    ctx: EventContext,
    now: datetime,
    reminder_minutes: int,
    area_names: Mapping[str, str],
) -> tuple[list[Event], WatchState]:
    events: list[Event] = []
    new = replace(prev, consecutive_errors=0)
    if prev.error_alerted:
        events.append(_event(ctx, "recovered", (), now, "checks are succeeding again"))
        new = replace(new, error_alerted=False)

    prev_set, cur_set = set(prev.available), set(current)
    new = replace(new, available=tuple(current))
    if cur_set != prev_set:
        new = replace(new, last_change_at=now)

    if not prev_set and not cur_set:
        return events, new

    if not cur_set:
        gone = [AreaInfo(aid, area_names.get(aid, aid), 0, "soldout") for aid in prev.available]
        events.append(_event(ctx, "soldout", gone, now))
        return events, replace(new, last_notified_at=now)

    if added := cur_set - prev_set:
        signature = _signature(cur_set)
        if (prev_set and signature == prev.last_onsale_signature and prev.last_onsale_at is not None
                and now - prev.last_onsale_at < DEBOUNCE):
            return events, new
        areas = [replace(a, is_new=bool(prev_set) and a.area_id in added) for a in current.values()]
        events.append(_event(ctx, "onsale", areas, now))
        return events, replace(new, last_notified_at=now, last_onsale_at=now, last_onsale_signature=signature)

    due = prev.last_notified_at is None or now - prev.last_notified_at >= timedelta(minutes=reminder_minutes)
    if reminder_minutes > 0 and due:
        events.append(_event(ctx, "reminder", current.values(), now))
        new = replace(new, last_notified_at=now)
    return events, new


def on_failure(
    prev: WatchState, ctx: EventContext, now: datetime, detail: str, immediate: bool = False
) -> tuple[list[Event], WatchState]:
    new = replace(prev, consecutive_errors=prev.consecutive_errors + 1)
    if not prev.error_alerted and (immediate or new.consecutive_errors >= ERROR_THRESHOLD):
        text = f"{detail} ({new.consecutive_errors} consecutive failures)"
        return [_event(ctx, "error", (), now, text)], replace(new, error_alerted=True)
    return [], new


def rollback_notifications(prev: WatchState, new: WatchState) -> WatchState:
    """Undo everything that assumed delivery succeeded, so the next round re-emits."""
    return replace(
        new,
        available=prev.available,
        last_change_at=prev.last_change_at,
        last_notified_at=prev.last_notified_at,
        last_onsale_at=prev.last_onsale_at,
        last_onsale_signature=prev.last_onsale_signature,
        error_alerted=prev.error_alerted,
    )
