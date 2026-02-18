"""
Rena Clip - Single script: UI + clipboard service.
Usage:
  python renaclip.py          # Launch UI
  python renaclip.py --service  # Run clipboard service (used when UI clicks Start)
  python renaclip.py --service --gem "X" --gem "Y"  # Service with explicit gems
"""

import asyncio
import atexit
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "gem_config.json"
VALID_MODIFIERS = ("ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt")
DEFAULT_GEMS = [
    {"name": "English to Chinese Translator", "description": "Translates English text to Chinese (Simplified).", "prompt": "You are a professional translator. Reply with only the Chinese translation."},
]
DEFAULT_SETTINGS = {"GEMINI_1PSID": "", "GEMINI_1PSIDTS": "", "SOCKS5_PROXY": "", "HOTKEY_MODIFIER": "ctrl"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> tuple[list[dict], dict]:
    gems, settings = [], dict(DEFAULT_SETTINGS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                gems = data.get("gems", gems)
                settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        except (json.JSONDecodeError, KeyError):
            pass
    if not gems:
        gems = list(DEFAULT_GEMS)
    return gems, settings


def save_config(gems: list[dict], settings: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"gems": gems, "settings": settings}, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Service: clipboard + hotkey (runs as subprocess or standalone)
# ---------------------------------------------------------------------------

def _run_service(arg_gems: list[str] | None) -> None:
    """Clipboard service logic. Imports gemini, keyboard, pyperclip.
    GEMINI_1PSID/PSIDTS/PROXY come from env (injected at subprocess spawn or by main() for direct run).
    """
    import pyperclip
    from gemini_client import GemNotFoundError, _delete_chat_after, ensure_gem_exists, get_client, get_or_create_gem

    gems_data, settings = load_config()
    mod = (settings.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
    if mod not in VALID_MODIFIERS:
        mod = "ctrl"

    specs = [{"name": s.strip() for s in arg_gems}] if arg_gems else gems_data
    if not specs:
        print("Error: at least one gem required.", file=sys.stderr)
        raise SystemExit(1)

    async def _main():
        client = get_client()
        await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
        gems: list = []
        for i, spec in enumerate(specs):
            name = spec["name"]
            if "prompt" in spec:
                g, created = await get_or_create_gem(client, name=name, prompt=spec["prompt"], description=spec.get("description", ""))
                print(f"  {mod}+{i + 1}: {g.name!r} (created={created})", flush=True)
            else:
                try:
                    g = await ensure_gem_exists(client, name)
                except GemNotFoundError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    raise SystemExit(1) from e
                print(f"  {mod}+{i + 1}: {g.name!r}", flush=True)
            gems.append(g)

        try:
            import keyboard
        except ImportError:
            print("Install 'keyboard': pip install keyboard", file=sys.stderr)
            raise SystemExit(1)

        loop = asyncio.get_running_loop()
        stop_ev = asyncio.Event()

        async def process_clip(g, text: str):
            if not text or not text.strip():
                return
            chat = client.start_chat(gem=g)
            try:
                resp = await chat.send_message(text)
                pyperclip.copy((resp.text or "").strip())
                print("[Clipboard] Updated.", flush=True)
            except Exception as e:
                pyperclip.copy(f"[Error] {e}")
                print(e, file=sys.stderr, flush=True)
            finally:
                await _delete_chat_after(client, chat)

        def on_key(g, i):
            try:
                t = pyperclip.paste() or ""
            except Exception as ex:
                print(f"[Clipboard read error] {ex}", file=sys.stderr)
                return
            if not t.strip():
                return
            asyncio.run_coroutine_threadsafe(process_clip(g, t), loop)

        for i, g in enumerate(gems):
            keyboard.add_hotkey(f"{mod}+{i + 1}", lambda g=g, i=i: on_key(g, i))
        keyboard.add_hotkey(f"{mod}+q", lambda: loop.call_soon_threadsafe(stop_ev.set))
        print(f"Listening: {mod}+1..{mod}+{len(gems)}, {mod}+q = exit.", flush=True)
        try:
            await stop_ev.wait()
        finally:
            keyboard.unhook_all()
            await client.close()

    asyncio.run(_main())


def start_service(extra_args: list[str] | None = None) -> subprocess.Popen | None:
    """Spawn service as subprocess. Injects config as temp env vars. Runs without console for reliable kill."""
    args = [sys.executable, str(__file__), "--service"]
    if extra_args:
        args.extend(extra_args)
    _, settings = load_config()
    env = dict(os.environ)
    for k in ("GEMINI_1PSID", "GEMINI_1PSIDTS", "SOCKS5_PROXY"):
        v = (settings.get(k) or "").strip()
        if v:
            env[k] = v
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.Popen(
            args,
            cwd=SCRIPT_DIR,
            env=env,
            creationflags=creationflags,
        )
    except Exception:
        return None


def stop_service(proc: subprocess.Popen | None) -> bool:
    if proc is None:
        return False
    if proc.poll() is not None:
        return True
    try:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            for c in p.children(recursive=True):
                try:
                    c.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            p.kill()
            p.wait(timeout=5)
        except ImportError:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
            else:
                proc.terminate()
                proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
    return True


def is_service_running(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


# ---------------------------------------------------------------------------
# UI (Flet)
# ---------------------------------------------------------------------------

def _ui_main(page):
    import flet as ft

    page.title = "Rena Clip"
    page.padding = 24
    page.spacing = 16
    gems, settings = load_config()
    gem_list_ref = ft.Ref[ft.Column]()
    clip_proc_ref = ft.Ref[object]()

    def update_status():
        st, sb, eb = status_ref.current, start_btn.current, stop_btn.current
        running = is_service_running(clip_proc_ref.current)
        try:
            page.window.progress_bar = 1.0 if running else None
            page.window.badge_label = "●" if running else None
        except Exception:
            pass
        if st:
            st.value = "Running" if running else "Stopped"
            st.color = ft.Colors.GREEN if running else ft.Colors.GREY_600
        if sb:
            sb.disabled = running
        if eb:
            eb.disabled = not running
        try:
            if st:
                st.update()
            if sb:
                sb.update()
            if eb:
                eb.update()
            page.update()
        except Exception:
            pass

    def on_start(e):
        if not gems:
            page.snack_bar = ft.SnackBar(content=ft.Text("Add at least one gem first."))
            page.snack_bar.open = True
            page.update()
            return
        p = start_service()
        clip_proc_ref.current = p
        if p is None:
            page.snack_bar = ft.SnackBar(content=ft.Text("Failed to start service."))
            page.snack_bar.open = True
        page.update()
        update_status()

    def on_stop(e):
        stop_service(clip_proc_ref.current)
        clip_proc_ref.current = None
        update_status()

    status_ref = ft.Ref[ft.Text]()
    start_btn = ft.Ref[ft.ElevatedButton]()
    stop_btn = ft.Ref[ft.ElevatedButton]()

    def cleanup():
        p = clip_proc_ref.current
        if p is not None:
            stop_service(p)
    atexit.register(cleanup)

    def rebuild_gems():
        col = gem_list_ref.current
        if not col:
            return
        col.controls.clear()
        if not gems:
            col.controls.append(ft.Text("No gems yet. Click 'Add Gem' to create one.", color=ft.Colors.GREY_600))
        else:
            for i, g in enumerate(gems):
                idx = i

                def mk_edit(j):
                    return lambda e: open_edit(j)

                def mk_del(j):
                    return lambda e: (gems.pop(j), save_config(gems, settings), rebuild_gems())

                col.controls.append(
                    ft.Card(
                        content=ft.Container(
                            content=ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(g.get("name", "Unnamed"), weight=ft.FontWeight.W_600, size=16),
                                            ft.Text((g.get("description", "") or "")[:80] + ("..." if len(g.get("description", "")) > 80 else ""), size=12, color=ft.Colors.GREY_700),
                                        ],
                                        expand=True,
                                        alignment=ft.MainAxisAlignment.START,
                                    ),
                                    ft.Row([ft.OutlinedButton("Edit", on_click=mk_edit(idx)), ft.OutlinedButton("Delete", on_click=mk_del(idx))]),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            padding=16,
                            on_click=mk_edit(idx),
                        )
                    )
                )
        col.update()

    def open_edit(index: int | None):
        g = gems[index] if index is not None else {"name": "", "description": "", "prompt": ""}
        nf = ft.TextField(label="Title", value=g.get("name", ""), width=400)
        df = ft.TextField(label="Description", value=g.get("description", ""), width=400, multiline=True, min_lines=2)
        pf = ft.TextField(label="Prompt", value=g.get("prompt", ""), width=400, multiline=True, min_lines=4)

        def close():
            page.pop_dialog()

        def save(idx, _nf, _df, _pf):
            def _save(e):
                item = {"name": _nf.value or "Unnamed", "description": _df.value or "", "prompt": _pf.value or ""}
                if idx is not None:
                    gems[idx] = item
                else:
                    gems.append(item)
                save_config(gems, settings)
                rebuild_gems()
                close()
            return _save

        d = ft.AlertDialog(
            title=ft.Text("Edit Gem" if index is not None else "Add Gem"),
            content=ft.Column(
                [nf, df, pf, ft.Row([ft.OutlinedButton("Cancel", on_click=lambda e: close()), ft.FilledButton("Save", on_click=save(index, nf, df, pf))], alignment=ft.MainAxisAlignment.END)],
                tight=True,
                spacing=12,
            ),
            modal=True,
            scrollable=True,
        )
        page.show_dialog(d)

    def open_settings(e=None):
        pf = ft.TextField(label="GEMINI_1PSID", value=settings.get("GEMINI_1PSID", ""), width=450, password=True, can_reveal_password=True)
        pt = ft.TextField(label="GEMINI_1PSIDTS", value=settings.get("GEMINI_1PSIDTS", ""), width=450, password=True, can_reveal_password=True)
        px = ft.TextField(label="Proxy (SOCKS5_PROXY)", value=settings.get("SOCKS5_PROXY", ""), width=450, hint_text="e.g. socks5://127.0.0.1:8889")
        dd = ft.Dropdown(label="Hotkey modifier", value=settings.get("HOTKEY_MODIFIER", "ctrl"), width=450, options=[ft.dropdown.Option(m) for m in VALID_MODIFIERS])

        def close():
            page.pop_dialog()

        def save_set(e):
            settings["GEMINI_1PSID"] = (pf.value or "").strip()
            settings["GEMINI_1PSIDTS"] = (pt.value or "").strip()
            settings["SOCKS5_PROXY"] = (px.value or "").strip()
            m = (dd.value or "ctrl").strip().lower()
            settings["HOTKEY_MODIFIER"] = m if m in VALID_MODIFIERS else "ctrl"
            save_config(gems, settings)
            close()

        page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Settings"),
                content=ft.Column(
                    [pf, pt, px, dd, ft.Text("modifier+1/2/3... for gems, modifier+q to exit", size=11, color=ft.Colors.GREY_600), ft.Row([ft.OutlinedButton("Cancel", on_click=lambda e: close()), ft.FilledButton("Save", on_click=save_set)], alignment=ft.MainAxisAlignment.END)],
                    tight=True,
                    spacing=12,
                ),
                modal=True,
            )
        )

    page.add(
        ft.Row([ft.Text("Rena Clip", size=24, weight=ft.FontWeight.BOLD), ft.Container(expand=True), ft.OutlinedButton("Settings", on_click=open_settings), ft.FilledButton("Add Gem", on_click=lambda e: open_edit(None))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Row(
            [
                ft.Text("Clipboard service:", size=14, color=ft.Colors.GREY_700),
                ft.Text("Stopped", ref=status_ref, size=14, color=ft.Colors.GREY_600),
                ft.Container(width=12),
                ft.ElevatedButton("Start", ref=start_btn, on_click=on_start),
                ft.OutlinedButton("Stop", ref=stop_btn, disabled=True, on_click=on_stop),
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=8,
        ),
        ft.Divider(),
        ft.Column(ref=gem_list_ref, spacing=8),
    )
    rebuild_gems()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if "--service" in args:
        # Direct run: inject config as temp env vars
        _, settings = load_config()
        for k in ("GEMINI_1PSID", "GEMINI_1PSIDTS", "SOCKS5_PROXY"):
            v = (settings.get(k) or "").strip()
            if v:
                os.environ[k] = v
        idx = args.index("--service")
        rest = args[idx + 1 :]
        gems_args = []
        i = 0
        while i < len(rest):
            if rest[i] == "--gem" and i + 1 < len(rest):
                gems_args.append(rest[i + 1])
                i += 2
            else:
                i += 1
        _run_service(gems_args if gems_args else None)
    else:
        import flet as ft
        ft.app(target=_ui_main)


if __name__ == "__main__":
    main()
