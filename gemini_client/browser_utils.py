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

def get_cookies_from_browser(browser: str = "edge"):
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