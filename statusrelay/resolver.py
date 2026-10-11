"""Turn an activity URL plus a session selector into IDs and an area catalog."""

import re
from dataclasses import dataclass
from datetime import datetime

from statusrelay.client import ApiError
from statusrelay.config import SessionSelector, Watch
from statusrelay.ids import IdDecodeError, event_short_id, session_short_id

ORDER_BASE = "https://ticketplus.com.tw/order"
_ACTIVITY_RE = re.compile(r"https://ticketplus\.com\.tw/activity/([0-9a-f]{32})/?(?:[?#].*)?")


class ResolveError(RuntimeError):
    """Config or upstream data does not identify exactly one usable session.

    Its message may contain session names/IDs: send it only to the private notification channel or local CLI output, never to logs.
    """


@dataclass(frozen=True)
class ResolvedArea:
    name: str
    product_ids: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    resolved_at: datetime
    event_long: str
    event_short: str
    session_long: str
    session_short: str
    order_url: str
    session_name: str
    session_time: str
    venue: str
    areas: dict[str, ResolvedArea]

    def to_dict(self) -> dict:
        return {
            "resolved_at": self.resolved_at.isoformat(),
            "event_long": self.event_long,
            "event_short": self.event_short,
            "session_long": self.session_long,
            "session_short": self.session_short,
            "order_url": self.order_url,
            "session_name": self.session_name,
            "session_time": self.session_time,
            "venue": self.venue,
            "areas": {aid: {"name": a.name, "product_ids": list(a.product_ids)} for aid, a in self.areas.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Resolution":
        fields = {k: d[k] for k in ("event_long", "event_short", "session_long", "session_short",
                                    "order_url", "session_name", "session_time", "venue")}
        areas = {aid: ResolvedArea(a["name"], tuple(a["product_ids"])) for aid, a in d["areas"].items()}
        return cls(resolved_at=datetime.fromisoformat(d["resolved_at"]), areas=areas, **fields)


def parse_activity_url(url: str) -> str:
    if match := _ACTIVITY_RE.fullmatch(url.strip()):
        return match.group(1)
    raise ResolveError("not a supported activity URL")


def records(payload: dict, key: str, *, required: tuple[str, ...] = ()) -> list[dict]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ApiError(f"{key} payload has no {key!r} list")
    rows = [r for r in value if isinstance(r, dict)]
    # Do not include record contents in the message: they may hold session names.
    if any(k not in r for r in rows for k in required):
        raise ApiError(f"malformed {key} payload")
    return rows


def _start(value: str) -> str:
    return value.split("~")[0].strip()


def session_time(session: dict) -> str:
    return f"{_start(session.get('date', ''))} {_start(session.get('time', ''))}".strip()


def describe_sessions(sessions: list[dict]) -> str:
    return "\n".join(f"  {session_time(s)}  {s.get('sessionId')}  {s.get('name', '')}" for s in sessions)


def select_session(sessions: list[dict], selector: SessionSelector) -> dict:
    if selector.session_id:
        kind, matches = "session_id", [s for s in sessions if s.get("sessionId") == selector.session_id]
    elif selector.date:
        kind, matches = "date", [s for s in sessions if _start(s.get("date", "")) == selector.date]
    else:
        kind, matches = "name_contains", [s for s in sessions if selector.name_contains in s.get("name", "")]
    if len(matches) != 1:
        # The selector value itself is not echoed: it comes from config.
        raise ResolveError(
            f"session selector ({kind}) matched {len(matches)} sessions; candidates:\n{describe_sessions(sessions)}"
        )
    return matches[0]


def resolve(watch: Watch, client, now: datetime) -> Resolution:
    event_long = parse_activity_url(watch.activity_url)
    sessions = [
        s for s in records(client.get_static(event_long, "sessions"), "sessions", required=("sessionId",))
        if not s.get("hidden")
    ]
    session = select_session(sessions, watch.session)
    session_long = session["sessionId"]
    try:
        event_short = watch.event_short or event_short_id(event_long)
        session_short = watch.session_short or session_short_id(session_long)
    except IdDecodeError as exc:
        raise ResolveError(f"{exc}; set event_short/session_short manually in config") from exc

    area_rows = sorted(
        (a for a in records(client.get_static(event_long, "ticketAreas"), "ticketAreas",
                            required=("ticketAreaId",))
         if a.get("sessionId") == session_long),
        key=lambda a: a.get("sortedIndex", 0),
    )
    products = [
        p for p in records(client.get_static(event_long, "products"), "products",
                           required=("productId", "ticketAreaId"))
        if p.get("sessionId") == session_long
    ]
    areas = {
        a["ticketAreaId"]: ResolvedArea(
            a.get("name", a["ticketAreaId"]),
            tuple(p["productId"] for p in products if p.get("ticketAreaId") == a["ticketAreaId"]),
        )
        for a in area_rows
    }
    if not areas:
        raise ResolveError(f"session {session_long} has no ticket areas")
    return Resolution(
        resolved_at=now,
        event_long=event_long,
        event_short=event_short,
        session_long=session_long,
        session_short=session_short,
        order_url=f"{ORDER_BASE}/{event_long}/{session_long}",
        session_name=session.get("name", ""),
        session_time=session_time(session),
        venue=session.get("location", ""),
        areas=areas,
    )
