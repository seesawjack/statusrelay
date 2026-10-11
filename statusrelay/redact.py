"""Keep secrets out of logs and error messages."""

import logging

_SECRETS: set[str] = set()


def add_secret(value: str | None) -> None:
    if value:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True
