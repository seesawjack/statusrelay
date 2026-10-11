"""Telegram Bot API notifier."""

import html
import re
import time
from collections.abc import Callable, Mapping

import requests

from statusrelay.message import Message, MessageFormatter, TelegramFormatter
from statusrelay.notifier.base import Notifier, NotifyError, register
from statusrelay.redact import add_secret, redact

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _plain_truncate(text: str) -> str:
    """Strip markup so a cut can never produce invalid HTML."""
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    return text[: MAX_MESSAGE_LENGTH - 1] + "…"


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
        text = f"{message.title}\n{message.body}"
        payload: dict = {"chat_id": self.chat_id, "disable_web_page_preview": False}
        if len(text) > MAX_MESSAGE_LENGTH:
            payload["text"] = _plain_truncate(text)
        else:
            payload["text"] = text
            payload["parse_mode"] = "HTML"
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
                data = resp.json()
                ok = resp.status_code == 200 and isinstance(data, dict) and data.get("ok") is True
            except ValueError:
                ok = False
            if ok:
                return
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break
        raise NotifyError(redact(f"telegram send failed: {last}"))
