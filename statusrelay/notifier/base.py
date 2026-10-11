"""Notifier abstraction, registry and fan-out."""

import importlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping

from statusrelay.events import Event
from statusrelay.message import Message, MessageFormatter, PlainTextFormatter

log = logging.getLogger(__name__)

NOTIFIERS: dict[str, type["Notifier"]] = {}


class NotifyError(RuntimeError):
    """Delivery failed."""


def register(name: str) -> Callable[[type["Notifier"]], type["Notifier"]]:
    def deco(cls: type["Notifier"]) -> type["Notifier"]:
        NOTIFIERS[name] = cls
        return cls

    return deco


class Notifier(ABC):
    formatter_cls: type[MessageFormatter] = PlainTextFormatter

    def __init__(self, formatter: MessageFormatter | None = None):
        self.formatter = formatter or self.formatter_cls()

    @classmethod
    def from_config(cls, options: dict, env: Mapping[str, str], formatter: MessageFormatter | None = None):
        return cls(formatter=formatter, **options)

    def notify(self, event: Event) -> None:
        self.send(self.formatter.format(event))

    @abstractmethod
    def send(self, message: Message) -> None:
        """Deliver one message or raise NotifyError."""


class MultiNotifier(Notifier):
    """Fan out to children; succeed if at least one child succeeds."""

    def __init__(self, children: Iterable[Notifier]):
        super().__init__()
        self.children: list[Notifier] = list(children)

    def _fan_out(self, call: Callable[[Notifier], None]) -> None:
        failures = 0
        for child in self.children:
            try:
                call(child)
            except Exception as exc:  # one broken channel must not block the others
                failures += 1
                log.error("notifier %s failed: %s", type(child).__name__, exc)
        if self.children and failures == len(self.children):
            raise NotifyError(f"all {failures} notifier(s) failed")

    def notify(self, event: Event) -> None:
        self._fan_out(lambda child: child.notify(event))

    def send(self, message: Message) -> None:
        self._fan_out(lambda child: child.send(message))


def load_formatter(path: str) -> type[MessageFormatter]:
    module_name, _, attr = path.rpartition(".")
    if not module_name:
        raise NotifyError("formatter must be a dotted path")
    try:
        cls = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError):
        raise NotifyError("cannot load formatter") from None
    if not (isinstance(cls, type) and issubclass(cls, MessageFormatter)):
        raise NotifyError("formatter is not a MessageFormatter subclass")
    return cls


def build_notifier(specs: Iterable[Mapping], env: Mapping[str, str]) -> MultiNotifier:
    children = []
    for i, spec in enumerate(specs):
        options = dict(spec)
        if "type" not in options:
            raise NotifyError(f"notifier spec at index {i} has no type")
        kind = options.pop("type")
        if kind not in NOTIFIERS:
            raise NotifyError(f"unknown notifier type at index {i}; expected one of {sorted(NOTIFIERS)}")
        formatter_path = options.pop("formatter", None)
        formatter = load_formatter(formatter_path)() if formatter_path else None
        try:
            children.append(NOTIFIERS[kind].from_config(options, env, formatter))
        except TypeError:
            raise NotifyError(f"invalid options for notifier at index {i}") from None
    return MultiNotifier(children)
