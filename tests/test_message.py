import pytest

from statusrelay.message import Message, MessageFormatter, PlainTextFormatter, TelegramFormatter
from tests.helpers import ORDER_URL, make_area, make_event


def test_onsale_plain_text():
    msg = PlainTextFormatter().format(make_event("onsale"))
    assert msg.title == "[On sale] Show Day 1"
    assert msg.url == ORDER_URL
    lines = msg.body.splitlines()
    assert lines[0] == "Sample Live Tour（Day 1） | 2026-10-31 19:00 | Sample Arena"
    assert "搖滾A區  NT$5,880 (limit 2)" in lines
    assert ORDER_URL in lines
    assert lines[-1] == "Listed as on sale; actual seats per official page."


def test_new_areas_are_marked():
    areas = [make_area(), make_area("a000000102", "搖滾B區", is_new=True)]
    body = PlainTextFormatter().format(make_event("onsale", areas)).body
    assert "NEW 搖滾B區  NT$5,880 (limit 2)" in body
    assert "NEW 搖滾A區" not in body


def test_area_without_limit():
    body = PlainTextFormatter().format(make_event(areas=[make_area(purchase_limit=None)])).body
    assert "搖滾A區  NT$5,880" in body.splitlines()


def test_reminder_title():
    assert PlainTextFormatter().format(make_event("reminder")).title == "[Still on sale] Show Day 1"


def test_soldout_lists_names_without_prices():
    msg = PlainTextFormatter().format(make_event("soldout", [make_area(price=0, status="soldout")]))
    assert msg.title == "[Sold out again] Show Day 1"
    assert "- 搖滾A區" in msg.body.splitlines()
    assert "NT$" not in msg.body


def test_error_and_recovered_use_detail():
    fmt = PlainTextFormatter()
    err = fmt.format(make_event("error", [], detail="API error: HTTP 500 (3 consecutive failures)"))
    assert err.title == "[Monitor error] Show Day 1"
    assert err.body == "API error: HTTP 500 (3 consecutive failures)"
    assert err.url is None
    rec = fmt.format(make_event("recovered", [], detail="checks are succeeding again"))
    assert rec.title == "[Monitor recovered] Show Day 1"


def test_telegram_escapes_html_and_bolds_title():
    msg = TelegramFormatter().format(make_event(watch_name="A&B <x>", areas=[make_area(name="<VIP>")]))
    assert msg.title == "<b>[On sale] A&amp;B &lt;x&gt;</b>"
    assert "&lt;VIP&gt;  NT$5,880 (limit 2)" in msg.body


def test_subclass_can_override_one_hook():
    class CompactFormatter(TelegramFormatter):
        def format_area(self, a):
            return self.escape(f"{a.name}={a.price}")

    body = CompactFormatter().format(make_event()).body
    assert "搖滾A區=5880" in body
    assert body.splitlines()[-1] == "Listed as on sale; actual seats per official page."


def test_base_class_is_abstract():
    with pytest.raises(TypeError):
        MessageFormatter()


def test_message_extra_defaults_to_empty_dict():
    assert Message("t", "b").extra == {}
