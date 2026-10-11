"""The only HTTP egress to the upstream API: public, read-only, throttled."""

import random
import time
from collections.abc import Callable, Iterable

import requests

from statusrelay import __version__
from statusrelay.ids import LONG_ID_RE

BASE = "https://apis.ticketplus.com.tw/config/api/v1"
ALLOWED_URLS = frozenset({f"{BASE}/getS3", f"{BASE}/get"})
USER_AGENT = f"statusrelay/{__version__} (+personal status relay)"
STATIC_FILES = frozenset({"sessions", "ticketAreas", "products"})


class ApiError(RuntimeError):
    """An upstream request failed or returned an unusable payload."""


class RateLimited(ApiError):
    """Upstream answered 403/429; callers should back off."""


class Throttle:
    """Space consecutive requests by a uniform random gap in [low, high] seconds."""

    def __init__(
        self,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        low: float = 1.0,
        high: float = 2.0,
    ):
        self.rng = rng or random.Random()
        self.sleep = sleep
        self.clock = clock
        self.low = low
        self.high = high
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.rng.uniform(self.low, self.high) - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()


class UpstreamClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        retries: int = 2,
    ):
        self.session = session or requests.Session()
        self.throttle = throttle or Throttle()
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.retries = retries

    def get_json(self, url: str, params: dict[str, str]) -> object:
        if url not in ALLOWED_URLS:
            raise ValueError(f"URL not allowed: {url}")
        last = ApiError("no attempt")
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(2**attempt + self.rng.uniform(0, 1))
            self.throttle.wait()
            try:
                resp = self.session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(5, 15))
            except requests.RequestException as exc:
                last = ApiError(f"network error: {type(exc).__name__}")
                continue
            finally:
                self.session.cookies.clear()  # never send cookies back
            if resp.status_code in (403, 429):
                raise RateLimited(f"HTTP {resp.status_code}")
            if resp.status_code >= 500:
                last = ApiError(f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise ApiError(f"HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                raise ApiError("invalid JSON") from exc
        raise last

    def get_static(self, event_long: str, name: str) -> dict:
        if not LONG_ID_RE.match(event_long):
            raise ValueError(f"bad event id: {event_long!r}")
        if name not in STATIC_FILES:
            raise ValueError(f"unknown static file: {name!r}")
        data = self.get_json(f"{BASE}/getS3", {"path": f"event/{event_long}/{name}.json"})
        if not isinstance(data, dict):
            raise ApiError(f"unexpected {name} payload")
        return data

    def get_status(
        self,
        *,
        event_ids: Iterable[str] = (),
        session_ids: Iterable[str] = (),
        area_ids: Iterable[str] = (),
        product_ids: Iterable[str] = (),
    ) -> dict:
        params = {}
        for key, ids in (
            ("eventId", event_ids),
            ("sessionId", session_ids),
            ("ticketAreaId", area_ids),
            ("productId", product_ids),
        ):
            if ids := list(ids):
                params[key] = ",".join(ids)
        data = self.get_json(f"{BASE}/get", params)
        if not isinstance(data, dict) or data.get("errCode") != "00" or not isinstance(data.get("result"), dict):
            code = data.get("errCode") if isinstance(data, dict) else None
            raise ApiError(f"bad status payload (errCode={code!r})")
        return data["result"]
