"""Command-line entry points: check, watch, resolve, keygen."""

import argparse
import logging
import os
import random
import signal
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TextIO

from statusrelay.app import LOCAL_TZ, run_round
from statusrelay.checker import fetch_status
from statusrelay.client import ApiError, UpstreamClient
from statusrelay.config import MIN_INTERVAL_SECONDS, ConfigError, load_config_from
from statusrelay.ids import IdDecodeError, session_short_id
from statusrelay.notifier import MultiNotifier, NotifyError, StdoutNotifier, build_notifier
from statusrelay.redact import RedactingFilter, add_secret, redact
from statusrelay.resolver import ResolveError, parse_activity_url, records, session_time
from statusrelay.state import STATE_KEY_ENV, EncryptedFileStore, JsonFileStore, StateStore, generate_key

log = logging.getLogger(__name__)

START_DELAY_MAX = 20
MAX_INTERVAL = 900
MAX_BACKOFF = 64


def next_delay(interval: int, rng: random.Random, backoff: int = 1) -> float:
    base = max(interval, min(interval * backoff, MAX_INTERVAL))
    offset = rng.uniform(1, 2) * rng.choice((-1, 1))
    return max(float(MIN_INTERVAL_SECONDS), base + offset)


def describe_activity(url: str, client) -> str:
    event_long = parse_activity_url(url)
    sessions = [s for s in records(client.get_static(event_long, "sessions"), "sessions") if not s.get("hidden")]
    areas = records(client.get_static(event_long, "ticketAreas"), "ticketAreas")
    shorts = {s["sessionId"]: session_short_id(s["sessionId"]) for s in sessions}
    status = fetch_status(client, list(shorts.values()), [a["ticketAreaId"] for a in areas], [])
    lines: list[str] = []
    for s in sessions:
        sid, short = s["sessionId"], shorts[s["sessionId"]]
        lines.append(f"{session_time(s)}  [{status['session'].get(short, {}).get('status', '?')}]  {s.get('name', '')}")
        lines.append(f"  session_id: {sid}  ({short})")
        own = sorted((a for a in areas if a.get("sessionId") == sid), key=lambda a: a.get("sortedIndex", 0))
        for a in own:
            area_status = status["ticketArea"].get(a["ticketAreaId"], {}).get("status", "?")
            lines.append(f"  - {a.get('name', '')}  NT${a.get('price', 0):,}  [{area_status}]")
    return "\n".join(lines)


def _setup_logging(stream: TextIO) -> None:
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="statusrelay", description="Personal status relay")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "run one round (for cron)"), ("watch", "poll until interrupted")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", default="config.yaml", help="used when STATUSRELAY_CONFIG is unset")
        p.add_argument("--state", default="state.json")
        p.add_argument("--dry-run", action="store_true", help="print notifications instead of sending (state is still updated)")
        p.add_argument("--require-encryption", action="store_true",
                       help=f"fail unless {STATE_KEY_ENV} is set")
        if name == "check":
            p.add_argument("--no-start-delay", action="store_true", help="skip the random 0-20 s start delay")
    r = sub.add_parser("resolve", help="list sessions and areas for an activity URL")
    r.add_argument("activity_url")
    sub.add_parser("keygen", help=f"print a new key for {STATE_KEY_ENV}")
    return parser


def _store(args, env: Mapping[str, str]) -> StateStore:
    key = env.get(STATE_KEY_ENV, "").strip()
    if key:
        add_secret(key)
        return EncryptedFileStore(args.state, key)
    if args.require_encryption:
        raise ConfigError(f"{STATE_KEY_ENV} is required (--require-encryption)")
    return JsonFileStore(args.state)


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[], object] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    env = os.environ if env is None else env
    rng = rng or random.Random()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    now_fn = now_fn or (lambda: datetime.now(LOCAL_TZ))
    args = _parser().parse_args(argv)
    _setup_logging(stderr)

    if args.command == "keygen":
        print(generate_key(), file=stdout)
        return 0

    client = (client_factory or UpstreamClient)()

    if args.command == "resolve":
        try:
            print(describe_activity(args.activity_url, client), file=stdout)
        except (ResolveError, ApiError, IdDecodeError) as exc:
            print(f"error: {exc}", file=stderr)
            return 1
        except (KeyError, TypeError, AttributeError):
            print("error: unexpected upstream payload shape", file=stderr)
            return 1
        return 0

    try:
        config = load_config_from(args.config, env)
        notifier = (MultiNotifier([StdoutNotifier(stream=stdout)]) if args.dry_run
                    else build_notifier(config.notifiers, env))
    except (ConfigError, NotifyError) as exc:
        print(redact(f"config error: {exc}"), file=stderr)
        return 2
    try:
        store = _store(args, env)
    except (ConfigError, ValueError) as exc:  # ValueError: malformed state key
        print(redact(f"config error: {exc}"), file=stderr)
        return 2

    if args.command == "check":
        if not args.no_start_delay:
            sleep(rng.uniform(0, START_DELAY_MAX))
        return run_round(config, store, client, notifier, now_fn).exit_code

    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    backoff = 1
    try:
        while True:
            result = run_round(config, store, client, notifier, now_fn)
            backoff = min(backoff * 2, MAX_BACKOFF) if result.rate_limited else 1
            sleep(next_delay(config.interval_seconds, rng, backoff))
    except KeyboardInterrupt:
        log.info("stopped")
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)
