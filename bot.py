import asyncio
import json
import logging
import os
import random
import re
import signal
import tempfile
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from curl_cffi import requests as curl_requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
from hcaptcha_challenger import AgentV, AgentConfig
from twikit import Client

load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN", "").strip()
DISCORD_SUPER_PROPERTIES = os.getenv("DISCORD_SUPER_PROPERTIES", "").strip()
DISCORD_BUILD_NUMBER = os.getenv("DISCORD_BUILD_NUMBER", "9999").strip()
DISCORD_LOCALE = os.getenv("DISCORD_LOCALE", "en-US").strip()
DISCORD_TIMEZONE = os.getenv("DISCORD_TIMEZONE", "Asia/Jakarta").strip()
DISCORD_USER_AGENT = os.getenv("DISCORD_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36").strip()
DISCORD_SEC_CH_UA = os.getenv("DISCORD_SEC_CH_UA", '"Chromium";v="134", "Google Chrome";v="134", "Not:A-Brand";v="24"').strip()
DISCORD_SEC_CH_UA_MOBILE = os.getenv("DISCORD_SEC_CH_UA_MOBILE", "?0").strip()
DISCORD_SEC_CH_UA_PLATFORM = os.getenv("DISCORD_SEC_CH_UA_PLATFORM", '"Linux"').strip()
DISCORD_API_BASE = os.getenv("DISCORD_API_BASE", "https://discord.com/api/v9").strip()
DISCORD_JOIN_ENABLED = os.getenv("DISCORD_JOIN_ENABLED", "false").lower() == "true"

MONITORED_FILE = os.getenv("MONITORED_FILE", "monitored_accounts.json")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
JOIN_DEBUG_FILE = os.getenv("JOIN_DEBUG_FILE", "join_debug.json")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "").strip()  # deprecated, kept for compat
POLL_MODE = os.getenv("POLL_MODE", "fast").strip().lower() or "fast"
if POLL_MODE == "safe":
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))
    TWEET_FETCH_COUNT = int(os.getenv("TWEET_FETCH_COUNT", "5"))
    DELAY_BETWEEN_ACCOUNTS = float(os.getenv("DELAY_BETWEEN_ACCOUNTS", "1.5"))
    MAX_CONCURRENT_CHECKS = int(os.getenv("MAX_CONCURRENT_CHECKS", "2"))
else:
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "6"))
    TWEET_FETCH_COUNT = int(os.getenv("TWEET_FETCH_COUNT", "10"))
    DELAY_BETWEEN_ACCOUNTS = float(os.getenv("DELAY_BETWEEN_ACCOUNTS", "0.35"))
    MAX_CONCURRENT_CHECKS = int(os.getenv("MAX_CONCURRENT_CHECKS", "4"))
USER_CACHE_TTL = int(os.getenv("USER_CACHE_TTL", "900"))
LOG_TWEETS_AND_REPLIES_FALLBACK = os.getenv("LOG_TWEETS_AND_REPLIES_FALLBACK", "false").lower() == "true"
CAPTCHA_MAX_RETRIES = int(os.getenv("CAPTCHA_MAX_RETRIES", "3"))
ACCOUNT_ERROR_THRESHOLD = int(os.getenv("ACCOUNT_ERROR_THRESHOLD", "10"))
RATE_LIMIT_BACKOFF_SECONDS = int(os.getenv("RATE_LIMIT_BACKOFF_SECONDS", "45"))

logger = logging.getLogger("discord_invite_hunter")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
logging.getLogger("httpcore.connection").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.INFO)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
twitter_client = Client()


class StaticClientTransaction:
    def __init__(self):
        self.home_page_response = True

    async def init(self, session, headers):
        self.home_page_response = True

    def generate_transaction_id(self, method: str, path: str, response=None, key=None, animation_key=None, time_now=None):
        return ""

MONITORED: Dict[str, Dict[str, Any]] = {}
STATE: Dict[str, Any] = {
    "last_tweets": {},
    "seen_codes": {},
    "join_enabled_per_chat": {}
}
USER_CACHE: Dict[str, Dict[str, Any]] = {}
CURRENT_TOKEN_INDEX = 0
TOKEN_USAGE: Dict[int, Dict[str, Any]] = {}
TWITTER_TOKENS: List[Dict[str, str]] = []
ACCOUNT_ERRORS: Dict[str, int] = {}  # track consecutive errors per account
RATE_LIMIT_UNTIL = 0.0

# Captcha solver stats (in-memory)
CAPTCHA_STATS: Dict[str, Any] = {
    "total_attempts": 0,
    "total_success": 0,
    "total_fail": 0,
    "avg_solve_time": 0.0,
    "last_solve_at": None,
}

DIRECT_INVITE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9-]{2,32})", re.IGNORECASE)
BARE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{6,12})(?![A-Za-z0-9])")
COMMON_FALSE_POSITIVES = {
    "dropped", "discord", "server", "invite", "today", "alpha", "claim", "mint", "watch", "hello"
}


def env_list_int(value: str) -> List[int]:
    if not value.strip():
        return []
    out = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ALLOWED_USER_IDS entry: %s", part)
    return out


ALLOWED_USER_IDS = env_list_int(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))


