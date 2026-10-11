import io
import json
import logging

import pytest
import requests
import responses

from statusrelay.message import Message, PlainTextFormatter, TelegramFormatter
from statusrelay.notifier import (
    NOTIFIERS,
    MultiNotifier,
    Notifier,
    NotifyError,
    StdoutNotifier,
    TelegramNotifier,
    build_notifier,
    load_formatter,
)
from statusrelay.redact import RedactingFilter, add_secret, redact
from tests.helpers import make_event

TOKEN = "123456:SECRET-token"
API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


class Recorder(Notifier):
    def __init__(self, fail=False, formatter=None):
        super().__init__(formatter)
        self.fail = fail
        self.messages = []

    def send(self, message):
        if self.fail:
            raise NotifyError("down")
        self.messages.append(message)


def test_builtins_are_registered():
    assert NOTIFIERS["telegram"] is TelegramNotifier
    assert NOTIFIERS["stdout"] is StdoutNotifier


def test_notify_formats_with_default_formatter():
    r = Recorder()
    r.notify(make_event())
    assert isinstance(r.formatter, PlainTextFormatter)
    assert r.messages[0].title == "[On sale] Show Day 1"


def test_multi_notifier_tolerates_partial_failure():
    ok, bad = Recorder(), Recorder(fail=True)
    MultiNotifier([bad, ok]).notify(make_event())
    assert len(ok.messages) == 1


def test_multi_notifier_raises_when_all_fail():
    with pytest.raises(NotifyError):
        MultiNotifier([Recorder(fail=True), Recorder(fail=True)]).notify(make_event())


def test_stdout_notifier_prints():
    out = io.StringIO()
    StdoutNotifier(stream=out).notify(make_event())
    assert out.getvalue().startswith("[On sale] Show Day 1\n")


def test_build_notifier_reads_env_and_formatter_override():
    multi = build_notifier(
        [
            {"type": "telegram", "token_env": "T", "chat_id_env": "C",
             "formatter": "statusrelay.message.PlainTextFormatter"},
            {"type": "stdout"},
        ],
        {"T": TOKEN, "C": "42"},
    )
    tg, out = multi.children
    assert isinstance(tg, TelegramNotifier) and tg.chat_id == "42"
    assert type(tg.formatter) is PlainTextFormatter
    assert isinstance(out, StdoutNotifier)


def test_build_notifier_missing_env():
    with pytest.raises(NotifyError, match="TG_BOT_TOKEN"):
        build_notifier([{"type": "telegram", "token_env": "TG_BOT_TOKEN", "chat_id_env": "TG_CHAT_ID"}], {})


def test_load_formatter_errors():
    with pytest.raises(NotifyError):
        load_formatter("NoDots")
    with pytest.raises(NotifyError):
        load_formatter("statusrelay.message.DoesNotExist")
    with pytest.raises(NotifyError):
        load_formatter("statusrelay.events.Event")


@responses.activate
def test_telegram_success_payload():
    responses.add(responses.POST, API, json={"ok": True})
    TelegramNotifier(TOKEN, "42", sleep=lambda s: None).notify(make_event(watch_name="A&B"))
    payload = json.loads(responses.calls[0].request.body)
    assert payload["chat_id"] == "42"
    assert payload["parse_mode"] == "HTML"
    assert payload["text"].startswith("<b>[On sale] A&amp;B</b>\n")


@responses.activate
def test_telegram_retries_then_succeeds():
    responses.add(responses.POST, API, status=500)
    responses.add(responses.POST, API, json={"ok": True})
    sleeps = []
    TelegramNotifier(TOKEN, "42", sleep=sleeps.append).send(Message("t", "b"))
    assert sleeps == [2]


@responses.activate
def test_telegram_final_failure_hides_token():
    for _ in range(3):
        responses.add(responses.POST, API, body=requests.ConnectionError(f"failed for {API}"))
    with pytest.raises(NotifyError) as info:
        TelegramNotifier(TOKEN, "42", sleep=lambda s: None).send(Message("t", "b"))
    assert TOKEN not in str(info.value)
    assert len(responses.calls) == 3


@responses.activate
def test_telegram_truncates_long_messages():
    responses.add(responses.POST, API, json={"ok": True})
    TelegramNotifier(TOKEN, "42", sleep=lambda s: None).send(Message("t", "&amp;" * 2000))
    text = json.loads(responses.calls[0].request.body)["text"]
    assert len(text) <= 4096
    assert text.endswith("…")
    assert not text[:-1].endswith("&") and not text[:-1].endswith("&am")


def test_redact_and_filter():
    add_secret("hunter2")
    assert redact("token=hunter2") == "token=***"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "value %s", ("hunter2",), None)
    RedactingFilter().filter(record)
    assert record.getMessage() == "value ***"


def test_telegram_formatter_is_default():
    assert isinstance(TelegramNotifier(TOKEN, "42").formatter, TelegramFormatter)
