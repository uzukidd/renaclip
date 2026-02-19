"""
Gemini client module: client factory, single/stream/chat runners, Gem API.

- get_client(): build GeminiClient from env cookies or browser (browser-cookie3).
- run_single / run_stream / run_chat: demo runners.
- Gem API: create_gem, update_gem, delete_gem, ensure_gem_exists, generate_with_gem, chat_with_gem.
  Call sites that use a gem by id/name raise GemNotFoundError if the gem does not exist.
"""

import json
import os
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Local SOCKS5 proxy (set SOCKS5_PROXY env to use proxy, leave empty to disable)
# DEFAULT_PROXY is None by default - proxy is only used if SOCKS5_PROXY env is set
DEFAULT_PROXY = None

# Cookie names we care about
COOKIE_PSID = "__Secure-1PSID"
COOKIE_PSIDTS = "__Secure-1PSIDTS"


def _parse_cookie_header(cookie_header: str) -> dict:
    """Parse Cookie header value into dict of name -> value."""
    out = {}
    if not cookie_header or not isinstance(cookie_header, str):
        return out
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            out[name.strip()] = value.strip()
    return out


def _parse_set_cookie_headers(set_cookie_value) -> dict:
    """
    Parse Set-Cookie header(s) into dict of name -> value.
    CDP may give one string (multiple cookies joined by \\n) or we pass raw header value.
    """
    out = {}
    if set_cookie_value is None:
        return out
    if isinstance(set_cookie_value, list):
        for item in set_cookie_value:
            out.update(_parse_set_cookie_headers(item))
        return out
    s = set_cookie_value if isinstance(set_cookie_value, str) else str(set_cookie_value)
    # Multiple Set-Cookie lines: "name1=val1; path=...\\nname2=val2; path=..."
    for block in re.split(r"[\r\n]+", s):
        block = block.strip()
        if not block:
            continue
        # First segment is name=value, rest are attributes
        part = block.split(";")[0].strip()
        if "=" in part:
            name, _, value = part.partition("=")
            out[name.strip()] = value.strip()
    return out


def _extract_psid_from_cookie_dict(cookie_dict: dict) -> tuple:
    """Get (psid, psidts) from a dict of cookie name -> value."""
    return cookie_dict.get(COOKIE_PSID), cookie_dict.get(COOKIE_PSIDTS)


def _collect_cookies_from_performance_log(driver) -> tuple:
    """
    Read performance log and extract __Secure-1PSID / __Secure-1PSIDTS from
    request Cookie headers and response Set-Cookie headers.
    Returns (psid, psidts) or (None, None).
    """
    psid, psidts = None, None
    try:
        logs = driver.get_log("performance")
    except Exception:
        return None, None
    for entry in logs:
        try:
            msg = entry.get("message")
            if not msg:
                continue
            data = json.loads(msg)
            message = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
            method = message.get("method", "")
            params = message.get("params") or {}
            if method == "Network.requestWillBeSent":
                request = params.get("request") or {}
                headers = request.get("headers") or {}
                # CDP header keys may be lowercase
                cookie_raw = headers.get("Cookie") or headers.get("cookie")
                if cookie_raw:
                    parsed = _parse_cookie_header(cookie_raw)
                    p, t = _extract_psid_from_cookie_dict(parsed)
                    if p:
                        psid = p
                    if t:
                        psidts = t
            elif method == "Network.responseReceived":
                response = params.get("response") or {}
                headers = response.get("headers") or {}
                set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie")
                if set_cookie:
                    parsed = _parse_set_cookie_headers(set_cookie)
                    p, t = _extract_psid_from_cookie_dict(parsed)
                    if p:
                        psid = p
                    if t:
                        psidts = t
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return psid, psidts


