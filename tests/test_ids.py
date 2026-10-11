import pytest

from statusrelay.ids import IdDecodeError, decode_long_id, event_short_id, session_short_id

EVENT_LONG = "8c47f9efe47df17d6381bcdb71309bfc"
SESSION_DAY1 = "26ab2893376c2821a1e1efbfdb28c4f4"
SESSION_DAY2 = "a0cd115cc2af7ef784fb6ee2414841c5"


@pytest.mark.parametrize(
    "long_id, short",
    [(EVENT_LONG, "e000000001"), (SESSION_DAY1, "s000000001"), (SESSION_DAY2, "s000000002")],
)
def test_decode_vectors(long_id, short):
    assert decode_long_id(long_id) == short


def test_typed_helpers():
    assert event_short_id(EVENT_LONG) == "e000000001"
    assert session_short_id(SESSION_DAY1) == "s000000001"


def test_event_helper_rejects_session_id():
    with pytest.raises(IdDecodeError):
        event_short_id(SESSION_DAY1)


def test_session_helper_rejects_event_id():
    with pytest.raises(IdDecodeError):
        session_short_id(EVENT_LONG)


@pytest.mark.parametrize("bad", ["", "xyz", EVENT_LONG.upper(), EVENT_LONG + "00"])
def test_rejects_malformed(bad):
    with pytest.raises(IdDecodeError):
        decode_long_id(bad)


def test_rejects_bad_padding():
    with pytest.raises(IdDecodeError):
        decode_long_id("0" * 32)


def test_rejects_trailing_newline():
    with pytest.raises(IdDecodeError):
        decode_long_id(EVENT_LONG + "\n")
