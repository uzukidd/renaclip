"""
Deprecated: Use renaclip.py instead.
  python renaclip.py --service               # Run clipboard service
  python renaclip.py --service --gem X --gem Y  # With explicit gems
"""

import asyncio
import os
import subprocess
import sys
import threading

import pyperclip

from gemini_client import (
    RENACLIP_PREFIX,
    GemNotFoundError,
    _delete_chat_after,
    delete_renaclip_gems_not_in_config,
    ensure_gem_exists,
    get_client,
    get_or_create_gem,
    update_gem,
)

try:
    from win10toast import ToastNotifier
except ImportError:  # pragma: no cover - optional dependency
    ToastNotifier = None

try:
    import pystray
    from PIL import Image, ImageDraw
    PYSTRAY_AVAILABLE = True
except ImportError:
    pystray = None
    PYSTRAY_AVAILABLE = False

_notifier = ToastNotifier() if ToastNotifier is not None else None


def _create_tray_icon_image(size: int = 64):
    """Create a simple tray icon image (RenaClip 'R' on dark background). Fallback when no asset."""
    img = Image.new("RGB", (size, size), (40, 44, 52))
    draw = ImageDraw.Draw(img)
    margin = size // 4
    draw.rounded_rectangle((margin, margin, size - margin, size - margin), radius=6, outline=(97, 175, 239), width=3)
    draw.line([(margin + size // 6, margin), (margin + size // 6, size - margin)], fill=(97, 175, 239), width=2)
    draw.line([(margin + size // 6, margin + size // 3), (size - margin - 2, margin + size // 3)], fill=(97, 175, 239), width=2)
    draw.line([(size - margin - 2, margin + size // 3), (size - margin - 2, size - margin)], fill=(97, 175, 239), width=2)
    draw.line([(margin + size // 6, margin + size // 2), (size - margin - 2, size - margin)], fill=(97, 175, 239), width=2)
    return img


def _load_tray_icon(size: int = 64):
    """Load tray icon from assets/renaclip_icon.png; fallback to drawn icon if missing."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(app_dir, "assets", "renaclip_icon.png")
    try:
        if os.path.isfile(path):
            img = Image.open(path).convert("RGBA")
            resample = getattr(Image, "Resampling", None)
            filter_ = resample.LANCZOS if resample else getattr(Image, "LANCZOS", 1)
            img = img.resize((size, size), filter_)
            return img
    except Exception:
        pass
    return _create_tray_icon_image(size)


def _run_tray_icon(icon):
    """Run pystray icon (blocking); used in a separate thread."""
    icon.run()


def _is_ui_process_running(app_dir: str) -> bool:
    """Return True if .renaclip_ui.lock exists and the recorded PID is still running."""
    lock_file = os.path.join(app_dir, ".renaclip_ui.lock")
    if not os.path.isfile(lock_file):
        return False
    try:
        with open(lock_file, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return False


def _launch_renaclip_ui():
    """Launch renaclip.py (RenaClip UI) in a subprocess. Skip if UI is already running."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if _is_ui_process_running(app_dir):
        return
    lock_file = os.path.join(app_dir, ".renaclip_ui.lock")
    try:
        if os.path.isfile(lock_file):
            os.remove(lock_file)
    except OSError:
        pass
    script = os.path.join(app_dir, "renaclip.py")
    if not os.path.isfile(script):
        return
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [sys.executable, script],
            cwd=app_dir,
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def show_notification(title: str, message: str) -> None:
    """Show a Windows toast notification if notifier is available."""
    if _notifier is None:
        return
    try:
        # win10toast has a known bug with threaded=True causing WNDPROC errors
        # Use threaded=False to avoid the issue, or catch the specific error
        _notifier.show_toast(title, message, duration=5, threaded=False)
    except (TypeError, ValueError) as e:
        # Catch WNDPROC/WPARAM errors which are harmless but noisy
        if "WPARAM" in str(e) or "WNDPROC" in str(e) or "LRESULT" in str(e):
            pass  # Silently ignore this known win10toast bug
        else:
            print(f"[Notification error] {e}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[Notification error] {e}", file=sys.stderr, flush=True)


def get_gem_specs(arg_gems: list[str] | None) -> list[dict]:
    """
    Return list of gem specs. Each spec is either:
    - from --gem: {"name": name_or_id} -> ensure_gem_exists (must exist).
    - from gem_config.json gems; else GEM_LIST.
    """
    if arg_gems:
        return [{"name": s.strip()} for s in arg_gems if s and s.strip()]

    from config_loader import load_config
    cfg_gems, _ = load_config()
    if cfg_gems:
        return cfg_gems



async def process_clipboard_with_gem(client, gem, text: str, model: str = "unspecified") -> None:
    """Send text to gem, put response (or error) back to clipboard, then delete chat."""
    if client is None:
        print("[Warning] Client is None, cannot process clipboard with gem.", flush=True)
        show_notification("RenaClip", "Client is not initialized. Cannot process clipboard.")
        return
    if not text or not text.strip():
        return
    chat = client.start_chat(gem=gem, model=model)
    try:
        response = await chat.send_message(text)
        result = (response.text or "").strip()
        pyperclip.copy(result)
        print("[Clipboard] Updated with gem response.", flush=True)
        gem_name = getattr(gem, "name", None) or "(unknown gem)"
        show_notification("RenaClip", f"Clipboard updated by {gem_name}.")
    except Exception as e:
        err_msg = f"[Gemini clipboard error] {e}"
        pyperclip.copy(err_msg)
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "Gemini clipboard processing failed. See console for details.")
    finally:
        if client is not None:
            await _delete_chat_after(client, chat)


def on_hotkey(loop, client, gem, index: int, model: str = "unspecified"):
    """Called from keyboard thread: read clipboard and schedule async work on main loop."""
    if client is None:
        print("[Warning] Client is None, cannot process hotkey.", flush=True)
        show_notification("RenaClip", "Client is not initialized. Cannot process hotkey.")
        return
    gem_name = getattr(gem, "name", None) or "(unknown gem)"
    try:
        text = pyperclip.paste() or ""
    except Exception as e:
        print(f"[Clipboard read error] {e}", file=sys.stderr, flush=True)
        show_notification("RenaClip", "Clipboard read failed.")
        return
    print(
        f"[Hotkey {index + 1}] Triggered. Clipboard length={len(text.strip())}",
        flush=True,
    )
    if not text.strip():
        print(f"[Hotkey {index + 1}] Clipboard is empty, skipped.", flush=True)
        show_notification("RenaClip", "Clipboard is empty, skipped.")
        return
    show_notification("RenaClip", f"Processing with {gem_name}...")
    asyncio.run_coroutine_threadsafe(process_clipboard_with_gem(client, gem, text, model), loop)


async def main_async(arg_gems: list[str] | None):
    # Apply config (env + hotkey) before get_client
    try:
        from config_loader import load_config, VALID_MODIFIERS, AVAILABLE_MODELS
        _, cfg_settings = load_config()
        
        GEMINI_1PSID = None
        GEMINI_1PSIDTS = None
        SOCKS5_PROXY = None
        COOKIE_BROWSER = None
        
        # Apply relevant settings to environment
        for k in ("GEMINI_1PSID", "GEMINI_1PSIDTS", "SOCKS5_PROXY", "COOKIE_BROWSER"):
            v = (cfg_settings.get(k).strip() or None)
            if v:
                GEMINI_1PSID = v if k == "GEMINI_1PSID" else GEMINI_1PSID
                GEMINI_1PSIDTS = v if k == "GEMINI_1PSIDTS" else GEMINI_1PSIDTS
                SOCKS5_PROXY = v if k == "SOCKS5_PROXY" else SOCKS5_PROXY
                COOKIE_BROWSER = v if k == "COOKIE_BROWSER" else COOKIE_BROWSER
                
    except ImportError:
        cfg_settings = {}
        VALID_MODIFIERS = ("ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt")
        AVAILABLE_MODELS = ("unspecified", "gemini-3.0-pro", "gemini-3.0-flash", "gemini-3.0-flash-thinking")
    
    mod = (cfg_settings.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
    try:
        from config_loader import VALID_MODIFIERS
        if mod not in VALID_MODIFIERS:
            mod = "ctrl"
    except ImportError:
        mod = "ctrl"
    
    model = (cfg_settings.get("MODEL") or "unspecified").strip()
    try:
        from config_loader import AVAILABLE_MODELS
        if model not in AVAILABLE_MODELS:
            model = "unspecified"
    except ImportError:
        model = "unspecified"

    client = get_client(proxy=SOCKS5_PROXY, psid=GEMINI_1PSID, psidts=GEMINI_1PSIDTS, cookie_browser=COOKIE_BROWSER)
    if client is None:
        print("[Error] Failed to initialize Gemini client. Client is None.", file=sys.stderr, flush=True)
        show_notification("RenaClip", "Failed to initialize Gemini client. Check your configuration.")
    else:
        try:
            await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
        except Exception as e:
            client = None
            print(f"[Error] Failed to initialize Gemini client. {e}", file=sys.stderr, flush=True)
            show_notification("RenaClip", "Failed to initialize Gemini client. Check your configuration.")

    specs = get_gem_specs(arg_gems)
    if not specs:
        print("Error: at least one gem required (use GEM_LIST or --gem NAME_OR_ID ...).", file=sys.stderr)
        raise SystemExit(1)

    if not arg_gems:
        if client is not None:
            config_display_names = [s["name"] for s in specs]
            await delete_renaclip_gems_not_in_config(client, config_display_names)
        else:
            print("[Warning] Client is None, skipping delete_renaclip_gems_not_in_config.", flush=True)
    gems: list = []
    for i, spec in enumerate(specs):
        if client is None:
            print(f"[Warning] Client is None, cannot process gem {i+1}.", flush=True)
            continue
        name = spec["name"]
        api_name = (RENACLIP_PREFIX + name) if "prompt" in spec else name
        if "prompt" in spec:
            if client is None:
                print(f"[Warning] Client is None, skipping get_or_create_gem for {name}.", flush=True)
                continue
            gem, created = await get_or_create_gem(
                client,
                name=api_name,
                prompt=spec["prompt"],
                description=spec.get("description", ""),
            )
            if client is None:
                print(f"[Warning] Client is None, skipping update_gem for {name}.", flush=True)
            else:
                gem = await update_gem(client, gem, name=api_name, prompt=spec["prompt"], description=spec.get("description", ""))
            print(f"  [RenaClip] {mod}+{i + 1}: {name!r} — {spec.get('description', '') or '(no description)'} (created={created})", flush=True)
        else:
            if client is None:
                print(f"[Warning] Client is None, skipping ensure_gem_exists for {name}.", flush=True)
                continue
            try:
                gem = await ensure_gem_exists(client, api_name)
            except GemNotFoundError as e:
                print(f"Error: {e}. Gem {name!r} not found.", file=sys.stderr)
                raise SystemExit(1) from e
            print(f"  {mod}+{i + 1}: {gem.name!r}", flush=True)
        gems.append(gem)

    loop = asyncio.get_running_loop()
    try:
        import keyboard
    except ImportError:
        print("Install 'keyboard' for global hotkey: pip install keyboard", file=sys.stderr)
        raise SystemExit(1)

    stop_event = asyncio.Event()
    tray_icon = None

    if PYSTRAY_AVAILABLE and pystray is not None:
        try:
            tray_image = _load_tray_icon(64)

            def on_open_ui(icon, item):
                _launch_renaclip_ui()

            def on_tray_exit(icon, item):
                loop.call_soon_threadsafe(stop_event.set)
                icon.stop()

            menu = pystray.Menu(
                pystray.MenuItem("Open UI", on_open_ui, default=True),
                pystray.MenuItem("Exit", on_tray_exit),
            )
            tray_icon = pystray.Icon("renaclip", tray_image, "RenaClip", menu)
            tray_thread = threading.Thread(target=_run_tray_icon, args=(tray_icon,), daemon=True)
            tray_thread.start()
        except Exception as e:
            print(f"[Tray] Failed to create tray icon: {e}", file=sys.stderr, flush=True)
    
    if model != "unspecified":
        print(f"Using model: {model}", flush=True)
    
    for i, g in enumerate(gems):
        key = f"{mod}+{i + 1}"
        keyboard.add_hotkey(key, lambda g=g, i=i, m=model: on_hotkey(loop, client, g, i, m))
    print(f"Listening: {mod}+1..{mod}+{len(gems)} = clipboard->gem->clipboard. Exit via tray menu.", flush=True)
    try:
        await stop_event.wait()
    finally:
        if tray_icon is not None:
            try:
                tray_icon.stop()
            except Exception:
                pass
        keyboard.unhook_all()
        if client is not None:
            await client.close()
        else:
            print("[Warning] Client is None, skipping client.close().", flush=True)
        print("Exited.", flush=True)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Clipboard -> Gem -> clipboard on Ctrl+1/2/3...")
    parser.add_argument(
        "--gem",
        type=str,
        action="append",
        dest="gems",
        metavar="NAME_OR_ID",
        help="Gem name or id; repeat for multiple (Ctrl+1=first, Ctrl+2=second, ...)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.gems))


if __name__ == "__main__":
    main()
