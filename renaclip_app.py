"""
Deprecated: Use renaclip.py instead.
  python renaclip.py --service               # Run clipboard service
  python renaclip.py --service --gem X --gem Y  # With explicit gems
"""

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.argv = [str(Path(__file__).resolve().parent / "renaclip.py"), "--service"] + sys.argv[1:]
    from renaclip import main
    main()

import asyncio
import os
import sys

import pyperclip

from gemini_client import (
    GemNotFoundError,
    _delete_chat_after,
    ensure_gem_exists,
    get_client,
    get_or_create_gem,
)

# ---------------------------------------------------------------------------
# Default gem list: name, description, prompt (作用). Used when no --gem is passed; gems are created if missing.
# Each item: "name" = display name, "description" = short description, "prompt" = system instruction (作用).
# ---------------------------------------------------------------------------
GEM_LIST = [
    {
        "name": "English to Chinese Translator",
        "description": "Translates English text to Chinese (Simplified).",
        "prompt": (
            "You are a professional translator. Your only task is to translate text from English to Chinese. "
            "Reply with only the Chinese translation, no explanations or extra text. "
            "Keep the tone and style of the original; use natural, fluent Chinese."
        ),
    },
]


def get_gem_specs(arg_gems: list[str] | None) -> list[dict]:
    """
    Return list of gem specs. Each spec is either:
    - from --gem: {"name": name_or_id} -> ensure_gem_exists (must exist).
    - from gem_config.json gems; else GEM_LIST.
    """
    if arg_gems:
        return [{"name": s.strip()} for s in arg_gems if s and s.strip()]
    env = (os.environ.get("GEMINI_CLIPBOARD_GEM") or "").strip()
    if env:
        return [{"name": env}]
    try:
        from config_loader import load_config
        cfg_gems, _ = load_config()
        if cfg_gems:
            return cfg_gems
    except ImportError:
        pass
    return list(GEM_LIST)


async def process_clipboard_with_gem(client, gem, text: str) -> None:
    """Send text to gem, put response (or error) back to clipboard, then delete chat."""
    if not text or not text.strip():
        return
    chat = client.start_chat(gem=gem)
    try:
        response = await chat.send_message(text)
        result = (response.text or "").strip()
        pyperclip.copy(result)
        print("[Clipboard] Updated with gem response.", flush=True)
    except Exception as e:
        err_msg = f"[Gemini clipboard error] {e}"
        pyperclip.copy(err_msg)
        print(err_msg, file=sys.stderr, flush=True)
    finally:
        await _delete_chat_after(client, chat)


def on_hotkey(loop, client, gem, index: int):
    """Called from keyboard thread: read clipboard and schedule async work on main loop."""
    try:
        text = pyperclip.paste() or ""
    except Exception as e:
        print(f"[Clipboard read error] {e}", file=sys.stderr, flush=True)
        return
    if not text.strip():
        print(f"[Hotkey {index + 1}] Clipboard is empty, skipped.", flush=True)
        return
    asyncio.run_coroutine_threadsafe(process_clipboard_with_gem(client, gem, text), loop)


async def main_async(arg_gems: list[str] | None):
    # Apply config (env + hotkey) before get_client
    try:
        from config_loader import load_config, VALID_MODIFIERS
        _, cfg_settings = load_config()
        for k in ("GEMINI_1PSID", "GEMINI_1PSIDTS", "SOCKS5_PROXY"):
            v = (cfg_settings.get(k) or "").strip()
            if v:
                os.environ[k] = v
    except ImportError:
        cfg_settings = {}
    mod = (cfg_settings.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
    try:
        from config_loader import VALID_MODIFIERS
        if mod not in VALID_MODIFIERS:
            mod = "ctrl"
    except ImportError:
        mod = "ctrl"

    client = get_client()
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)

    specs = get_gem_specs(arg_gems)
    if not specs:
        print("Error: at least one gem required (use GEM_LIST or --gem NAME_OR_ID ...).", file=sys.stderr)
        raise SystemExit(1)

    gems: list = []
    for i, spec in enumerate(specs):
        name = spec["name"]
        if "prompt" in spec:
            gem, created = await get_or_create_gem(
                client,
                name=name,
                prompt=spec["prompt"],
                description=spec.get("description", ""),
            )
            print(f"  {mod}+{i + 1}: {gem.name!r} — {spec.get('description', '') or '(no description)'} (created={created})", flush=True)
        else:
            try:
                gem = await ensure_gem_exists(client, name)
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

    def on_exit_hotkey():
        loop.call_soon_threadsafe(stop_event.set)

    for i, g in enumerate(gems):
        key = f"{mod}+{i + 1}"
        keyboard.add_hotkey(key, lambda g=g, i=i: on_hotkey(loop, client, g, i))
    keyboard.add_hotkey(f"{mod}+q", on_exit_hotkey)
    print(f"Listening: {mod}+1..{mod}+{len(gems)} = clipboard->gem->clipboard, {mod}+q = exit.", flush=True)
    try:
        await stop_event.wait()
    finally:
        keyboard.unhook_all()
        await client.close()
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
