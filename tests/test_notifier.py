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
import statusrelay.redact
from statusrelay.notifier import register
from statusrelay.redact import RedactingFilter, add_secret, redact
from tests.helpers import make_event

TOKEN = "123456:SECRET-token"
API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


@pytest.fixture(autouse=True)
def _clear_secrets():
    yield
    statusrelay.redact._SECRETS.clear()


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
    with pytest.raises(NotifyError, match="^formatter must be a dotted path$"):
        load_formatter("NoDots")
    with pytest.raises(NotifyError, match="^cannot load formatter$") as info:
        load_formatter("pkg.SENTINEL_FMT.Missing")
    assert "SENTINEL_FMT" not in str(info.value) and info.value.__cause__ is None
    with pytest.raises(NotifyError, match="^formatter is not a MessageFormatter subclass$"):
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
def test_telegram_long_message_falls_back_to_plain_text():
    responses.add(responses.POST, API, json={"ok": True})
    body = "<b>x</b> &amp; <a href=\"http://e.com\">link</a> " * 400
    TelegramNotifier(TOKEN, "42", sleep=lambda s: None).send(Message("t", body))
    payload = json.loads(responses.calls[0].request.body)
    text = payload["text"]
    assert "parse_mode" not in payload
    assert len(text) <= 4096
    assert text.endswith("…")
    assert "<" not in text and "&amp;" not in text


@responses.activate
def test_telegram_4xx_is_not_retried():
    responses.add(responses.POST, API, status=400, body=f"bad {TOKEN}")
    with pytest.raises(NotifyError) as info:
        TelegramNotifier(TOKEN, "42", sleep=lambda s: None).send(Message("t", "b"))
    assert len(responses.calls) == 1
    assert TOKEN not in str(info.value)


@responses.activate
def test_telegram_non_dict_json_is_failure():
    responses.add(responses.POST, API, json=[1, 2])
    with pytest.raises(NotifyError):
        TelegramNotifier(TOKEN, "42", sleep=lambda s: None, retries=0).send(Message("t", "b"))


def test_redact_replaces_longest_secret_first():
    add_secret("abc")
    add_secret("abcdef")
    assert redact("x abcdef y") == "x *** y"


def test_filter_redacts_tracebacks():
    add_secret("s3cret-token")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("test_tb_redact")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise RuntimeError("boom s3cret-token")
        except RuntimeError:
            logger.exception("failed")
        logger.info("stack", stack_info=True, extra={})
    finally:
        logger.removeHandler(handler)
    assert "s3cret-token" not in stream.getvalue()
    assert "boom ***" in stream.getvalue()


def test_filter_redacts_existing_exc_text_and_stack_info():
    add_secret("s3cret-token")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", (), None)
    record.exc_text = "tb s3cret-token"
    record.stack_info = "stack s3cret-token"
    RedactingFilter().filter(record)
    assert "s3cret-token" not in record.exc_text
    assert "s3cret-token" not in record.stack_info


def test_build_notifier_missing_type():
    with pytest.raises(NotifyError, match="index 1 has no type"):
        build_notifier([{"type": "stdout"}, {}], {})


def test_build_notifier_unknown_type_does_not_echo_value():
    with pytest.raises(NotifyError, match="unknown notifier type at index 0") as info:
        build_notifier([{"type": "hunter2-chan"}], {})
    assert "hunter2-chan" not in str(info.value)
    assert "stdout" in str(info.value)


def test_build_notifier_invalid_options():
    with pytest.raises(NotifyError, match="invalid options for notifier at index 0") as info:
        build_notifier([{"type": "stdout", "bogus": "hunter2"}], {})
    assert "hunter2" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_subclass_reuses_inherited_notify():
    class Shout(PlainTextFormatter):
        def format(self, event):
            m = super().format(event)
            return Message(m.title.upper(), m.body)

    @register("test-sub")
    class SubNotifier(StdoutNotifier):
        formatter_cls = Shout

    try:
        out = io.StringIO()
        multi = build_notifier([{"type": "test-sub", "stream": out}], {})
        multi.notify(make_event())
    finally:
        NOTIFIERS.pop("test-sub", None)
    assert isinstance(multi.children[0], SubNotifier)
    assert out.getvalue().startswith("[ON SALE] SHOW DAY 1\n")


def test_redact_and_filter():
    add_secret("hunter2")
    assert redact("token=hunter2") == "token=***"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "value %s", ("hunter2",), None)
    RedactingFilter().filter(record)
    assert record.getMessage() == "value ***"


def test_telegram_formatter_is_default():
    assert isinstance(TelegramNotifier(TOKEN, "42").formatter, TelegramFormatter)