def get_cookies_from_browser():
    """
    Launch browser with visible window (Edge or Chrome), open https://gemini.google.com,
    and wait for user to log in. Collects __Secure-1PSID and __Secure-1PSIDTS by:
    - Polling document cookies every 3 seconds, and
    - Hooking all requests: reading Cookie from request headers and Set-Cookie from
      response headers (via CDP performance log), and extracting the two cookie values.

    Browser type is selected via config/env:
    - COOKIE_BROWSER = "edge" (default) or "chrome"

    Uses current directory as browser user data directory (.edge_user_data / .chrome_user_data)
    to avoid conflicts with already running browsers.

    If user closes the browser window, raises an exception.

    Returns tuple (psid, psidts) or (None, None) if not found.
    """
    driver = None
    try:
        # Decide which browser to use
        browser = (os.environ.get("COOKIE_BROWSER") or "edge").strip().lower()
        if browser not in {"edge", "chrome"}:
            browser = "edge"

        # Use current directory as browser user data directory
        current_dir = os.getcwd()
        if browser == "chrome":
            user_data_dir = os.path.join(current_dir, ".chrome_user_data")
        else:
            user_data_dir = os.path.join(current_dir, ".edge_user_data")

        profile_name = "Default"

        # Create directory if it doesn't exist
        os.makedirs(user_data_dir, exist_ok=True)

        # Configure browser options
        if browser == "chrome":
            options = ChromeOptions()
        else:
            options = EdgeOptions()

        # Use current directory for user data
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument(f"--profile-directory={profile_name}")

        # Add remote debugging port to avoid conflicts
        debug_port = 9000
        options.add_argument(f"--remote-debugging-port={debug_port}")

        # Add stability arguments
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Enable performance log to hook request Cookie and response Set-Cookie headers (Chrome/Edge Chromium)
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # Do NOT use headless mode - user needs to see the window to log in

        # Create browser driver
        print(f"Starting {browser.capitalize()} browser (user data: {user_data_dir})...", flush=True)
        if browser == "chrome":
            driver = webdriver.Chrome(options=options)
        else:
            driver = webdriver.Edge(options=options)

        # Navigate to Gemini website
        print("Opening https://gemini.google.com - please log in...", flush=True)
        driver.get("https://gemini.google.com")
        
        # Wait for page to load
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Poll for cookies every 3 seconds
        print("Waiting for login cookies...", flush=True)
        max_wait_time = 300  # Maximum 5 minutes
        elapsed_time = 0
        
        while elapsed_time < max_wait_time:
            # Check if browser window is still open
            try:
                # Try to get window handles - if this fails, window is closed
                window_handles = driver.window_handles
                if not window_handles:
                    raise RuntimeError("Browser window was closed by user")
                
                # Try to get current URL - if this fails, session is lost
                current_url = driver.current_url
            except Exception as e:
                error_msg = str(e).lower()
                if "no such window" in error_msg or "invalid session id" in error_msg:
                    raise RuntimeError("Browser window was closed by user. Please keep the window open until login is complete.")
                raise
            
            # 1) Get cookies from document (current page cookies)
            cookies = driver.get_cookies()
            psid, psidts = None, None
            for cookie in cookies:
                if cookie["name"] == COOKIE_PSID:
                    psid = cookie["value"]
                elif cookie["name"] == COOKIE_PSIDTS:
                    psidts = cookie["value"]

            # 2) Hook network: get Cookie from request headers and Set-Cookie from response headers
            net_psid, net_psidts = _collect_cookies_from_performance_log(driver)
            if net_psid:
                psid = net_psid
            if net_psidts:
                psidts = net_psidts

            # Return as soon as we have at least __Secure-1PSID
            if psid and psidts:
                print(
                    f"\nSuccessfully retrieved cookies: __Secure-1PSID=***, "
                    f"__Secure-1PSIDTS={'***' if psidts else 'not found'}",
                    flush=True,
                )
                return psid, psidts
            
            # Wait 3 seconds before next check
            print(".", end="", flush=True)
            time.sleep(3)
            elapsed_time += 3
        
        # Timeout
        print("\nTimeout: Cookies not found after waiting.", flush=True)
        return None, None
        
    except RuntimeError as e:
        # Re-raise RuntimeError (window closed)
        print(f"\nError: {e}", file=sys.stderr, flush=True)
        raise
    except Exception as e:
        error_msg = str(e)
        if "DevToolsActivePort" in error_msg or "session not created" in error_msg.lower():
            print(
                "\nError: Edge browser failed to start. This usually happens when:\n"
                "1. Edge browser is already running and using the same profile\n"
                "2. The user data directory is locked\n\n"
                "Solutions:\n"
                "  - Close all Edge browser windows and try again\n"
                "  - Or delete the .edge_user_data directory in current folder\n",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"Error retrieving cookies from browser: {e}", file=sys.stderr, flush=True)
        return None, None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def get_client(psid=None, psidts=None):
    """Build GeminiClient from env cookies or browser (browser-cookie3)."""
    from gemini_webapi import GeminiClient

    # Only use proxy if SOCKS5_PROXY env is explicitly set
    env_proxy = os.environ.get("SOCKS5_PROXY")
    if env_proxy is None:
        proxy = DEFAULT_PROXY  # None by default - no proxy
    else:
        proxy = env_proxy.strip() or None  # Empty string becomes None
    
    if proxy:
        print(f"Using proxy: {proxy}", flush=True)

    if psid is None:
        psid = (os.environ.get("GEMINI_1PSID") or "").strip()
    if psidts is None:
        psidts = (os.environ.get("GEMINI_1PSIDTS") or "").strip()

    if psid:
        has_psidts = "yes" if psidts else "no"
        print(f"Using env cookies: GEMINI_1PSID=***, GEMINI_1PSIDTS={has_psidts}", flush=True)
        print(f"psid: {psid}")
        if psid == "auto":
            # Use selenium to launch Edge browser and get cookies
            try:
                psid, psidts = get_cookies_from_browser()
                if not psid:
                    raise ValueError("Failed to retrieve __Secure-1PSID cookie from browser")
            except RuntimeError as e:
                # Re-raise RuntimeError (browser window closed by user)
                raise ValueError(f"Login process interrupted: {e}") from e
            
        return GeminiClient(psid, psidts, proxy=proxy)
    try:
        return GeminiClient(proxy=proxy)
    except Exception as e:
        print(
            "Error: Set GEMINI_1PSID (and optionally GEMINI_1PSIDTS), "
            "or install browser-cookie3 and login at https://gemini.google.com",
            file=sys.stderr,
        )
        raise SystemExit(1) from e


