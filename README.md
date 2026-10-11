# statusrelay

A small personal tool that polls a configured upstream status source on a schedule and relays meaningful changes to Telegram.

## Setup (GitHub Actions)

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather). Send it a message, then read your `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Generate a state key locally:
   ```bash
   python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
   .venv/bin/statusrelay keygen
   ```
3. Under **Settings → Secrets and variables → Actions**, add these secrets:
   - `STATUSRELAY_CONFIG`: the full YAML config (see `config.example.yaml`)
   - `STATUSRELAY_STATE_KEY`: the key from step 2
   - `TG_BOT_TOKEN` and `TG_CHAT_ID`
4. Run the `relay` workflow once from the Actions tab. State is stored encrypted in the `state` branch.

Scheduled runs are best effort, and GitHub may delay them. Scheduled workflows are disabled after 60 days without repository activity; re-enable them from the Actions tab.

## Local usage

```bash
cp config.example.yaml config.yaml   # git-ignored
.venv/bin/statusrelay resolve <activity page URL>
.venv/bin/statusrelay check --dry-run --no-start-delay
TG_BOT_TOKEN=… TG_CHAT_ID=… .venv/bin/statusrelay watch
```

Exit codes: `0` ok, `1` notification failed, `2` invalid config or missing secrets.

## Extending

- **New channel:** subclass `statusrelay.notifier.Notifier`, implement `send()`, and register it with `@register("name")`.
- **Custom message format:** subclass `PlainTextFormatter` or `TelegramFormatter`, override any hook, and set `formatter:` on the notifier.

## Development

```bash
.venv/bin/pytest
```
