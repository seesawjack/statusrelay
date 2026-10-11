"""Turn events into channel-neutral messages. Subclass to customise."""

import html
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from statusrelay.events import UPSTREAM_TZ, AreaInfo, Event

@dataclass(frozen=True)
class Message:
    title: str
    body: str
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class MessageFormatter(ABC):
    """Template-method formatter: override any hook, implement `escape`."""

    TITLES: dict[str, str] = {
        "onsale": "On sale",
        "reminder": "Still on sale",
        "soldout": "Sold out again",
        "error": "Monitor error",
        "recovered": "Monitor recovered",
    }

    def format(self, event: Event) -> Message:
        handler = getattr(self, f"format_{event.kind}", None)
        if not callable(handler):
            raise ValueError(f"unsupported event kind: {event.kind!r}")
        return handler(event)

    def format_onsale(self, e: Event) -> Message:
        return Message(self.title(e), self.body(e), e.order_url or None)

    def format_reminder(self, e: Event) -> Message:
        return Message(self.title(e), self.body(e), e.order_url or None)

    def format_soldout(self, e: Event) -> Message:
        lines = [self.session_line(e), self.escape("No matching areas are on sale anymore:")]
        lines += [self.escape(f"- {a.name}") for a in e.areas]
        return Message(self.title(e), "\n".join(lines), e.order_url or None)

    def format_error(self, e: Event) -> Message:
        return Message(self.title(e), self.escape(e.detail or "unknown error"))

    def format_recovered(self, e: Event) -> Message:
        return Message(self.title(e), self.escape(e.detail or "checks are succeeding again"))

    def title(self, e: Event) -> str:
        return self.escape(f"[{self.TITLES[e.kind]}] {e.watch_name}")

    def session_line(self, e: Event) -> str:
        return self.escape(f"{e.session_name} | {e.session_time} | {e.venue}")

    def body(self, e: Event) -> str:
        lines = [self.session_line(e)]
        lines += [self.format_area(a) for a in e.areas]
        lines += [self.escape(e.order_url), self.escape(self.disclaimer())]
        return "\n".join(lines)

    def format_area(self, a: AreaInfo) -> str:
        text = f"{a.name}  NT${a.price:,}"
        if a.purchase_limit:
            text += f" (limit {a.purchase_limit})"
        if a.sale_end:
            text += f" | sale until {a.sale_end.astimezone(UPSTREAM_TZ):%m/%d %H:%M}"
        if a.is_new:
            text = "NEW " + text
        return self.escape(text)

    def disclaimer(self) -> str:
        return "Listed as on sale; actual seats per official page."

    @abstractmethod
    def escape(self, text: str) -> str:
        """Escape text for the target channel."""


class PlainTextFormatter(MessageFormatter):
    def escape(self, text: str) -> str:
        return text


class TelegramFormatter(PlainTextFormatter):
    """For Telegram `parse_mode=HTML`."""

    def escape(self, text: str) -> str:
        return html.escape(text, quote=False)

    def title(self, e: Event) -> str:
        return f"<b>{super().title(e)}</b>"
