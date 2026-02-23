"""
Deprecated: Use renaclip.py instead.
  python renaclip.py --service               # Run clipboard service
  python renaclip.py --service --gem X --gem Y  # With explicit gems
"""

import asyncio
import os
import sys
import threading
import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import pyperclip

from config_loader import (
    AVAILABLE_MODELS,
    VALID_MODIFIERS,
    load_config as load_gem_config,
)
from interfaces import launch_ui
from gemini_client import (
    RENACLIP_PREFIX,
    GemNotFoundError,
    delete_chat_after,
    delete_renaclip_gems_not_in_config,
    ensure_gem_exists,
    get_client,
    get_or_create_gem,
    update_gem,
)

from typing import Optional

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


@dataclass
class RenaClipApp:
    """Holds model, hotkey modifier, gem specs, gem instances, and client-related config."""

    model: str
    hotkey_modifier: str
    gems: list
    specs: list[dict]
    gemini_1psid: str | None = None
    gemini_1psidts: str | None = None
    socks5_proxy: str | None = None
    cookie_browser: str | None = None
    use_browser_cookie: bool = False

    @classmethod
    def load_config(cls, path: str | Path, arg_gems: list[str] | None = None) -> "RenaClipApp":
        """
        Load config from a JSON file at the given path.
        Returns RenaClipApp with specs and settings filled; gems list is empty (filled later).
        If arg_gems is provided, specs are built from it; otherwise from config file gems.
        """
        path = Path(path)  # kept for API compatibility; config read via config_loader
        gems, settings = load_gem_config()
        if arg_gems:
            specs = [{"name": s.strip()} for s in arg_gems if s and s.strip()]
        else:
            specs = gems

        def _str(v, default: str = "") -> str:
            return (v or "").strip() or default

        gemini_1psid = _str(settings.get("GEMINI_1PSID")) or None
        gemini_1psidts = _str(settings.get("GEMINI_1PSIDTS")) or None
        socks5_proxy = _str(settings.get("SOCKS5_PROXY")) or None
        cookie_browser = _str(settings.get("COOKIE_BROWSER")) or None
        use_browser_cookie = settings.get("USE_BROWSER_COOKIE", False)

        mod = _str(settings.get("HOTKEY_MODIFIER"), "ctrl").lower()
        if mod not in VALID_MODIFIERS:
            mod = "ctrl"

        model = _str(settings.get("MODEL"), "unspecified")
        if model not in AVAILABLE_MODELS:
            model = "unspecified"

        if use_browser_cookie:
            gemini_1psid = None
            gemini_1psidts = None

        return cls(
            model=model,
            hotkey_modifier=mod,
            gems=[],
            specs=specs,
            gemini_1psid=gemini_1psid,
            gemini_1psidts=gemini_1psidts,
            socks5_proxy=socks5_proxy,
            cookie_browser=cookie_browser,
            use_browser_cookie=use_browser_cookie,
        )


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


def _launch_renaclip_ui():
    """Launch RenaClip UI via interfaces. Skip if UI is already running."""
    app_dir = Path(__file__).resolve().parent
    launch_ui(lock_path=app_dir / ".renaclip_ui.lock")

async def initialize_client(app: RenaClipApp):
    client = get_client(
        proxy=app.socks5_proxy,
        psid=app.gemini_1psid,
        psidts=app.gemini_1psidts,
        cookie_browser=app.cookie_browser,
    )
    if client is None:
        raise Exception("Client is None.")
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
    
    return client
    

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
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "Gemini clipboard processing failed. See console for details.")
    finally:
        if client is not None:
            await delete_chat_after(client, chat)


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

# multiprocessing.freeze_support()
async def main_async(arg_gems: Optional[list[str]] = None):
    config_path = Path(__file__).resolve().parent / "gem_config.json"
    app = RenaClipApp.load_config(config_path, arg_gems)
    global ui_process
    ui_process = None
    try:
        client = await initialize_client(app)
    except Exception as e:
        client = None
        print(f"[Error] Failed to initialize Gemini client. {e}", file=sys.stderr, flush=True)
        show_notification("RenaClip", "Failed to initialize Gemini client. Check your configuration.")

    if not arg_gems and client is not None:
        config_display_names = [s["name"] for s in app.specs]
        await delete_renaclip_gems_not_in_config(client, config_display_names)
    elif not arg_gems and client is None:
        print("[Warning] Client is None, skipping delete_renaclip_gems_not_in_config.", flush=True)

    for i, spec in enumerate(app.specs):
        if client is None:
            print(f"[Warning] Client is None, cannot process gem {i+1}.", flush=True)
            continue
        name = spec["name"]
        api_name = (RENACLIP_PREFIX + name) if "prompt" in spec else name
        if "prompt" in spec:
            gem, created = await get_or_create_gem(
                client,
                name=api_name,
                prompt=spec["prompt"],
                description=spec.get("description", ""),
            )
            gem = await update_gem(client, gem, name=api_name, prompt=spec["prompt"], description=spec.get("description", ""))
            print(f"  [RenaClip] {app.hotkey_modifier}+{i + 1}: {name!r} — {spec.get('description', '') or '(no description)'} (created={created})", flush=True)
        else:
            try:
                gem = await ensure_gem_exists(client, api_name)
            except GemNotFoundError as e:
                print(f"Error: {e}. Gem {name!r} not found.", file=sys.stderr)
                raise SystemExit(1) from e
            print(f"  {app.hotkey_modifier}+{i + 1}: {gem.name!r}", flush=True)
        app.gems.append(gem)

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
                global ui_process
                if ui_process is not None and ui_process.is_alive():
                    return
                ui_process = multiprocessing.Process(target=_launch_renaclip_ui)
                ui_process.daemon = True
                ui_process.start()

            def on_tray_exit(icon, item):
                loop.call_soon_threadsafe(stop_event.set)

            menu = pystray.Menu(
                pystray.MenuItem("Open menu", on_open_ui, default=True),
                pystray.MenuItem("Exit", on_tray_exit),
            )
            tray_icon = pystray.Icon("renaclip", tray_image, "RenaClip", menu)
            tray_thread = threading.Thread(target=_run_tray_icon, args=(tray_icon,), daemon=True)
            tray_thread.start()
        except Exception as e:
            print(f"[Tray] Failed to create tray icon: {e}", file=sys.stderr, flush=True)

    if app.model != "unspecified":
        print(f"Using model: {app.model}", flush=True)

    for i, g in enumerate(app.gems):
        key = f"{app.hotkey_modifier}+{i + 1}"
        keyboard.add_hotkey(key, lambda g=g, i=i, m=app.model: on_hotkey(loop, client, g, i, m))
    print(f"Listening: {app.hotkey_modifier}+1..{app.hotkey_modifier}+{len(app.gems)} = clipboard->gem->clipboard. Exit via tray menu.", flush=True)
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

    # parser = argparse.ArgumentParser(description="Clipboard -> Gem -> clipboard on Ctrl+1/2/3...")
    # parser.add_argument(
    #     "--gem",
    #     type=str,
    #     action="append",
    #     dest="gems",
    #     metavar="NAME_OR_ID",
    #     help="Gem name or id; repeat for multiple (Ctrl+1=first, Ctrl+2=second, ...)",
    # )
    # args = parser.parse_args()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
