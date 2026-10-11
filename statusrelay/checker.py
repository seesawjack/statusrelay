"""One availability check: query live status and decide which areas are buyable."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone

from statusrelay.client import ApiError
from statusrelay.config import Filters, Watch
from statusrelay.events import AreaInfo
from statusrelay.resolver import Resolution, ResolvedArea

BATCH_SIZE = 100
UPSTREAM_TZ = timezone(timedelta(hours=8))
type StatusMaps = dict[str, dict[str, dict]]


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_price(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_time(value) -> datetime | None:
    if value is None or value == "":
        return None
    if _is_number(value):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UPSTREAM_TZ)
    return parsed


def _chunks(ids: Sequence[str]) -> list[list[str]]:
    ids = list(ids)
    return [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]


def fetch_status(client, session_ids: Sequence[str], area_ids: Sequence[str], product_ids: Sequence[str]) -> StatusMaps:
    groups = [_chunks(session_ids), _chunks(area_ids), _chunks(product_ids)]
    rounds = max([1, *(len(g) for g in groups)])
    maps: StatusMaps = {"session": {}, "ticketArea": {}, "product": {}}
    for i in range(rounds):
        sessions, areas, products = (g[i] if i < len(g) else [] for g in groups)
        result = client.get_status(session_ids=sessions, area_ids=areas, product_ids=products)
        for key, by_id in maps.items():
            for record in result.get(key) or []:
                if isinstance(record, dict) and "id" in record:
                    by_id[record["id"]] = record
    return maps


def _visible(record: dict, now: datetime) -> bool:
    if record.get("hidden"):
        return False
    start, end = parse_time(record.get("exposeStart")), parse_time(record.get("exposeEnd"))
    if start and now < start:
        return False
    if end and now > end:
        return False
    return True


def _area_info(area_id: str, area: ResolvedArea, maps: StatusMaps, filters: Filters, now: datetime) -> AreaInfo | None:
    record = maps["ticketArea"].get(area_id)
    if record is None or record.get("status") != "onsale" or not _visible(record, now):
        return None
    name = record.get("ticketAreaName") or area.name
    if not filters.matches_name(name):
        return None
    candidates = [
        product
        for pid in area.product_ids
        if (product := maps["product"].get(pid))
        and product.get("status") == "onsale"
        and _visible(product, now)
        and _is_price(product.get("price"))
        and filters.matches_price(product["price"])
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda p: p["price"])
    limit = best.get("purchaseLimit")
    if limit is None:
        limit = best.get("userLimit")
    return AreaInfo(
        area_id=area_id,
        name=name,
        price=best["price"],
        status="onsale",
        sale_start=parse_time(record.get("saleStart")),
        sale_end=parse_time(record.get("saleEnd")),
        purchase_limit=limit,
    )


def check(watch: Watch, resolution: Resolution, client, now: datetime) -> dict[str, AreaInfo]:
    product_ids = list(dict.fromkeys(
        pid for area in resolution.areas.values() for pid in area.product_ids
    ))
    maps = fetch_status(client, [resolution.session_short], list(resolution.areas), product_ids)
    session = maps["session"].get(resolution.session_short)
    if session is None:
        raise ApiError("session missing from status response")
    if session.get("status") != "onsale":
        return {}
    return {
        area_id: info
        for area_id, area in resolution.areas.items()
        if (info := _area_info(area_id, area, maps, watch.filters, now)) is not None
    }
