# Discord Hunter

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

- `CHECK_INTERVAL`
- `TWEET_FETCH_COUNT`
- `DELAY_BETWEEN_ACCOUNTS`
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

Reduce polling pressure by increasing:

- `CHECK_INTERVAL`
- `DELAY_BETWEEN_ACCOUNTS`

And configure multiple rotating X auth token pairs.

## License

Use privately and responsibly.