# ---------------------------------------------------------------------------
# Runners: single-turn, stream, chat
# ---------------------------------------------------------------------------


async def _delete_chat_after(client, chat):
    """Delete conversation from Gemini history after use."""
    try:
        await client.delete_chat(chat.cid)
    except Exception as e:
        print(f"[Warning] delete_chat failed: {e}", file=sys.stderr)


async def run_single(client, prompt: str = "Say hello in one sentence."):
    """Single-turn via one-shot chat; conversation is deleted after use."""
    print("Single-turn:", repr(prompt))
    print("-" * 40)
    chat = client.start_chat()
    try:
        response = await chat.send_message(prompt)
        print(response.text)
        if response.images:
            print("\n[Images in response]:", len(response.images))
    finally:
        await _delete_chat_after(client, chat)


async def run_stream(client, prompt: str = "Count from 1 to 5, one number per line."):
    """Streaming via chat; conversation is deleted after use."""
    print("Streaming:", repr(prompt))
    print("-" * 40)
    chat = client.start_chat()
    try:
        async for chunk in chat.send_message_stream(prompt):
            if chunk.text_delta:
                print(chunk.text_delta, end="", flush=True)
        print()
    finally:
        await _delete_chat_after(client, chat)


async def run_chat(client, prompts: list[str] | None = None):
    """Multi-turn chat with start_chat / send_message; conversation is deleted after use."""
    if prompts is None:
        prompts = [
            "My name is Demo. Remember it.",
            "What is my name?",
        ]
    print("Multi-turn chat")
    print("-" * 40)
    chat = client.start_chat()
    try:
        for i, msg in enumerate(prompts, 1):
            print(f"[User {i}]: {msg}")
            response = await chat.send_message(msg)
            print(f"[Gemini]: {response.text}\n")
    finally:
        await _delete_chat_after(client, chat)


# ---------------------------------------------------------------------------
# Gem API: create, update, get by id/name, generate with gem (checks existence)
# ---------------------------------------------------------------------------

# Name prefix for gems created/managed by RenaClip. Display and config use names without this.
RENACLIP_PREFIX = "[RenaClip]"


class GemNotFoundError(Exception):
    """Raised when a gem does not exist (by id or name)."""

    def __init__(self, identifier: str, by: str = "id"):
        self.identifier = identifier
        self.by = by
        super().__init__(f"Gem not found: {by}={identifier!r}")


