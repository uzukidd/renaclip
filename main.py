"""
Deprecated: Use renaclip.py instead.
  python renaclip.py --service               # Run clipboard service
  python renaclip.py --service --gem X --gem Y  # With explicit gems
"""

import asyncio
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
from constants import ASSETS_DIR, CONFIG_PATH, TRAY_ICON_PATH, UI_LOCK_PATH
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
    openai_model: str
    hotkey_modifier: str
    backend: str
    gemini_gems: list   # Gemini API gem objects (requires client init)
    openai_gems: list   # simple objects with .name / .prompt from config specs
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
        openai_model = _str(settings.get("OPENAI_MODEL"), "gpt-4o")

        if use_browser_cookie:
            gemini_1psid = None
            gemini_1psidts = None

        return cls(
            model=model,
            openai_model=openai_model,
            hotkey_modifier=mod,
            backend=settings.get("BACKEND", "gemini"),
            gemini_gems=[],
            openai_gems=[],
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
    try:
        if TRAY_ICON_PATH.is_file():
            img = Image.open(str(TRAY_ICON_PATH)).convert("RGBA")
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
    launch_ui(lock_path=UI_LOCK_PATH)

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



async def process_clipboard_gemini(client, gemini_gem, text: str, model: str = "unspecified") -> None:
    """Gemini backend: send text to an actual Gemini gem, put response back to clipboard."""
    if client is None:
        print("[Warning] Gemini client is None, cannot process clipboard.", flush=True)
        show_notification("RenaClip", "Gemini client is not initialized.")
        return
    gem_name = getattr(gemini_gem, "name", None) or "(unknown gem)"
    chat = client.start_chat(gem=gemini_gem, model=model)
    try:
        response = await chat.send_message(text)
        result = (response.text or "").strip()
        pyperclip.copy(result)
        print(f"[Gemini] Clipboard updated by {gem_name}.", flush=True)
        show_notification("RenaClip", f"Clipboard updated by {gem_name}.")
    except Exception as e:
        err_msg = f"[Gemini error] {e}"
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "Gemini processing failed.")
    finally:
        if client is not None:
            await delete_chat_after(client, chat)


async def process_clipboard_openai(openai_gem, text: str, model: str = "gpt-4o") -> None:
    """OpenAI backend: send text + prompt to OpenAI-compatible API, put response back."""
    gem_name = getattr(openai_gem, "name", None) or "(unknown gem)"
    prompt = getattr(openai_gem, "prompt", "")
    # TODO: call OpenAI API with real request
    result = f"[OpenAI] {gem_name}"
    pyperclip.copy(result)
    print(f"[OpenAI] Placeholder — gem: {gem_name}, model: {model}", flush=True)
    show_notification("RenaClip", f"OpenAI: {gem_name} (placeholder).")


def on_hotkey(loop, app: RenaClipApp, client, index: int):
    """Called from keyboard thread: read clipboard, dispatch by backend to correct gem."""
    backend = app.backend
    print(f"[Hotkey {index + 1}] backend={backend}", flush=True)

    try:
        text = pyperclip.paste() or ""
    except Exception as e:
        print(f"[Clipboard read error] {e}", file=sys.stderr, flush=True)
        show_notification("RenaClip", "Clipboard read failed.")
        return

    if not text.strip():
        print(f"[Hotkey {index + 1}] Clipboard is empty, skipped.", flush=True)
        show_notification("RenaClip", "Clipboard is empty, skipped.")
        return

    if backend == "openai":
        if index >= len(app.openai_gems):
            show_notification("RenaClip", "No OpenAI gem at this slot.")
            return
        gem = app.openai_gems[index]
        model = app.openai_model
        show_notification("RenaClip", f"Processing with {gem.name} (OpenAI)...")
        asyncio.run_coroutine_threadsafe(process_clipboard_openai(gem, text, model), loop)
    else:
        if client is None:
            show_notification("RenaClip", "Gemini client is not initialized.")
            return
        if index >= len(app.gemini_gems):
            show_notification("RenaClip", "No Gemini gem at this slot.")
            return
        gem = app.gemini_gems[index]
        model = app.model
        show_notification("RenaClip", f"Processing with {gem.name} (Gemini)...")
        asyncio.run_coroutine_threadsafe(process_clipboard_gemini(client, gem, text, model), loop)

