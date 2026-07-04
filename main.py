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
    openai_api_key: str
    openai_base_url: str
    openai_streaming: bool
    hotkey_modifier: str
    backend: str
    gemini_gems: list   # Gemini API gem objects (requires client init)
    openai_chats: list  # dicts {name, prompt} for each OpenAI gem
    specs: list[dict]
    gemini_psid: str | None = None
    gemini_psidts: str | None = None
    gemini_proxy: str | None = None
    gemini_cookie_browser: str | None = None
    gemini_use_browser_cookie: bool = False

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

        gemini_psid = _str(settings.get("GEMINI_PSID")) or None
        gemini_psidts = _str(settings.get("GEMINI_PSIDTS")) or None
        gemini_proxy = _str(settings.get("GEMINI_PROXY")) or None
        gemini_cookie_browser = _str(settings.get("GEMINI_COOKIE_BROWSER")) or None
        gemini_use_browser_cookie = settings.get("GEMINI_USE_BROWSER_COOKIE", False)

        mod = _str(settings.get("HOTKEY_MODIFIER"), "ctrl").lower()
        if mod not in VALID_MODIFIERS:
            mod = "ctrl"

        model = _str(settings.get("GEMINI_MODEL"), "unspecified")
        if model not in AVAILABLE_MODELS:
            model = "unspecified"
        openai_model = _str(settings.get("OPENAI_MODEL"), "gpt-4o")
        openai_api_key = _str(settings.get("OPENAI_API_KEY")) or None
        openai_base_url = _str(settings.get("OPENAI_BASE_URL")) or None
        openai_streaming = settings.get("OPENAI_STREAMING", False)

        if gemini_use_browser_cookie:
            gemini_psid = None
            gemini_psidts = None

        return cls(
            model=model,
            openai_model=openai_model,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            openai_streaming=openai_streaming,
            hotkey_modifier=mod,
            backend=settings.get("BACKEND", "gemini"),
            gemini_gems=[],
            openai_chats=[],
            specs=specs,
            gemini_psid=gemini_psid,
            gemini_psidts=gemini_psidts,
            gemini_proxy=gemini_proxy,
            gemini_cookie_browser=gemini_cookie_browser,
            gemini_use_browser_cookie=gemini_use_browser_cookie,
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
        proxy=app.gemini_proxy,
        psid=app.gemini_psid,
        psidts=app.gemini_psidts,
        cookie_browser=app.gemini_cookie_browser,
    )
    if client is None:
        raise Exception("Client is None.")
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
    
    return client
    

# (init_openai_client removed — OpenAI backend now creates a fresh client per request
#  and sends the gem prompt as a system message inline.)


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


async def process_clipboard_openai(app: RenaClipApp, chat_index: int, text: str) -> None:
    """OpenAI backend: create a fresh client each time, send system prompt + user text, put response back."""
    from openai_client import get_openai_client

    if chat_index >= len(app.openai_chats):
        show_notification("RenaClip", "No OpenAI gem at this slot.")
        return

    entry = app.openai_chats[chat_index]
    gem_name = entry["name"]
    prompt = entry["prompt"]
    model = app.openai_model

    client = get_openai_client(api_key=app.openai_api_key, base_url=app.openai_base_url or None)
    if client is None:
        show_notification("RenaClip", "OpenAI client could not be initialized.")
        return

    try:
        messages: list = []
        if prompt.strip():
            messages.append({"role": "system", "content": prompt.strip()})
        messages.append({"role": "user", "content": text})

        if app.openai_streaming:
            # Stream chunks and progressively update clipboard
            stream = await client.chat.completions.create(model=model, messages=messages, stream=True)
            accumulated = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    accumulated += delta
                    pyperclip.copy(accumulated)
            result = accumulated
            print(f"[OpenAI] Streamed & clipboard updated by {gem_name} ({len(result)} chars).", flush=True)
        else:
            resp = await client.chat.completions.create(model=model, messages=messages)
            result = (resp.choices[0].message.content or "").strip()
            pyperclip.copy(result)
            print(f"[OpenAI] Clipboard updated by {gem_name}.", flush=True)
        show_notification("RenaClip", f"Clipboard updated by {gem_name} (OpenAI).")
    except Exception as e:
        err_msg = f"[OpenAI error] {e}"
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "OpenAI processing failed.")
    finally:
        try:
            await client.close()
        except Exception:
            pass


