"""Print messages; used for --dry-run and debugging."""

import sys
from typing import TextIO

from statusrelay.message import Message, MessageFormatter
from statusrelay.notifier.base import Notifier, register


@register("stdout")
class StdoutNotifier(Notifier):
    def __init__(self, formatter: MessageFormatter | None = None, stream: TextIO | None = None):
        super().__init__(formatter)
        self.stream = stream or sys.stdout

    def send(self, message: Message) -> None:
        print(f"{message.title}\n{message.body}\n", file=self.stream, flush=True)