async def fetch_gems_cached(client, *, include_hidden: bool = False, language: str = "en"):
    """Fetch gems and cache in client.gems. Idempotent."""
    await client.fetch_gems(include_hidden=include_hidden, language=language)
    return client.gems


def get_gem_by_id_or_name(client, gem_identifier: str):
    """
    Look up a gem by id or name from client.gems (must call fetch_gems_cached first).
    Returns the gem object or None if not found.
    """
    if not hasattr(client, "gems") or client.gems is None:
        return None
    g = client.gems.get(id=gem_identifier)
    if g is not None:
        return g
    return client.gems.get(name=gem_identifier)


async def ensure_gem_exists(client, gem_identifier: str):
    """
    Fetch gems, then resolve gem by id or name. Raise GemNotFoundError if not found.
    """
    await fetch_gems_cached(client)
    gem = get_gem_by_id_or_name(client, gem_identifier)
    if gem is None:
        raise GemNotFoundError(gem_identifier, "id or name")
    return gem


async def get_or_create_gem(client, name: str, prompt: str, description: str = ""):
    """
    Get a gem by name from cache; if not found, create it with the given name, prompt, description.
    name: API name (e.g. [RenaClip]DisplayName). Returns (gem, created: bool).
    """
    await fetch_gems_cached(client)
    gem = get_gem_by_id_or_name(client, name)
    if gem is not None:
        return gem, False
    new_gem = await create_gem(client, name, prompt, description)
    return new_gem, True


async def create_gem(client, name: str, prompt: str, description: str = ""):
    """
    Create a custom gem. name: full API name (e.g. [RenaClip]DisplayName).
    Returns the created gem object.
    """
    new_gem = await client.create_gem(
        name=name,
        prompt=prompt,
        description=description or "",
    )
    return new_gem


async def update_gem(client, gem_or_id, name: str, prompt: str, description: str = ""):
    """
    Update an existing custom gem. name: full API name (e.g. [RenaClip]DisplayName).
    """
    updated = await client.update_gem(
        gem=gem_or_id,
        name=name,
        prompt=prompt,
        description=description or "",
    )
    return updated


async def delete_gem(client, gem_or_id):
    """Delete a custom gem. gem_or_id: Gem object or gem id string."""
    await client.delete_gem(gem_or_id)
    return True


def _iter_all_gems(client):
    """Yield all gem objects from client.gems after fetch_gems_cached. Best-effort."""
    if not hasattr(client, "gems") or client.gems is None:
        return
    try:
        if hasattr(client.gems, "values"):
            yield from client.gems.values()
        elif hasattr(client.gems, "__iter__") and not isinstance(client.gems, (str, bytes)):
            yield from client.gems
        else:
            pass
    except Exception:
        pass


async def delete_renaclip_gems_not_in_config(client, config_display_names: list[str]):
    """
    Fetch all gems; for each gem whose name starts with RENACLIP_PREFIX, if the
    display name (name without prefix) is not in config_display_names, delete it.
    """
    await fetch_gems_cached(client)
    config_set = set(config_display_names)
    for gem in _iter_all_gems(client):
        try:
            name = getattr(gem, "name", None) or ""
            if not name.startswith(RENACLIP_PREFIX):
                continue
            display_name = name[len(RENACLIP_PREFIX) :].strip()
            if display_name and display_name not in config_set:
                await delete_gem(client, gem)
                print(f"[RenaClip] Deleted orphan gem: {name!r}", flush=True)
        except Exception as e:
            print(f"[RenaClip] Failed to check/delete gem: {e}", file=sys.stderr, flush=True)


async def generate_with_gem(client, prompt: str, gem_identifier: str, **kwargs):
    """
    Generate content using a gem specified by id or name.
    If the gem does not exist, raises GemNotFoundError.
    kwargs are passed through to client.generate_content (e.g. model, files).
    """
    gem = await ensure_gem_exists(client, gem_identifier)
    return await client.generate_content(prompt, gem=gem, **kwargs)


async def chat_with_gem(client, gem_identifier: str, **kwargs):
    """
    Start a chat session with a gem. If the gem does not exist, raises GemNotFoundError.
    kwargs are passed through to client.start_chat (e.g. model).
    Returns (gem, chat_session).
    """
    gem = await ensure_gem_exists(client, gem_identifier)
    chat = client.start_chat(gem=gem, **kwargs)
    return gem, chat