def on_hotkey(loop, app: RenaClipApp, client, index: int):
    """Called from keyboard thread: read clipboard, dispatch by backend to correct gem."""
    backend = app.backend

    # Bounds-check against whichever gem list is active
    max_gems = len(app.openai_chats) if backend == "openai" else len(app.gemini_gems)
    if index < 0 or index >= max_gems:
        return

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
        gem_name = app.openai_chats[index]["name"]
        show_notification("RenaClip", f"Processing with {gem_name} (OpenAI)...")
        asyncio.run_coroutine_threadsafe(process_clipboard_openai(app, index, text), loop)
    else:
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
        print("[Info] Backend is OpenAI — building gem list, prompt sent per-request.", flush=True)
        for spec in app.specs:
            name = spec["name"]
            prompt = spec.get("prompt", "")
            app.openai_chats.append({"name": name, "prompt": prompt})
        print(f"[OpenAI] {len(app.openai_chats)} gem(s) ready.", flush=True)
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

    # --- Build both gem lists ---
    if client is not None:
        for spec in app.specs:
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

    print(f"Backend: {app.backend}, Gems: {len(app.specs)}", flush=True)
    if app.backend == "openai":
        print(f"[OpenAI] {len(app.openai_chats)} gem(s), model: {app.openai_model}", flush=True)
    elif app.model != "unspecified":
        print(f"[Gemini] {len(app.gemini_gems)} gem(s), model: {app.model}", flush=True)

    # Register hotkeys 1-9 unconditionally; on_hotkey checks if a gem exists at that slot
    MAX_HOTKEYS = 9
    for i in range(MAX_HOTKEYS):
        key = f"{app.hotkey_modifier}+{i + 1}"
        keyboard.add_hotkey(key, lambda i=i: on_hotkey(loop, app, client, i))
    print(f"Listening: {app.hotkey_modifier}+1..{app.hotkey_modifier}+{MAX_HOTKEYS}. {len(app.specs)} gem(s) active. Exit via tray menu.", flush=True)

    # --- Config file watcher: reload settings live when gem_config.json changes ---
    async def watch_config():
        nonlocal client
        last_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        previous_backend = app.backend
        previous_hotkey = app.hotkey_modifier
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

                # Keep app.specs and openai_chats in sync with config gems
                app.specs = gems2
                app.openai_chats.clear()
                for spec in gems2:
                    app.openai_chats.append({"name": spec["name"], "prompt": spec.get("prompt", "")})

                app.hotkey_modifier = (settings2.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
                app.model = (settings2.get("GEMINI_MODEL") or "unspecified").strip()
                app.openai_model = (settings2.get("OPENAI_MODEL") or "gpt-4o").strip()
                app.openai_api_key = (settings2.get("OPENAI_API_KEY") or "").strip() or None
                app.openai_base_url = (settings2.get("OPENAI_BASE_URL") or "").strip() or None
                app.openai_streaming = settings2.get("OPENAI_STREAMING", False)
                app.gemini_proxy = (settings2.get("GEMINI_PROXY") or "").strip() or None
                app.gemini_psid = (settings2.get("GEMINI_PSID") or "").strip() or None
                app.gemini_psidts = (settings2.get("GEMINI_PSIDTS") or "").strip() or None

                # Update hotkeys if modifier changed
                if app.hotkey_modifier != previous_hotkey:
                    previous_hotkey = app.hotkey_modifier
                    keyboard.unhook_all()
                    for i in range(MAX_HOTKEYS):
                        key = f"{app.hotkey_modifier}+{i + 1}"
                        keyboard.add_hotkey(key, lambda i=i: on_hotkey(loop, app, client, i))

                print(f"[Config] Reloaded — backend={new_backend}, gems={len(app.specs)}", flush=True)

                if previous_backend != new_backend:
                    app.backend = new_backend
                    previous_backend = new_backend
                    show_notification("RenaClip", f"Backend changed to {new_backend.capitalize()}. Please restart the service for full effect.")

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
