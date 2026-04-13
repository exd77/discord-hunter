# Discord Hunter

![node >=18](https://img.shields.io/badge/node-%3E%3D18-3C873A?style=flat-square)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square)
![platform Linux](https://img.shields.io/badge/platform-Linux-8A2BE2?style=flat-square)
![source X/Twitter](https://img.shields.io/badge/source-X%2FTwitter-111111?style=flat-square)
![discord Discord](https://img.shields.io/badge/discord-Invite%20Hunter-5865F2?style=flat-square)
![mode alerting | auto-join](https://img.shields.io/badge/mode-alerting%20%7C%20auto--join-1F8B4C?style=flat-square)
![status production](https://img.shields.io/badge/status-production-FF8C42?style=flat-square)

Discord Hunter monitors selected X accounts, extracts Discord invite codes from tweets, validates them against the Discord API, and sends alerts to Telegram. It also supports an optional auto-join flow with captcha handling and persistent local state.

## Features

- Monitor selected X accounts using `twikit`
- Extract Discord invite links and bare invite codes from tweet text
- Validate invites against the Discord API
- Send Telegram alerts with invite status and join results
- Optional auto-join using a Discord user token
- Optional captcha solving flow for invite joins
- JSON-based persistence for monitored accounts, seen codes, and runtime state
- Playwright-powered browser flow for captcha handling

## Requirements

- Python 3.10+
- Linux environment recommended
- A Telegram bot token
- X/Twitter auth cookies for monitored account access
- A Discord user token if auto-join is enabled
- Playwright browser dependencies installed on the host

## Project Structure

- `bot.py` - main bot logic
- `openai_compat_provider.py` - model provider adapter used for multimodal captcha solving
- `generate_super_properties.py` - helper for Discord header generation
- `requirements.txt` - Python dependencies
- `start.sh` - simple startup helper
- `setup.sh` - basic setup helper
- `monitored_accounts.json` - monitored X accounts
- `state.json` - runtime state cache
- `join_debug.json` - join failure and captcha debug records

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/exd77/discord-hunter.git
cd discord-hunter
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Playwright browser

```bash
python -m playwright install chromium
```

### 5. Install required system libraries on Ubuntu

If Playwright Chromium fails to launch, install the required host packages:

```bash
sudo apt-get update
sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libxcomposite1 libxdamage1
```

### 6. Configure environment variables

Copy the example file:

```bash
cp .env.example .env
```

Then edit `.env` with your values.

## Environment Variables

### Telegram

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`

### Discord

- `DISCORD_USER_TOKEN`
- `DISCORD_SUPER_PROPERTIES`
- `DISCORD_BUILD_NUMBER`
- `DISCORD_LOCALE`
- `DISCORD_TIMEZONE`
- `DISCORD_USER_AGENT`
- `DISCORD_SEC_CH_UA`
- `DISCORD_SEC_CH_UA_MOBILE`
- `DISCORD_SEC_CH_UA_PLATFORM`
- `DISCORD_API_BASE`
- `DISCORD_JOIN_ENABLED`

### X / Twitter

You can provide either a single token pair or multiple rotating token pairs:

- `TWITTER_AUTH_TOKEN`
- `TWITTER_CT0`

Or:

- `AUTH_TOKEN1`, `CT0_1`
- `AUTH_TOKEN2`, `CT0_2`
- etc.

### Captcha / Vision Model

- `GEMINI_API_KEY`
- `GEMINI_BASE_URL`
- `GEMINI_MODEL`

Despite the legacy variable names, these values are used by the current multimodal provider layer and may point to a compatible third-party API.

### Runtime Tuning

- `POLL_MODE` (`fast` or `safe`)
- `CHECK_INTERVAL`
- `TWEET_FETCH_COUNT`
- `DELAY_BETWEEN_ACCOUNTS`
- `MAX_CONCURRENT_CHECKS`
- `RATE_LIMIT_BACKOFF_SECONDS`
- `USER_CACHE_TTL`
- `CAPTCHA_MAX_RETRIES`
- `ACCOUNT_ERROR_THRESHOLD`
- `MONITORED_FILE`
- `STATE_FILE`
- `JOIN_DEBUG_FILE`

## Running the Bot

```bash
source venv/bin/activate
python bot.py
```

Or:

```bash
./start.sh
```

## Production Workflow

A typical production workflow for this bot looks like this:

1. Configure `.env` with Telegram, X/Twitter, Discord, and model provider credentials.
2. Add the X accounts you want to monitor from Telegram using `/add <username>`.
3. Keep the bot running continuously in a stable Linux environment.
4. The monitoring loop checks configured accounts on a recurring schedule.
5. When a new tweet contains a Discord invite, the bot:
   - extracts the invite code
   - validates the invite against the Discord API
   - sends a Telegram alert with status details
6. If auto-join is enabled, the bot attempts to join using the configured Discord token.
7. If Discord returns a captcha challenge, the bot tries to solve it through the configured multimodal model workflow.
8. Join failures, captcha attempts, and artifacts are written to local debug/state files for troubleshooting.
9. Operators can review alerts in Telegram and inspect `bot.log`, `join_debug.json`, and `tmp/.challenge/` when needed.

### Recommended Production Setup

- Use a dedicated Linux VPS or always-on server
- Keep the repository private
- Use multiple X auth token pairs for safer rotation
- Start with `POLL_MODE=safe`, then move to `fast` only if your tokens are stable
- Rotate credentials immediately if you suspect rate limiting or account restrictions
- Periodically review logs and debug artifacts to catch changes in upstream platform behavior

## Telegram Commands

- `/start`
- `/help`
- `/add <username>`
- `/remove <username>`
- `/list`
- `/status`
- `/join_on`
- `/join_off`

## Notes

- Keep `.env`, token files, and debug state out of public repositories.
- Auto-join can fail due to captcha, server screening, account trust limits, or Discord-side anti-abuse checks.
- Captcha solving quality depends heavily on the configured multimodal model.
- `join_debug.json` and `tmp/.challenge/` are useful for troubleshooting failed captcha or join attempts.
- This project is best treated as a private operational tool.

## Troubleshooting

### Playwright browser launch error

Install the browser:

```bash
python -m playwright install chromium
```

Install missing host libraries if needed:

```bash
sudo apt-get update
sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libxcomposite1 libxdamage1
```

### Captcha solve succeeds but join still fails

Check:

- `join_debug.json`
- `bot.log`
- `tmp/.challenge/`

This usually indicates Discord rejected the captcha response or required a fresh challenge context.

### Twitter rate limit issues

If you want safer behavior, switch to:

```bash
POLL_MODE=safe
```

You can also reduce pressure by tuning:

- `CHECK_INTERVAL`
- `DELAY_BETWEEN_ACCOUNTS`
- `MAX_CONCURRENT_CHECKS`
- `RATE_LIMIT_BACKOFF_SECONDS`

And configure multiple rotating X auth token pairs.

## License

Use privately and responsibly.