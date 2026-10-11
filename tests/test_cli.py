import io
import random

import pytest

from statusrelay.cli import describe_activity, main, next_delay
from statusrelay.client import RateLimited
from statusrelay.state import EncryptedFileStore, generate_key
from tests.helpers import ACTIVITY_URL, NOW, SESSION_DAY1, FakeClient, make_status

CONFIG = f"""
notifiers:
  - type: stdout
watches:
  - name: Show Day 1
    activity_url: {ACTIVITY_URL}
    session:
      date: 2026-10-31
"""


def run(tmp_path, args, config=CONFIG, client=None, sleep=None, env=None, state="state.json"):
    cfg = tmp_path / "config.yaml"
    if config is not None:
        cfg.write_text(config, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    client = client or FakeClient()
    sleeps = []
    code = main(
        [args[0], "--config", str(cfg), "--state", str(tmp_path / state), *args[1:]],
        env=env or {},
        client_factory=lambda: client,
        now_fn=lambda: NOW,
        sleep=sleep or sleeps.append,
        rng=random.Random(5),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), sleeps, client


@pytest.mark.parametrize("seed", range(200))
def test_next_delay_jitter(seed):
    d = next_delay(60, random.Random(seed))
    assert 58 <= d <= 59 or 61 <= d <= 62


def test_next_delay_floor_and_backoff():
    rng = random.Random(0)
    assert all(next_delay(30, rng) >= 30 for _ in range(100))
    assert all(238 <= next_delay(60, rng, backoff=4) <= 242 for _ in range(100))
    assert all(898 <= next_delay(60, rng, backoff=64) <= 902 for _ in range(100))


def test_check_dry_run(tmp_path):
    code, out, _, sleeps, _ = run(tmp_path, ["check", "--dry-run"])
    assert code == 0
    assert "[On sale] Show Day 1" in out
    assert len(sleeps) == 1 and 0 <= sleeps[0] <= 20
    assert (tmp_path / "state.json").exists()


def test_check_no_start_delay(tmp_path):
    _, _, _, sleeps, _ = run(tmp_path, ["check", "--dry-run", "--no-start-delay"])
    assert sleeps == []


def test_config_from_env_without_file(tmp_path):
    code, out, _, _, _ = run(tmp_path, ["check", "--dry-run", "--no-start-delay"], config=None,
                             env={"STATUSRELAY_CONFIG": CONFIG})
    assert code == 0 and "[On sale] Show Day 1" in out


def test_encrypted_state_when_key_set(tmp_path):
    key = generate_key()
    code, _, _, _, _ = run(tmp_path, ["check", "--dry-run", "--no-start-delay", "--require-encryption"],
                           env={"STATUSRELAY_STATE_KEY": key}, state="state.enc")
    assert code == 0
    raw = (tmp_path / "state.enc").read_bytes()
    assert b"s000000001" not in raw and b"Show Day 1" not in raw
    assert EncryptedFileStore(tmp_path / "state.enc", key).load().data["watches"]["Show Day 1"]["available"]


@pytest.mark.parametrize("key", [None, "", "  "])
def test_require_encryption_without_key(tmp_path, key):
    env = {} if key is None else {"STATUSRELAY_STATE_KEY": key}
    code, _, err, _, _ = run(tmp_path, ["check", "--require-encryption"], env=env)
    assert code == 2 and "STATUSRELAY_STATE_KEY" in err
    assert not (tmp_path / "state.json").exists()


def test_bad_state_key(tmp_path):
    code, _, err, _, _ = run(tmp_path, ["check"], env={"STATUSRELAY_STATE_KEY": "nope"})
    assert code == 2 and "Fernet" in err


def test_check_missing_telegram_env_is_config_error(tmp_path):
    config = CONFIG.replace("type: stdout", "type: telegram\n    token_env: TG_BOT_TOKEN\n    chat_id_env: TG_CHAT_ID")
    code, _, err, _, _ = run(tmp_path, ["check"], config=config)
    assert code == 2 and "TG_BOT_TOKEN" in err


def test_invalid_config(tmp_path):
    code, _, err, _, _ = run(tmp_path, ["check"], config="watches: []\nnotifiers: []\n")
    assert code == 2 and "config error" in err


def test_watch_loops_until_interrupted(tmp_path):
    delays = []

    def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) == 2:
            raise KeyboardInterrupt

    code, out, _, _, client = run(tmp_path, ["watch", "--dry-run"], sleep=fake_sleep)
    assert code == 0
    assert len(client.status_calls) == 2
    assert all(58 <= d <= 62 for d in delays)
    assert out.count("[On sale]") == 1


