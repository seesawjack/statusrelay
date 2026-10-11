"""One monitoring round across all watches.

Logs are de-identified (watch #n, counts, error categories) because CI logs
may be public. Details go only to notifications.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from statusrelay.checker import check
from statusrelay.client import ApiError, RateLimited
from statusrelay.config import Config, Watch
from statusrelay.events import Event
from statusrelay.notifier import Notifier, NotifyError
from statusrelay.resolver import Resolution, ResolveError, resolve
from statusrelay.rules import EventContext, WatchState, on_failure, on_success, rollback_notifications
from statusrelay.state import StateStore

log = logging.getLogger(__name__)

LOCAL_TZ = timezone(timedelta(hours=8))
RESOLUTION_TTL = timedelta(hours=24)
SYSTEM_WATCH = "statusrelay"


@dataclass(frozen=True)
class RoundResult:
    exit_code: int
    rate_limited: bool


def _deliver(notifier: Notifier, events: Iterable[Event]) -> bool:
    for event in events:
        try:
            notifier.notify(event)
        except NotifyError as exc:  # message is already redacted
            log.error("notification failed: %s", exc)
            return False
    return True


def _cached_resolution(entry: dict) -> Resolution | None:
    """The stored resolution, or None if absent or corrupt."""
    if "resolved" not in entry:
        return None
    try:
        resolution = Resolution.from_dict(entry["resolved"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    # Timezone-naive times cannot be compared with the aware clock: treat as corrupt.
    return resolution if resolution.resolved_at.tzinfo is not None else None


def _load_state(entry: dict) -> WatchState:
    try:
        state = WatchState.from_dict(entry)
    except (ValueError, TypeError, AttributeError):
        return WatchState()
    times = (state.last_change_at, state.last_notified_at, state.last_onsale_at)
    if any(t is not None and t.tzinfo is None for t in times):
        return WatchState()
    return state


def _ensure_resolution(label: str, watch: Watch, entry: dict, client, now: datetime) -> Resolution:
    cached = _cached_resolution(entry)
    if cached is not None and entry.get("config_hash") != watch.config_hash():
        cached = None
    if cached is not None and now - cached.resolved_at < RESOLUTION_TTL:
        return cached
    try:
        fresh = resolve(watch, client, now)
    except ApiError:
        if cached is not None:
            log.warning("%s: refresh failed; using cached resolution", label)
            return cached
        raise
    entry["config_hash"] = watch.config_hash()
    entry["resolved"] = fresh.to_dict()
    return fresh


def _context(watch: Watch, entry: dict) -> EventContext:
    if (cached := _cached_resolution(entry)) is not None:
        return EventContext.from_resolution(watch.name, cached)
    return EventContext(watch.name)


def _run_watch(index: int, watch: Watch, entry: dict, config: Config, client, notifier: Notifier,
               now_fn: Callable[[], datetime]) -> tuple[bool, bool]:
    label = f"watch #{index}"
    prev = _load_state(entry)
    now = now_fn()
    limited = False
    try:
        resolution = _ensure_resolution(label, watch, entry, client, now)
        current = check(watch, resolution, client, now)
    except ResolveError as exc:
        # The message may name sessions: it goes to the notification only.
        log.warning("%s: failed (resolve error)", label)
        events, new = on_failure(prev, EventContext(watch.name), now, str(exc), immediate=True)
    except RateLimited as exc:
        limited = True
        log.warning("%s: failed (rate limited)", label)
        events, new = on_failure(prev, _context(watch, entry), now, f"rate limited: {exc}")
    except ApiError as exc:
        log.warning("%s: failed (api error)", label)
        events, new = on_failure(prev, _context(watch, entry), now, f"API error: {exc}")
    else:
        names = {aid: area.name for aid, area in resolution.areas.items()}
        ctx = EventContext.from_resolution(watch.name, resolution)
        events, new = on_success(prev, current, ctx, now, config.reminder_minutes, names)
        log.info("%s: ok, %d matched, %d event(s)", label, len(current), len(events))
    delivered = _deliver(notifier, events)
    if not delivered:
        new = rollback_notifications(prev, new)
    entry.update(new.to_dict())
    return delivered, limited


def run_round(config: Config, store: StateStore, client, notifier: Notifier,
              now_fn: Callable[[], datetime]) -> RoundResult:
    loaded = store.load()
    data = loaded.data
    exit_code, rate_limited = 0, False
    try:
        if loaded.reset_reason:
            log.warning("state reset: %s", loaded.reset_reason)
            notice = Event("error", SYSTEM_WATCH, "", "", "", "", (), now_fn(), loaded.reset_reason)
            if not _deliver(notifier, [notice]):
                exit_code = 1
        names = {w.name for w in config.watches}
        for stale in set(data["watches"]) - names:
            del data["watches"][stale]
        for index, watch in enumerate(config.watches, start=1):
            entry = data["watches"].get(watch.name)
            if not isinstance(entry, dict):
                entry = data["watches"][watch.name] = {}
            try:
                delivered, limited = _run_watch(index, watch, entry, config, client, notifier, now_fn)
            except Exception as exc:  # noqa: BLE001 - isolate watches; log the type only (no message/traceback)
                log.error("watch #%d: failed (internal error: %s)", index, type(exc).__name__)
                delivered, limited = False, False
            if not delivered:
                exit_code = 1
            rate_limited = rate_limited or limited
    finally:
        store.save(data)
    return RoundResult(exit_code, rate_limited)
