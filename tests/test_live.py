"""Opt-in: STATUSRELAY_LIVE_URL=<activity url> .venv/bin/pytest -m live"""

import os

import pytest

from statusrelay.cli import describe_activity
from statusrelay.client import UpstreamClient

LIVE_URL = os.environ.get("STATUSRELAY_LIVE_URL", "")

pytestmark = [pytest.mark.live, pytest.mark.skipif(not LIVE_URL, reason="set STATUSRELAY_LIVE_URL")]


def test_describe_real_activity():
    text = describe_activity(LIVE_URL, UpstreamClient())
    assert "session_id:" in text
    assert "NT$" in text