def is_authorized(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def clean_env_value(value: str) -> str:
    return value.strip().strip('"\'') if value else ""


def load_twitter_tokens() -> List[Dict[str, str]]:
    tokens = []
    idx = 1
    while True:
        auth = clean_env_value(os.getenv(f"AUTH_TOKEN{idx}", ""))
        ct0 = clean_env_value(os.getenv(f"CT0_{idx}", ""))
        if not auth and not ct0:
            break
        if auth and ct0:
            tokens.append({"auth_token": auth, "ct0": ct0})
        idx += 1

    if not tokens:
        auth = clean_env_value(os.getenv("TWITTER_AUTH_TOKEN", ""))
        ct0 = clean_env_value(os.getenv("TWITTER_CT0", ""))
        if auth and ct0:
            tokens.append({"auth_token": auth, "ct0": ct0})
    return tokens


# --- Atomic file I/O (prevents corruption on crash) ---

def load_json_file(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[IO] Failed to load %s: %s", path, e)
        # Try backup
        bak = path + ".bak"
        if os.path.exists(bak):
            logger.info("[IO] Loading backup %s", bak)
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default


def save_json_file(path: str, data: Any) -> None:
    """Atomic write: write to temp file, then rename (prevents partial writes on crash)."""
    dir_name = os.path.dirname(path) or "."
    try:
        # Keep a backup of current file
        if os.path.exists(path):
            bak = path + ".bak"
            try:
                os.replace(path, bak)
            except OSError:
                pass
        with tempfile.NamedTemporaryFile("w", dir=dir_name, suffix=".tmp", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
    except OSError as e:
        logger.error("[IO] Failed to save %s: %s", path, e)
        # Fallback: direct write
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


def load_data() -> None:
    global MONITORED, STATE
    MONITORED = load_json_file(MONITORED_FILE, {})
    STATE = load_json_file(STATE_FILE, STATE)
    STATE.setdefault("last_tweets", {})
    STATE.setdefault("seen_codes", {})
    STATE.setdefault("join_enabled_per_chat", {})


async def save_data() -> None:
    save_json_file(MONITORED_FILE, MONITORED)
    save_json_file(STATE_FILE, STATE)


def get_account_key(chat_id: str, username: str) -> str:
    return f"{chat_id}_{username.lower()}"


def get_human_delay() -> float:
    return random.uniform(1.0, 2.5)


def get_best_token() -> Tuple[Optional[Dict[str, str]], Optional[int]]:
    if not TWITTER_TOKENS:
        return None, None
    now = datetime.now().timestamp()
    candidates = []
    for idx, token in enumerate(TWITTER_TOKENS):
        tracker = TOKEN_USAGE.get(idx, {})
        if tracker.get("invalid"):
            continue
        last_used = tracker.get("last_used", 0)
        candidates.append((last_used, idx, token))
    if not candidates:
        return None, None
    _, idx, token = min(candidates, key=lambda x: x[0])
    if idx not in TOKEN_USAGE:
        TOKEN_USAGE[idx] = {}
    TOKEN_USAGE[idx]["last_used"] = now
    return token, idx


async def switch_twitter_token() -> bool:
    global CURRENT_TOKEN_INDEX
    token, idx = get_best_token()
    if token is None:
        return False
    twitter_client.set_cookies({"auth_token": token["auth_token"], "ct0": token["ct0"]})
    CURRENT_TOKEN_INDEX = idx
    logger.info("Twitter token switched to #%s", idx + 1)
    return True


async def authenticate_twitter() -> bool:
    if not TWITTER_TOKENS:
        logger.error("No Twitter tokens loaded")
        return False
    twitter_client.client_transaction = StaticClientTransaction()
    return await switch_twitter_token()


async def get_user_with_cache(username: str):
    now = datetime.now().timestamp()
    cached = USER_CACHE.get(username.lower())
    if cached and now - cached["at"] < USER_CACHE_TTL:
        return cached["user"]
    await asyncio.sleep(get_human_delay())
    try:
        user = await twitter_client.get_user_by_screen_name(username)
    except Exception as e:
        if "KEY_BYTE indices" in str(e):
            logger.warning("Twikit client transaction bootstrap failed, retrying with static transaction id bypass")
            twitter_client.client_transaction = StaticClientTransaction()
            user = await twitter_client.get_user_by_screen_name(username)
        else:
            raise
    USER_CACHE[username.lower()] = {"user": user, "at": now}
    return user


def get_tweet_text_with_expanded_urls(tweet) -> str:
    """Replace t.co shortened URLs with their expanded versions from tweet entities."""
    text = getattr(tweet, "text", "") or ""
    urls = getattr(tweet, "urls", None)
    if urls:
        for url_entity in urls:
            short_url = getattr(url_entity, "url", "") or ""
            expanded = getattr(url_entity, "expanded_url", "")
            if not short_url and isinstance(url_entity, dict):
                short_url = url_entity.get("url", "")
                expanded = url_entity.get("expanded_url", "")
            if short_url and expanded:
                text = text.replace(short_url, expanded)
    return text


def extract_discord_codes(text: str) -> List[str]:
    if not text:
        return []

    found = []
    seen = set()

    for match in DIRECT_INVITE_RE.findall(text):
        code = match.strip().strip("/.,!?)]}\"\u200b")
        if 2 <= len(code) <= 32 and code.lower() not in seen:
            found.append(code)
            seen.add(code.lower())

    for line in text.splitlines():
        stripped = line.strip().strip("`*_- ")
        if not stripped or " " in stripped:
            continue
        if stripped.lower() in COMMON_FALSE_POSITIVES:
            continue
        if re.fullmatch(r"[A-Za-z0-9]{6,12}", stripped):
            if stripped.lower() not in seen:
                found.append(stripped)
                seen.add(stripped.lower())

    for match in BARE_CODE_RE.findall(text):
        code = match.strip()
        if code.lower() in COMMON_FALSE_POSITIVES:
            continue
        if any(ch.isdigit() for ch in code) and code.lower() not in seen:
            found.append(code)
            seen.add(code.lower())

    return found


def build_discord_headers() -> Dict[str, str]:
    headers = {
        "Authorization": DISCORD_USER_TOKEN,
        "Accept": "*/*",
        "Accept-Language": DISCORD_LOCALE,
        "Content-Type": "application/json",
        "Origin": "https://discord.com",
        "Priority": "u=1, i",
        "Referer": "https://discord.com/channels/@me",
        "Sec-CH-UA": DISCORD_SEC_CH_UA,
        "Sec-CH-UA-Mobile": DISCORD_SEC_CH_UA_MOBILE,
        "Sec-CH-UA-Platform": DISCORD_SEC_CH_UA_PLATFORM,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": DISCORD_USER_AGENT,
        "X-Debug-Options": "bugReporterEnabled",
        "X-Discord-Locale": DISCORD_LOCALE,
        "X-Discord-Timezone": DISCORD_TIMEZONE,
    }
    if DISCORD_SUPER_PROPERTIES:
        headers["X-Super-Properties"] = DISCORD_SUPER_PROPERTIES
    if DISCORD_BUILD_NUMBER:
        headers["X-Client-Build-Number"] = DISCORD_BUILD_NUMBER
    return headers


def validate_invite(code: str) -> Dict[str, Any]:
    url = f"{DISCORD_API_BASE}/invites/{code}?with_counts=true&with_expiration=true"
    try:
        resp = curl_requests.get(url, impersonate="chrome124", timeout=15)
        data = resp.json() if resp.content else {}
        return {
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "data": data,
        }
    except Exception as e:
        return {"ok": False, "status_code": None, "error": str(e), "data": {}}


def summarize_join_result(join_info: Optional[Dict[str, Any]]) -> str:
    if join_info is None:
        return "SKIPPED"
    if join_info.get("ok"):
        guild = join_info.get("data", {}).get("guild", {}) if isinstance(join_info.get("data"), dict) else {}
        guild_name = guild.get("name", "unknown")
        return f"JOIN_OK ({guild_name})"

    data = join_info.get("data") or {}
    if isinstance(data, dict):
        if data.get("captcha_key"):
            return "JOIN_FAIL (captcha_required)"
        if data.get("message"):
            return f"JOIN_FAIL ({data.get('message')})"

    if join_info.get("status_code"):
        return f"JOIN_FAIL ({join_info['status_code']})"
    if join_info.get("reason"):
        return f"JOIN_FAIL ({join_info['reason']})"
    return "JOIN_FAIL (unknown)"


def extract_join_failure_reason(join_info: Optional[Dict[str, Any]]) -> str:
    if join_info is None:
        return "join_disabled"
    if join_info.get("ok"):
        return "success"

    data = join_info.get("data") or {}
    if isinstance(data, dict):
        if data.get("captcha_key"):
            return "captcha_required"
        if data.get("message"):
            return str(data.get("message"))
        if data.get("code"):
            return f"discord_code_{data.get('code')}"

    if join_info.get("reason"):
        return str(join_info.get("reason"))
    if join_info.get("status_code"):
        return f"http_{join_info.get('status_code')}"
    return "unknown"


def save_join_debug(entry: Dict[str, Any]) -> None:
    existing = load_json_file(JOIN_DEBUG_FILE, [])
    existing.append(entry)
    existing = existing[-200:]
    save_json_file(JOIN_DEBUG_FILE, existing)


def join_invite(code: str, captcha_payload: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if not DISCORD_USER_TOKEN:
        return {"ok": False, "reason": "missing_discord_token"}
    url = f"{DISCORD_API_BASE}/invites/{code}"
    headers = build_discord_headers()
    payload = {}

    if captcha_payload:
        if captcha_payload.get("captcha_key"):
            headers["X-Captcha-Key"] = captcha_payload["captcha_key"]
        if captcha_payload.get("captcha_rqtoken"):
            headers["X-Captcha-Rqtoken"] = captcha_payload["captcha_rqtoken"]
        if captcha_payload.get("captcha_session_id"):
            headers["X-Captcha-Session-Id"] = captcha_payload["captcha_session_id"]

    try:
        resp = curl_requests.post(url, headers=headers, json=payload, impersonate="chrome124", timeout=30)
        data = resp.json() if resp.content else {}
        return {
            "ok": resp.status_code in (200, 201, 204),
            "status_code": resp.status_code,
            "data": data,
            "response_headers": dict(resp.headers),
        }
    except Exception as e:
        return {"ok": False, "status_code": None, "reason": str(e), "data": {}}


# --- Playwright + hcaptcha-challenger solver ---

PLAYWRIGHT_INSTANCE: Optional[Playwright] = None
PLAYWRIGHT_BROWSER: Optional[Browser] = None
PLAYWRIGHT_CONTEXT: Optional[BrowserContext] = None
_browser_lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None


def _get_browser_lock() -> asyncio.Lock:
    """Lazy-init lock (must be called inside running event loop)."""
    global _browser_lock
    if _browser_lock is None:
        _browser_lock = asyncio.Lock()
    return _browser_lock


async def get_browser() -> BrowserContext:
    """Get or create a persistent Playwright browser context with crash recovery."""
    global PLAYWRIGHT_INSTANCE, PLAYWRIGHT_BROWSER, PLAYWRIGHT_CONTEXT

    lock = _get_browser_lock()
    async with lock:
        # Check if existing browser is still alive
        if PLAYWRIGHT_BROWSER:
            try:
                if PLAYWRIGHT_BROWSER.is_connected():
                    if PLAYWRIGHT_CONTEXT:
                        return PLAYWRIGHT_CONTEXT
                else:
                    logger.warning("[BROWSER] Browser disconnected, restarting...")
                    await _cleanup_browser()
            except Exception:
                logger.warning("[BROWSER] Browser health check failed, restarting...")
                await _cleanup_browser()

        # Launch fresh browser
        PLAYWRIGHT_INSTANCE = await async_playwright().start()
        PLAYWRIGHT_BROWSER = await PLAYWRIGHT_INSTANCE.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--single-process",
            ],
        )
        PLAYWRIGHT_CONTEXT = await PLAYWRIGHT_BROWSER.new_context(
            user_agent=DISCORD_USER_AGENT,
            locale=DISCORD_LOCALE,
        )
        logger.info("[BROWSER] Playwright Chromium launched (headless)")
        return PLAYWRIGHT_CONTEXT


async def _cleanup_browser() -> None:
    """Safely close browser and playwright instance."""
    global PLAYWRIGHT_BROWSER, PLAYWRIGHT_CONTEXT, PLAYWRIGHT_INSTANCE
    for resource, name in [
        (PLAYWRIGHT_CONTEXT, "context"),
        (PLAYWRIGHT_BROWSER, "browser"),
    ]:
        if resource:
            try:
                await resource.close()
            except Exception as e:
                logger.debug("[BROWSER] Error closing %s: %s", name, e)
    if PLAYWRIGHT_INSTANCE:
        try:
            await PLAYWRIGHT_INSTANCE.stop()
        except Exception as e:
            logger.debug("[BROWSER] Error stopping playwright: %s", e)
    PLAYWRIGHT_BROWSER = None
    PLAYWRIGHT_CONTEXT = None
    PLAYWRIGHT_INSTANCE = None


async def solve_discord_captcha_browser(join_data: Dict[str, Any], max_retries: int = None) -> Dict[str, Any]:
    """
    Solve hCaptcha using Playwright + hcaptcha-challenger with Gemini API.
    Includes retry logic and timing.
    """
    if max_retries is None:
        max_retries = CAPTCHA_MAX_RETRIES

    sitekey = join_data.get("captcha_sitekey")
    rqdata = join_data.get("captcha_rqdata")
    rqtoken = join_data.get("captcha_rqtoken")

    if not sitekey or not rqtoken:
        return {"ok": False, "reason": "missing_captcha_fields"}

    if not GEMINI_API_KEY:
        return {"ok": False, "reason": "missing_gemini_api_key"}

    last_error = "unknown"

    for attempt in range(1, max_retries + 1):
        t_start = _time.monotonic()
        page = None
        try:
            context = await get_browser()
            page = await context.new_page()

            # Build hCaptcha demo page URL with Discord's sitekey
            captcha_url = f"https://accounts.hcaptcha.com/demo?sitekey={sitekey}"
            if rqdata:
                captcha_url += f"&rqdata={rqdata}"

            logger.info("[CAPTCHA] Attempt %d/%d | sitekey=%s", attempt, max_retries, sitekey)
            await page.goto(captcha_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_timeout(3000)
                logger.info("[CAPTCHA] Demo page loaded | title=%s | url=%s", await page.title(), page.url)
            except Exception as e:
                logger.warning("[CAPTCHA] Post-load inspection failed: %s", e)

            # Configure hcaptcha-challenger agent
            agent_config = AgentConfig(
                GEMINI_API_KEY=GEMINI_API_KEY,
                CHALLENGE_CLASSIFIER_MODEL=GEMINI_MODEL,
                IMAGE_CLASSIFIER_MODEL=GEMINI_MODEL,
                SPATIAL_POINT_REASONER_MODEL=GEMINI_MODEL,
                SPATIAL_PATH_REASONER_MODEL=GEMINI_MODEL,
                EXECUTION_TIMEOUT=180,
                RESPONSE_TIMEOUT=60,
                RETRY_ON_FAILURE=False,  # we handle retries ourselves
            )

            agent = AgentV(page=page, agent_config=agent_config)

            # Click the checkbox to trigger captcha
            await agent.robotic_arm.click_checkbox()

            # Wait for challenge and solve
            signal = await agent.wait_for_challenge()

            elapsed = round(_time.monotonic() - t_start, 1)
            signal_name = signal.name if hasattr(signal, "name") else str(signal)
            logger.info("[CAPTCHA] Attempt %d result: %s (%.1fs)", attempt, signal_name, elapsed)

            # Update stats
            CAPTCHA_STATS["total_attempts"] += 1

            # Extract the captcha token from solved response
            if agent.cr_list:
                cr = agent.cr_list[-1]
                if cr.is_pass and cr.generated_pass_UUID:
                    CAPTCHA_STATS["total_success"] += 1
                    CAPTCHA_STATS["last_solve_at"] = datetime.utcnow().isoformat() + "Z"
                    # Rolling average solve time
                    n = CAPTCHA_STATS["total_success"]
                    CAPTCHA_STATS["avg_solve_time"] = round(
                        ((CAPTCHA_STATS["avg_solve_time"] * (n - 1)) + elapsed) / n, 1
                    )
                    logger.info("[CAPTCHA] Got pass UUID (len=%d, %.1fs)", len(cr.generated_pass_UUID), elapsed)
                    return {
                        "ok": True,
                        "captcha_key": cr.generated_pass_UUID,
                        "captcha_rqtoken": rqtoken,
                        "captcha_session_id": join_data.get("captcha_session_id"),
                        "solve_time": elapsed,
                        "attempt": attempt,
                    }

            last_error = f"solver_{signal_name.lower()}"
            CAPTCHA_STATS["total_fail"] += 1

        except Exception as e:
            elapsed = round(_time.monotonic() - t_start, 1)
            last_error = f"browser_error: {str(e)}"
            logger.error("[CAPTCHA] Attempt %d error (%.1fs): %s", attempt, elapsed, e)
            CAPTCHA_STATS["total_attempts"] += 1
            CAPTCHA_STATS["total_fail"] += 1

            # If browser crashed, force cleanup so next attempt gets fresh one
            if "Target page, context or browser has been closed" in str(e) or "Browser has been closed" in str(e):
                logger.warning("[BROWSER] Detected crash, forcing cleanup")
                await _cleanup_browser()

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

        # Brief pause between retries
        if attempt < max_retries:
            await asyncio.sleep(2)

    return {"ok": False, "reason": last_error, "attempts": max_retries}


# --- Alert / Notification ---

async def alert_telegram(chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True)
    except Exception as e:
        logger.error("[ALERT] Failed to send Telegram message to %s: %s", chat_id, e)


def format_invite_alert(
    username: str,
    tweet_id: str,
    tweet_text: str,
    code: str,
    invite_info: Dict[str, Any],
    join_info: Optional[Dict[str, Any]],
    captcha_info: Optional[Dict[str, Any]] = None,
    total_time: Optional[float] = None,
) -> str:
    invite_url = f"https://discord.gg/{code}"
    tweet_url = f"https://x.com/{username}/status/{tweet_id}"
    guild_name = invite_info.get("data", {}).get("guild", {}).get("name", "Unknown server")
    members = invite_info.get("data", {}).get("approximate_member_count", "?")
    online = invite_info.get("data", {}).get("approximate_presence_count", "?")
    expires = invite_info.get("data", {}).get("expires_at")

    if invite_info.get("ok"):
        invite_status = "Valid"
        invite_status_emoji = "✅"
    else:
        invite_status = f"Invalid ({invite_info.get('status_code')})"
        invite_status_emoji = "❌"

    join_result = summarize_join_result(join_info)
    if join_info is None:
        join_status = "Skipped"
        join_emoji = "⏭"
    elif join_info.get("ok"):
        join_status = join_result
        join_emoji = "✅"
    else:
        join_status = join_result
        join_emoji = "❌"

    join_reason = extract_join_failure_reason(join_info)

    if captcha_info is None:
        captcha_line = "Captcha: Not needed\n"
    elif captcha_info.get("ok"):
        solve_time = captcha_info.get("solve_time", "?")
        attempts = captcha_info.get("attempt", "?")
        captcha_line = f"Captcha: ✅ Solved in {solve_time}s on attempt {attempts}\n"
    else:
        captcha_line = f"Captcha: ❌ {captcha_info.get('reason', 'failed')}\n"

    timing_line = f"Processing time: {total_time}s\n" if total_time else ""
    expires_line = f"Invite expiry: {expires}\n" if expires else ""

    snippet = (tweet_text or "").strip().replace("<", "&lt;").replace(">", "&gt;")
    if len(snippet) > 280:
        snippet = snippet[:280] + "..."
    if not snippet:
        snippet = "No tweet snippet available."

    return (
        f"🚨 New Discord invite spotted\n\n"
        f"Source account: @{username}\n"
        f"Server: {guild_name}\n"
        f"Invite code: {code}\n"
        f"Invite link: {invite_url}\n"
        f"Invite status: {invite_status_emoji} {invite_status}\n"
        f"Members: {members} total, {online} online\n"
        f"{expires_line}"
        f"Join attempt: {join_emoji} {join_status}\n"
        f"Join details: {join_reason}\n"
        f"{captcha_line}"
        f"{timing_line}"
        f"Tweet link: {tweet_url}\n\n"
        f"Tweet snippet:\n{snippet}"
    )


def join_enabled_for_chat(chat_id: str) -> bool:
    return STATE.get("join_enabled_per_chat", {}).get(chat_id, DISCORD_JOIN_ENABLED)


def track_account_error(account_key: str, success: bool) -> None:
    """Track consecutive errors per account for auto-mute."""
    if success:
        ACCOUNT_ERRORS.pop(account_key, None)
    else:
        ACCOUNT_ERRORS[account_key] = ACCOUNT_ERRORS.get(account_key, 0) + 1


async def process_code(chat_id: int, username: str, tweet: Any, code: str) -> None:
    chat_id_str = str(chat_id)
    code_key = code.lower()
    seen_codes = STATE.setdefault("seen_codes", {})
    dedupe_key = f"{chat_id_str}:{code_key}"
    if dedupe_key in seen_codes:
        return

    t_start = _time.monotonic()
    logger.info("[CODE] @%s | tweet=%s | code=%s", username, getattr(tweet, "id", "?"), code)

    invite_info = await asyncio.to_thread(validate_invite, code)
    guild_name = invite_info.get("data", {}).get("guild", {}).get("name", "unknown") if invite_info.get("ok") else "invalid"
    logger.info("[INVITE] code=%s | valid=%s | status=%s | guild=%s", code, invite_info.get("ok"), invite_info.get("status_code"), guild_name)

    join_info = None
    captcha_info = None
    join_allowed = join_enabled_for_chat(chat_id_str)

    if invite_info.get("ok") and join_allowed:
        logger.info("[JOIN] attempting | code=%s", code)
        join_info = await asyncio.to_thread(join_invite, code)
        logger.info("[JOIN] result | code=%s | %s", code, summarize_join_result(join_info))

        if not join_info.get("ok") and extract_join_failure_reason(join_info) == "captcha_required":
            logger.info("[CAPTCHA] solving via browser | code=%s", code)
            captcha_info = await solve_discord_captcha_browser(join_info.get("data", {}))
            if captcha_info.get("ok"):
                logger.info(
                    "[CAPTCHA] solved | code=%s | time=%.1fs | attempt=%s",
                    code, captcha_info.get("solve_time", 0), captcha_info.get("attempt", "?")
                )
                join_info = await asyncio.to_thread(join_invite, code, captcha_info)
                logger.info("[JOIN] retry result | code=%s | %s", code, summarize_join_result(join_info))
            else:
                logger.warning("[CAPTCHA] solve failed | code=%s | reason=%s", code, captcha_info.get("reason"))
                join_info["captcha_solver"] = captcha_info
    elif invite_info.get("ok") and not join_allowed:
        logger.info("[JOIN] skipped | code=%s | reason=disabled", code)

    total_time = round(_time.monotonic() - t_start, 1)

    seen_codes[dedupe_key] = {
        "tweet_id": str(tweet.id),
        "username": username,
        "at": datetime.utcnow().isoformat() + "Z",
        "invite_status": invite_info.get("status_code"),
        "join_status": None if join_info is None else join_info.get("status_code"),
        "join_reason": extract_join_failure_reason(join_info),
        "captcha_solved": captcha_info.get("ok") if captcha_info else None,
        "total_time": total_time,
    }
    await save_data()

    if join_info is not None and not join_info.get("ok"):
        save_join_debug({
            "at": datetime.utcnow().isoformat() + "Z",
            "username": username,
            "tweet_id": str(tweet.id),
            "code": code,
            "invite_status": invite_info.get("status_code"),
            "invite_guild": invite_info.get("data", {}).get("guild", {}).get("name"),
            "join_status": join_info.get("status_code"),
            "join_reason": extract_join_failure_reason(join_info),
            "join_data": join_info.get("data"),
            "response_headers": join_info.get("response_headers"),
            "captcha_solver": captcha_info,
            "total_time": total_time,
        })

    text = format_invite_alert(
        username, str(tweet.id), getattr(tweet, "text", ""),
        code, invite_info, join_info,
        captcha_info=captcha_info,
        total_time=total_time,
    )
    await alert_telegram(chat_id, text)


async def check_account(username: str, chat_id: int) -> None:
    chat_id_str = str(chat_id)
    account_key = get_account_key(chat_id_str, username)

    # Check if account exceeded error threshold
    error_count = ACCOUNT_ERRORS.get(account_key, 0)
    if error_count >= ACCOUNT_ERROR_THRESHOLD:
        logger.warning("[MONITOR] @%s auto-paused after %d consecutive errors", username, error_count)
        return

    try:
        user = await get_user_with_cache(username)
    except Exception as e:
        track_account_error(account_key, False)
        raise

    await asyncio.sleep(get_human_delay())
    try:
        tweets = await twitter_client.get_user_tweets(user.id, "TweetsAndReplies", count=TWEET_FETCH_COUNT)
    except KeyError as e:
        if str(e).strip("'") == "Tweetsandreplies":
            if LOG_TWEETS_AND_REPLIES_FALLBACK:
                logger.warning("Twikit category TweetsAndReplies unsupported, falling back to Tweets for @%s", username)
            tweets = await twitter_client.get_user_tweets(user.id, "Tweets", count=TWEET_FETCH_COUNT)
        else:
            track_account_error(account_key, False)
            raise
    except Exception:
        track_account_error(account_key, False)
        raise

    tweets = list(tweets) if tweets else []
    if not tweets:
        track_account_error(account_key, True)  # no tweets is fine
        return

    last_id = STATE.setdefault("last_tweets", {}).get(account_key)
    if not last_id:
        STATE["last_tweets"][account_key] = str(tweets[0].id)
        await save_data()
        logger.info("Initialized baseline for @%s", username)
        track_account_error(account_key, True)
        return

    new_tweets = []
    for tweet in tweets:
        if int(tweet.id) <= int(last_id):
            break
        new_tweets.append(tweet)

    if not new_tweets:
        track_account_error(account_key, True)
        return

    STATE["last_tweets"][account_key] = str(new_tweets[0].id)
    await save_data()

    logger.info("[CHECK] @%s | fetched=%s | new=%s", username, len(tweets), len(new_tweets))

    for tweet in reversed(new_tweets):
        text = get_tweet_text_with_expanded_urls(tweet)
        codes = extract_discord_codes(text)
        if not codes:
            continue
        logger.info("[MATCH] @%s | tweet=%s | codes=%s", username, tweet.id, ", ".join(codes))
        for code in codes:
            await process_code(chat_id, username, tweet, code)
        await asyncio.sleep(1)

    track_account_error(account_key, True)


async def _check_account_safe(username: str, chat_id_str: str, semaphore: asyncio.Semaphore) -> None:
    global RATE_LIMIT_UNTIL
    async with semaphore:
        try:
            await check_account(username, int(chat_id_str))
        except Exception as e:
            logger.error("[ERROR] checking @%s failed: %s", username, e)
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("rate limit", "429", "unauthorized", "403")):
                logger.warning("[TOKEN] Rate limited or auth error, rotating Twitter token")
                RATE_LIMIT_UNTIL = max(RATE_LIMIT_UNTIL, _time.monotonic() + RATE_LIMIT_BACKOFF_SECONDS)
                await switch_twitter_token()


async def monitoring_loop() -> None:
    global RATE_LIMIT_UNTIL
    logger.info("Monitoring loop started | mode=%s | interval=%ss | fetch_count=%s | concurrency=%s", POLL_MODE, CHECK_INTERVAL, TWEET_FETCH_COUNT, MAX_CONCURRENT_CHECKS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    while True:
        try:
            if not MONITORED:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            now = _time.monotonic()
            if RATE_LIMIT_UNTIL > now:
                backoff = round(RATE_LIMIT_UNTIL - now, 1)
                logger.warning("[TOKEN] In temporary backoff for %.1fs due to recent rate limit", backoff)
                await asyncio.sleep(min(backoff, CHECK_INTERVAL))
                continue

            tasks = []
            for chat_id_str, accounts in list(MONITORED.items()):
                for username, settings in list(accounts.items()):
                    if settings.get("muted"):
                        continue
                    tasks.append(asyncio.create_task(_check_account_safe(username, chat_id_str, semaphore)))
                    await asyncio.sleep(max(0.1, DELAY_BETWEEN_ACCOUNTS))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error("Monitoring loop failure: %s", e)
            await asyncio.sleep(CHECK_INTERVAL)


# --- Telegram Commands ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id):
        return
    join_state = "ON" if join_enabled_for_chat(str(message.chat.id)) else "OFF"
    captcha_state = "✅ ready" if GEMINI_API_KEY else "❌ no GEMINI_API_KEY"
    await message.reply(
        "Discord Invite Hunter aktif.\n\n"
        "Commands:\n"
        "/add <username>\n"
        "/remove <username>\n"
        "/list\n"
        "/status\n"
        "/join_on\n"
        "/join_off\n"
        "/reset_errors\n"
        "/help\n\n"
        f"Auto join: {join_state}\n"
        f"Captcha solver: {captcha_state}"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_authorized(message.from_user.id):
        return
    await message.reply(
        "Pakai /add username buat monitor akun X.\n"
        "Kalau tweet ada code Discord, bot validate invite, optionally join "
        "(dengan auto-captcha solver), lalu kirim alert ke Telegram.\n\n"
        "Commands:\n"
        "/add <username> — tambahin akun\n"
        "/remove <username> — hapus akun\n"
        "/list — list semua akun\n"
        "/status — status bot + captcha stats\n"
        "/join_on — aktifkan auto join\n"
        "/join_off — matikan auto join\n"
        "/reset_errors — reset error counter semua akun"
    )


@dp.message(Command("add"))
async def cmd_add(message: Message):
    if not is_authorized(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Format: /add username")
        return
    username = parts[1].strip().lstrip("@").lower()
    chat_id_str = str(message.chat.id)
    MONITORED.setdefault(chat_id_str, {})
    if username in MONITORED[chat_id_str]:
        await message.reply(f"@{username} udah dimonitor")
        return

    try:
        user = await get_user_with_cache(username)
    except Exception as e:
        await message.reply(f"Gagal fetch @{username}: {e}")
        return

    tweets = await twitter_client.get_user_tweets(user.id, "Tweets", count=1)
    tweets = list(tweets) if tweets else []

    MONITORED[chat_id_str][username] = {
        "muted": False,
        "added_at": datetime.utcnow().isoformat() + "Z",
        "user_data": {
            "real_name": getattr(user, "name", username),
            "followers": getattr(user, "followers_count", 0),
            "following": getattr(user, "following_count", 0),
        }
    }
    if tweets:
        STATE.setdefault("last_tweets", {})[get_account_key(chat_id_str, username)] = str(tweets[0].id)
    await save_data()
    await message.reply(f"Mantap. @{username} sekarang dimonitor.")


@dp.message(Command("remove"))
async def cmd_remove(message: Message):
    if not is_authorized(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Format: /remove username")
        return
    username = parts[1].strip().lstrip("@").lower()
    chat_id_str = str(message.chat.id)
    if username not in MONITORED.get(chat_id_str, {}):
        await message.reply(f"@{username} nggak ada di monitor list")
        return
    del MONITORED[chat_id_str][username]
    STATE.setdefault("last_tweets", {}).pop(get_account_key(chat_id_str, username), None)
    ACCOUNT_ERRORS.pop(get_account_key(chat_id_str, username), None)
    await save_data()
    await message.reply(f"Sip, @{username} dihapus dari monitor list.")


@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_authorized(message.from_user.id):
        return
    chat_id_str = str(message.chat.id)
    accounts = MONITORED.get(chat_id_str, {})
    if not accounts:
        await message.reply("Belum ada akun yang dimonitor.")
        return
    lines = []
    for u in sorted(accounts.keys()):
        err_count = ACCOUNT_ERRORS.get(get_account_key(chat_id_str, u), 0)
        status = ""
        if err_count >= ACCOUNT_ERROR_THRESHOLD:
            status = " ⚠️ paused (errors)"
        elif err_count > 0:
            status = f" ({err_count} errors)"
        lines.append(f"- @{u}{status}")
    text = "Akun yang dimonitor:\n" + "\n".join(lines)
    await message.reply(text)


@dp.message(Command("join_on"))
async def cmd_join_on(message: Message):
    if not is_authorized(message.from_user.id):
        return
    STATE.setdefault("join_enabled_per_chat", {})[str(message.chat.id)] = True
    await save_data()
    await message.reply("Auto join Discord diaktifkan untuk chat ini.")


@dp.message(Command("join_off"))
async def cmd_join_off(message: Message):
    if not is_authorized(message.from_user.id):
        return
    STATE.setdefault("join_enabled_per_chat", {})[str(message.chat.id)] = False
    await save_data()
    await message.reply("Auto join Discord dimatikan untuk chat ini.")


@dp.message(Command("reset_errors"))
async def cmd_reset_errors(message: Message):
    if not is_authorized(message.from_user.id):
        return
    cleared = len(ACCOUNT_ERRORS)
    ACCOUNT_ERRORS.clear()
    await message.reply(f"Error counter di-reset ({cleared} entries cleared).")


@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not is_authorized(message.from_user.id):
        return
    chat_id_str = str(message.chat.id)
    accounts = MONITORED.get(chat_id_str, {})
    seen = len([k for k in STATE.get("seen_codes", {}) if k.startswith(chat_id_str + ":")])
    join_state = "ON" if join_enabled_for_chat(chat_id_str) else "OFF"
    captcha_ready = "✅" if GEMINI_API_KEY else "❌ no key"

    # Captcha stats
    cs = CAPTCHA_STATS
    captcha_rate = f"{cs['total_success']}/{cs['total_attempts']}" if cs['total_attempts'] > 0 else "0/0"
    captcha_pct = round(cs['total_success'] / cs['total_attempts'] * 100) if cs['total_attempts'] > 0 else 0

    # Paused accounts
    paused = sum(1 for k, v in ACCOUNT_ERRORS.items() if v >= ACCOUNT_ERROR_THRESHOLD)

    # Browser status
    browser_status = "🟢 running" if (PLAYWRIGHT_BROWSER and PLAYWRIGHT_BROWSER.is_connected()) else "⚪ idle"

    await message.reply(
        f"📊 Bot Status\n\n"
        f"Monitored: {len(accounts)} accounts\n"
        f"Paused (errors): {paused}\n"
        f"Seen codes: {seen}\n"
        f"Auto join: {join_state}\n"
        f"Twitter tokens: {len(TWITTER_TOKENS)}\n\n"
        f"🔐 Captcha Solver\n"
        f"Status: {captcha_ready}\n"
        f"Browser: {browser_status}\n"
        f"Solved: {captcha_rate} ({captcha_pct}%)\n"
        f"Avg time: {cs['avg_solve_time']}s\n"
        f"Last solve: {cs['last_solve_at'] or 'never'}"
    )


# --- Startup & Shutdown ---

async def on_shutdown() -> None:
    """Graceful shutdown: cleanup browser."""
    logger.info("Shutting down...")
    await _cleanup_browser()
    logger.info("Browser cleaned up")


async def main() -> None:
    global TWITTER_TOKENS
    load_data()
    TWITTER_TOKENS = load_twitter_tokens()
    ok = await authenticate_twitter()
    if not ok:
        raise RuntimeError("Twitter auth failed")

    logger.info(
        "Bot starting | accounts=%d | tokens=%d | captcha=%s | join=%s",
        sum(len(v) for v in MONITORED.values()),
        len(TWITTER_TOKENS),
        "ready" if GEMINI_API_KEY else "no_key",
        "enabled" if DISCORD_JOIN_ENABLED else "disabled",
    )

    # Pre-warm browser if Gemini key is configured
    if GEMINI_API_KEY:
        try:
            await get_browser()
            logger.info("[BROWSER] Pre-warmed successfully")
        except Exception as e:
            logger.warning("[BROWSER] Pre-warm failed (will retry on demand): %s", e)

    asyncio.create_task(monitoring_loop())

    # Register shutdown handler
    dp.shutdown.register(on_shutdown)

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
