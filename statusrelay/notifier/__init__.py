"""Notifier package. Importing it registers the built-in notifiers."""

from statusrelay.notifier.base import (
    NOTIFIERS,
    MultiNotifier,
    Notifier,
    NotifyError,
    build_notifier,
    load_formatter,
    register,
)
from statusrelay.notifier.stdout import StdoutNotifier
from statusrelay.notifier.telegram import TelegramNotifier

__all__ = [
    "NOTIFIERS", "MultiNotifier", "Notifier", "NotifyError", "StdoutNotifier",
    "TelegramNotifier", "build_notifier", "load_formatter", "register",
]
