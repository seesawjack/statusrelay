"""Telegram Bot API notifier."""

import re
import time
from collections.abc import Callable, Mapping

import requests

from statusrelay.message import Message, MessageFormatter, TelegramFormatter
from statusrelay.notifier.base import Notifier, NotifyError, register
from statusrelay.redact import add_secret, redact

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _truncate(text: str) -> str:
    if len(text) <= MAX_MESSAGE_LENGTH:
        return text
    cut = text[: MAX_MESSAGE_LENGTH - 1]
    cut = re.sub(r"&[^;\s]*$", "", cut)  # never leave half an HTML entity
    return cut + "…"


@register("telegram")
class TelegramNotifier(Notifier):
    formatter_cls = TelegramFormatter

    def __init__(
        self,
        token: str,
        chat_id: str,
        formatter: MessageFormatter | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 2,
    ):
        super().__init__(formatter)
        add_secret(token)
        self.token = token
        self.chat_id = chat_id
        self.session = session or requests.Session()
        self.sleep = sleep
        self.retries = retries

    @classmethod
    def from_config(cls, options: dict, env: Mapping[str, str], formatter: MessageFormatter | None = None):
        token_env = options.get("token_env", "TG_BOT_TOKEN")
        chat_env = options.get("chat_id_env", "TG_CHAT_ID")
        token, chat_id = env.get(token_env), env.get(chat_env)
        if not token or not chat_id:
            raise NotifyError(f"telegram notifier needs env vars {token_env} and {chat_env}")
        return cls(token, chat_id, formatter=formatter)

    def send(self, message: Message) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": _truncate(f"{message.title}\n{message.body}"),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        last = "no attempt"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(2 ** attempt)
            try:
                resp = self.session.post(API_URL.format(token=self.token), json=payload, timeout=(5, 15))
            except requests.RequestException as exc:
                last = type(exc).__name__
                continue
            try:
                ok = resp.status_code == 200 and resp.json().get("ok") is True
            except ValueError:
                ok = False
            if ok:
                return
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        raise NotifyError(redact(f"telegram send failed: {last}"))
