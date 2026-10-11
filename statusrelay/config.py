"""Load and validate the config (from STATUSRELAY_CONFIG or a file).

Error messages never echo config values: they may reach public CI logs.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from statusrelay.notifier import NOTIFIERS

log = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 30
CONFIG_ENV = "STATUSRELAY_CONFIG"


class ConfigError(ValueError):
    """The configuration is invalid."""


@dataclass(frozen=True)
class SessionSelector:
    date: str | None = None
    name_contains: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Filters:
    area_name_contains: tuple[str, ...] = ()
    max_price: int | None = None
    min_price: int | None = None

    def matches_name(self, name: str) -> bool:
        return not self.area_name_contains or any(k in name for k in self.area_name_contains)

    def matches_price(self, price: int) -> bool:
        if self.max_price is not None and price > self.max_price:
            return False
        if self.min_price is not None and price < self.min_price:
            return False
        return True


@dataclass(frozen=True)
class Watch:
    name: str
    activity_url: str
    session: SessionSelector
    filters: Filters = field(default_factory=Filters)
    event_short: str | None = None
    session_short: str | None = None

    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Config:
    interval_seconds: int
    reminder_minutes: int
    notifiers: tuple[dict[str, Any], ...]
    watches: tuple[Watch, ...]


def _parse_yaml(text: str, source: str) -> Config:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"invalid YAML in {source}{where}") from None
    return parse_config(raw)


def load_config(path) -> Config:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc.strerror}") from None
    return _parse_yaml(text, str(path))


def load_config_from(path, env: Mapping[str, str]) -> Config:
    text = env.get(CONFIG_ENV, "")
    if text.strip():
        return _parse_yaml(text, CONFIG_ENV)
    return load_config(path)


def _check_keys(where: str, mapping: Any, allowed: set[str]) -> None:
    if not isinstance(mapping, dict):
        raise ConfigError(f"{where} must be a mapping")
    unknown = set(map(str, mapping)) - allowed
    if unknown:
        raise ConfigError(f"{where}: {len(unknown)} unknown key(s)")


def _opt_int(where: str, value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer")
    return value


def parse_config(raw: Any) -> Config:
    _check_keys("config", raw, {"poll", "notifiers", "watches"})
    poll = raw.get("poll") or {}
    _check_keys("poll", poll, {"interval_seconds", "reminder_minutes"})
    interval = _opt_int("poll.interval_seconds", poll.get("interval_seconds"), 60)
    if interval < MIN_INTERVAL_SECONDS:
        log.warning("poll.interval_seconds=%s raised to %s", interval, MIN_INTERVAL_SECONDS)
        interval = MIN_INTERVAL_SECONDS
    reminder = _opt_int("poll.reminder_minutes", poll.get("reminder_minutes"), 10)
    if reminder < 0:
        raise ConfigError("poll.reminder_minutes must be >= 0")

    notifiers = raw.get("notifiers")
    if not isinstance(notifiers, list) or not notifiers:
        raise ConfigError("notifiers must be a non-empty list")
    for i, spec in enumerate(notifiers):
        kind = spec.get("type") if isinstance(spec, dict) else None
        if not isinstance(kind, str) or kind not in NOTIFIERS:
            raise ConfigError(f"notifiers[{i}]: unknown type; known: {sorted(NOTIFIERS)}")

    watches_raw = raw.get("watches")
    if not isinstance(watches_raw, list) or not watches_raw:
        raise ConfigError("watches must be a non-empty list")
    watches = tuple(_parse_watch(i, w) for i, w in enumerate(watches_raw))
    seen: set[str] = set()
    for i, w in enumerate(watches):
        if w.name in seen:
            raise ConfigError(f"duplicate watch name at watches[{i}]")
        seen.add(w.name)
    return Config(interval, reminder, tuple(dict(n) for n in notifiers), watches)


def _parse_watch(i: int, raw: Any) -> Watch:
    where = f"watches[{i}]"
    _check_keys(where, raw, {"name", "activity_url", "session", "filters", "event_short", "session_short"})
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}.name is required")
    url = raw.get("activity_url")
    if not isinstance(url, str) or not url.strip():
        raise ConfigError(f"{where}.activity_url is required")

    selector_raw = raw.get("session")
    _check_keys(f"{where}.session", selector_raw, {"date", "name_contains", "session_id"})
    given = {k: str(v) for k, v in selector_raw.items() if v is not None}  # str() normalises YAML dates
    if len(given) != 1:
        raise ConfigError(f"{where}.session needs exactly one of date, name_contains, session_id")

    filters_raw = raw.get("filters") or {}
    _check_keys(f"{where}.filters", filters_raw, {"area_name_contains", "max_price", "min_price"})
    keywords = filters_raw.get("area_name_contains") or ()
    if isinstance(keywords, str):
        keywords = (keywords,)
    if not isinstance(keywords, list | tuple):
        raise ConfigError(f"{where}.filters.area_name_contains must be a string or a list")
    filters = Filters(
        area_name_contains=tuple(str(k) for k in keywords),
        max_price=_opt_int(f"{where}.filters.max_price", filters_raw.get("max_price")),
        min_price=_opt_int(f"{where}.filters.min_price", filters_raw.get("min_price")),
    )
    return Watch(
        name=name.strip(),
        activity_url=url.strip(),
        session=SessionSelector(**given),
        filters=filters,
        event_short=raw.get("event_short"),
        session_short=raw.get("session_short"),
    )