# multiprocessing.freeze_support()
async def main_async(arg_gems: Optional[list[str]] = None):
    print("trying to load config from", CONFIG_PATH)
    app = RenaClipApp.load_config(CONFIG_PATH, arg_gems)
    global ui_process
    ui_process = None

    if app.backend == "openai":
        client = None
        print("[Info] Backend is OpenAI — skipping Gemini client init.", flush=True)
    else:
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
        print("[Warning] Gemini Client is None, skipping delete_renaclip_gems_not_in_config.", flush=True)

    # --- Build both gem lists: gemini_gems (API objects) + openai_gems (simple objects) ---
    for spec in app.specs:
        # OpenAI gem: always just a simple object with .name and .prompt
        app.openai_gems.append(type("OpenAIGem", (), {"name": spec["name"], "prompt": spec.get("prompt", "")})())

        # Gemini gem: only if client is available
        if client is not None:
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
                print(f"  [RenaClip] {app.hotkey_modifier}+{len(app.gemini_gems) + 1}: {name!r} (created={created})", flush=True)
            else:
                try:
                    gem = await ensure_gem_exists(client, api_name)
                except GemNotFoundError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    raise SystemExit(1) from e
                print(f"  {app.hotkey_modifier}+{len(app.gemini_gems) + 1}: {gem.name!r}", flush=True)
            app.gemini_gems.append(gem)
        else:
            print(f"  [OpenAI] {app.hotkey_modifier}+{len(app.openai_gems)}: {spec['name']!r}", flush=True)

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

    print(f"Backend: {app.backend}", flush=True)
    if app.backend == "openai":
        print(f"Using OpenAI model: {app.openai_model}", flush=True)
    elif app.model != "unspecified":
        print(f"Using Gemini model: {app.model}", flush=True)

    for i in range(len(app.specs)):
        key = f"{app.hotkey_modifier}+{i + 1}"
        keyboard.add_hotkey(key, lambda i=i: on_hotkey(loop, app, client, i))
    print(f"Listening: {app.hotkey_modifier}+1..{app.hotkey_modifier}+{len(app.specs)} = clipboard->gem->clipboard. Exit via tray menu.", flush=True)

    # --- Config file watcher: reload settings live when gem_config.json changes ---
    async def watch_config():
        last_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        while not stop_event.is_set():
            await asyncio.sleep(2)
            try:
                if not CONFIG_PATH.exists():
                    continue
                mtime = CONFIG_PATH.stat().st_mtime
                if mtime == last_mtime:
                    continue
                last_mtime = mtime

                gems2, settings2 = load_gem_config()
                new_backend = (settings2.get("BACKEND") or "gemini").strip().lower()
                old_backend = app.backend

                # Update app fields in-place (shared with hotkey callbacks)
                app.backend = new_backend
                app.hotkey_modifier = (settings2.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
                app.model = (settings2.get("MODEL") or "unspecified").strip()
                app.openai_model = (settings2.get("OPENAI_MODEL") or "gpt-4o").strip()
                app.socks5_proxy = (settings2.get("SOCKS5_PROXY") or "").strip() or None

                # Update specs if gems changed
                app.specs = gems2

                print(f"[Config] Reloaded — backend={app.backend}", flush=True)

                if old_backend != "gemini" and new_backend == "gemini":
                    show_notification("RenaClip", "Backend switched to Gemini. Please restart the service for full effect.")
            except Exception as e:
                print(f"[Config watcher] {e}", file=sys.stderr, flush=True)

    asyncio.create_task(watch_config())

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
            print("[Warning] Gemini Client is None, skipping client.close().", flush=True)
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