def test_watch_backs_off_when_rate_limited(tmp_path):
    delays = []

    def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) == 3:
            raise KeyboardInterrupt

    run(tmp_path, ["watch", "--dry-run"], client=FakeClient(status=RateLimited("HTTP 429")), sleep=fake_sleep)
    assert 118 <= delays[0] <= 122 and 238 <= delays[1] <= 242 and 478 <= delays[2] <= 482


def test_describe_activity():
    text = describe_activity(ACTIVITY_URL, FakeClient(status=make_status(area_status={"a000000102": "soldout"})))
    assert "2026-10-31 19:00  [onsale]" in text
    assert f"session_id: {SESSION_DAY1}  (s000000001)" in text
    assert "  - 搖滾A區  NT$5,880  [onsale]" in text
    assert "  - 搖滾B區  NT$5,880  [soldout]" in text
    assert "Hidden rehearsal" not in text


def test_resolve_command_prints():
    out = io.StringIO()
    code = main(["resolve", ACTIVITY_URL], client_factory=FakeClient, stdout=out, stderr=io.StringIO())
    assert code == 0 and "搖滾A區" in out.getvalue()


def test_resolve_command_bad_url():
    err = io.StringIO()
    code = main(["resolve", "https://example.com"], client_factory=FakeClient, stdout=io.StringIO(), stderr=err)
    assert code == 1 and "not a supported activity URL" in err.getvalue()


def test_keygen_prints_key():
    out = io.StringIO()
    assert main(["keygen"], stdout=out, stderr=io.StringIO()) == 0
    key = out.getvalue().strip()
    assert len(key) == 44
    EncryptedFileStore("unused", key)  # valid key: does not raise


def test_bad_notifier_spec_is_config_error_without_echo(tmp_path):
    config = CONFIG.replace("type: stdout", "type: stdout\n    bogus_option: SECRET-VALUE-123")
    code, out, err, _, _ = run(tmp_path, ["check"], config=config)
    assert code == 2 and "config error" in err
    assert "SECRET-VALUE-123" not in err + out


def test_malformed_state_key_not_echoed(tmp_path):
    code, _, err, _, _ = run(tmp_path, ["check", "--require-encryption"],
                             env={"STATUSRELAY_STATE_KEY": "not-a-key-SENTINEL123"})
    assert code == 2 and "config error" in err
    assert "SENTINEL123" not in err


def test_missing_formatter_path_not_echoed(tmp_path):
    config = CONFIG.replace("type: stdout", 'type: stdout\n    formatter: "pkg.SENTINEL_FMT.Missing"')
    code, _, err, _, _ = run(tmp_path, ["check"], config=config)
    assert code == 2 and "config error" in err
    assert "SENTINEL_FMT" not in err


def test_config_error_output_is_redacted(tmp_path):
    from statusrelay.redact import add_secret
    add_secret("LEAKY-TOKEN-777")
    config = CONFIG.replace("type: stdout", "type: LEAKY-TOKEN-777")
    code, _, err, _, _ = run(tmp_path, ["check"], config=config)
    assert code == 2 and "LEAKY-TOKEN-777" not in err


def test_next_delay_above_max_interval_never_shortens():
    rng = random.Random(0)
    assert all(next_delay(1200, rng, backoff=2) >= 1198 for _ in range(100))


def test_dry_run_help_mentions_state(capsys):
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    assert "state is still updated" in " ".join(capsys.readouterr().out.split())
