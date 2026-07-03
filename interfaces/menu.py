"""
RenaClip UI host. Run the settings/gem list window via launch_ui() (non-blocking).
"""

import os
import threading
from pathlib import Path

from config_loader import load_config, save_config, VALID_MODIFIERS, AVAILABLE_MODELS, VALID_BACKENDS
from constants import APP_ROOT, IS_DEV
from openai_client import fetch_available_models

SCRIPT_DIR = Path(__file__).resolve().parent
APP_NAME = "RenaClip"
UI_LOCK_PATH = SCRIPT_DIR / ".renaclip_ui.lock"


def _remove_ui_lock(lock_path: Path | None = None) -> None:
    p = lock_path or UI_LOCK_PATH
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# UI (Flet)
# ---------------------------------------------------------------------------

def _ui_main(page, *, lock_path: Path | None = None):
    import flet as ft

    env_prefix = "(Dev) " if IS_DEV else ""
    display_name = f"{env_prefix}{APP_NAME}"

    page.title = f"{display_name} - {APP_ROOT}"
    icon_path = APP_ROOT / "assets" / "renaclip_icon.ico"
    if not icon_path.is_file():
        icon_path = APP_ROOT / "assets" / "renaclip_icon.png"
    if icon_path.is_file():
        try:
            page.window.icon = str(icon_path.resolve())
        except Exception:
            pass
    page.padding = 24
    page.spacing = 16
    gems, settings = load_config()
    gem_list_ref = ft.Ref[ft.ReorderableListView]()
    clip_proc_ref = ft.Ref[object]()

    def on_reorder(e):
        old_idx = e.old_index
        new_idx = e.new_index
        if 0 <= old_idx < len(gems) and 0 <= new_idx < len(gems):
            item = gems.pop(old_idx)
            gems.insert(new_idx, item)
            save_config(gems, settings)
            rebuild_gems()

    def rebuild_gems():
        col = gem_list_ref.current
        if not col:
            return
        col.controls.clear()
        if not gems:
            col.controls.append(ft.Text("No gems yet. Click 'Add Gem' to create one.", color=ft.Colors.GREY_600))
        else:
            mod = (settings.get("HOTKEY_MODIFIER") or "ctrl").strip().lower()
            if mod not in VALID_MODIFIERS:
                mod = "ctrl"
            mod_label = "+".join(p.capitalize() for p in mod.split("+"))
            for i, g in enumerate(gems):
                idx = i
                shortcut = f"{mod_label}+{i + 1}"

                def mk_edit(j):
                    return lambda e: open_edit(j)

                def mk_del(j):
                    return lambda e: (gems.pop(j), save_config(gems, settings), rebuild_gems())

                gem_key = f"gem_{i}_{g.get('name', 'Unnamed')}"
                col.controls.append(
                    ft.Card(
                        key=gem_key,
                        content=ft.Container(
                            content=ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(g.get("name", "Unnamed"), weight=ft.FontWeight.W_600, size=16),
                                                    ft.Container(
                                                        content=ft.Text(shortcut, size=11, color=ft.Colors.WHITE),
                                                        bgcolor=ft.Colors.GREEN_700,
                                                        padding=ft.padding.symmetric(horizontal=8, vertical=4),
                                                        border_radius=4,
                                                    ),
                                                ],
                                                spacing=8,
                                                wrap=True,
                                            ),
                                            ft.Text((g.get("description", "") or "")[:80] + ("..." if len(g.get("description", "")) > 80 else ""), size=12, color=ft.Colors.GREY_700),
                                        ],
                                        expand=True,
                                        alignment=ft.MainAxisAlignment.START,
                                    ),
                                    ft.Row([ft.OutlinedButton("Edit", on_click=mk_edit(idx)), ft.OutlinedButton("Delete", on_click=mk_del(idx))], spacing=8),
                                    ft.Container(width=24),  # Space for drag handle
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                spacing=12,
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
        backend = (settings.get("BACKEND") or "gemini").strip().lower()
        if backend not in VALID_BACKENDS:
            backend = "gemini"

        # --- Hotkey (always enabled, at top) ---------------------------------
        dd = ft.Dropdown(label="Hotkey modifier", value=settings.get("HOTKEY_MODIFIER", "ctrl"), width=450, options=[ft.dropdown.Option(m) for m in VALID_MODIFIERS])

        # --- Gemini fields ---------------------------------------------------
        use_browser_cookie = settings.get("GEMINI_USE_BROWSER_COOKIE") is True
        pf = ft.TextField(label="GEMINI_PSID", value=settings.get("GEMINI_PSID", ""), width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)
        pt = ft.TextField(label="GEMINI_PSIDTS", value=settings.get("GEMINI_PSIDTS", ""), width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)

        def on_cb_change(e):
            use = cb.value
            pf.disabled = use
            pt.disabled = use
            pf.update()
            pt.update()

        cb = ft.Checkbox(label="Log in via browser-cookie3", value=use_browser_cookie, on_change=on_cb_change)
        cb2 = ft.Dropdown(label="GEMINI_COOKIE_BROWSER", value=settings.get("GEMINI_COOKIE_BROWSER", "edge"), width=450, options=[ft.dropdown.Option("edge"), ft.dropdown.Option("chrome")])
        px = ft.TextField(label="GEMINI_PROXY", value=settings.get("GEMINI_PROXY", ""), width=450, hint_text="e.g. socks5://127.0.0.1:8889")
        model_dd = ft.Dropdown(label="GEMINI_MODEL", value=settings.get("GEMINI_MODEL", "unspecified"), width=450, options=[ft.dropdown.Option(m) for m in AVAILABLE_MODELS])

        gemini_section = ft.Container(
            content=ft.Column([pf, pt, cb, cb2, px, model_dd], spacing=12),
            disabled=backend != "gemini",
        )

        # --- OpenAI fields ---------------------------------------------------
        ok = ft.TextField(label="OPENAI_API_KEY", value=settings.get("OPENAI_API_KEY", ""), width=450, password=True, can_reveal_password=True)
        ou = ft.TextField(label="OPENAI_BASE_URL", value=settings.get("OPENAI_BASE_URL", ""), width=450, hint_text="https://api.openai.com/v1")
        saved_models = settings.get("OPENAI_MODELS", [])
        om = ft.Dropdown(
            label="OPENAI_MODEL", value=settings.get("OPENAI_MODEL", "gpt-4o"),
            width=360,
            options=[ft.dropdown.Option(m) for m in saved_models] if saved_models else [ft.dropdown.Option(settings.get("OPENAI_MODEL", "gpt-4o"))],
        )

        async def refresh_models(e):
            refresh_btn.disabled = True
            refresh_btn.update()
            try:
                key = (ok.value or "").strip()
                url = (ou.value or "").strip() or None
                model_ids = await fetch_available_models(api_key=key, base_url=url)
                if model_ids:
                    settings["OPENAI_MODELS"] = model_ids
                    save_config(gems, settings)
                om.options = [ft.dropdown.Option(m) for m in model_ids] if model_ids else [ft.dropdown.Option("gpt-4o")]
                if model_ids and om.value not in model_ids:
                    om.value = model_ids[0]
                om.update()
            finally:
                refresh_btn.disabled = om.disabled
                refresh_btn.update()

        refresh_btn = ft.IconButton(icon=ft.Icons.REFRESH, tooltip="Refresh model list",
                                    on_click=lambda e: page.run_task(refresh_models, e))

        openai_section = ft.Container(
            content=ft.Column([ok, ou, ft.Row([om, refresh_btn], spacing=8)], spacing=12),
            disabled=backend != "openai",
        )

        # --- Backend selector (two buttons) ----------------------------------
        def switch_backend(to: str):
            nonlocal backend
            if to == backend:
                return
            backend = to
            gemini_btn.style = _btn_on if backend == "gemini" else _btn_off
            openai_btn.style = _btn_on if backend == "openai" else _btn_off
            gemini_section.disabled = backend != "gemini"
            openai_section.disabled = backend != "openai"
            gemini_btn.update()
            openai_btn.update()
            gemini_section.update()
            openai_section.update()
            page.update()

        _btn_on = ft.ButtonStyle(bgcolor=ft.Colors.BLUE_100, color=ft.Colors.BLUE_700, shape=ft.RoundedRectangleBorder(radius=8))
        _btn_off = ft.ButtonStyle(bgcolor=None, color=ft.Colors.BLUE_700, shape=ft.RoundedRectangleBorder(radius=8))
        gemini_btn = ft.ElevatedButton("Gemini", on_click=lambda e: switch_backend("gemini"), style=_btn_on if backend == "gemini" else _btn_off)
        openai_btn = ft.ElevatedButton("OpenAI", on_click=lambda e: switch_backend("openai"), style=_btn_on if backend == "openai" else _btn_off)

        backend_label = ft.Text(f"Backend: {backend.capitalize()}", weight=ft.FontWeight.W_600, size=14)

        def close():
            page.pop_dialog()

        def save_set(e):
            settings["BACKEND"] = backend
            settings["GEMINI_PSID"] = (pf.value or "").strip()
            settings["GEMINI_PSIDTS"] = (pt.value or "").strip()
            settings["GEMINI_PROXY"] = (px.value or "").strip()
            settings["GEMINI_USE_BROWSER_COOKIE"] = bool(cb.value)
            settings["GEMINI_COOKIE_BROWSER"] = (cb2.value or "edge").strip()
            m = (dd.value or "ctrl").strip().lower()
            settings["HOTKEY_MODIFIER"] = m if m in VALID_MODIFIERS else "ctrl"
            model_val = (model_dd.value or "unspecified").strip()
            settings["GEMINI_MODEL"] = model_val if model_val in AVAILABLE_MODELS else "unspecified"
            settings["OPENAI_API_KEY"] = (ok.value or "").strip()
            settings["OPENAI_BASE_URL"] = (ou.value or "").strip()
            settings["OPENAI_MODEL"] = (om.value or "gpt-4o").strip()
            save_config(gems, settings)
            close()

        page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Settings"),
                content=ft.Column(
                    [
                        ft.Text("Hotkey", weight=ft.FontWeight.W_600, size=14),
                        dd,
                        ft.Divider(),
                        backend_label,
                        ft.Row([gemini_btn, openai_btn], spacing=8),
                        ft.Divider(),
                        ft.Text("Gemini", weight=ft.FontWeight.W_600, size=14),
                        ft.Text("Set GEMINI_PSID to 'auto' to trigger browser login (may not work every time).", size=11, color=ft.Colors.GREY_600),
                        gemini_section,
                        ft.Divider(),
                        ft.Text("OpenAI", weight=ft.FontWeight.W_600, size=14),
                        openai_section,
                    ],
                    tight=True,
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[
                    ft.OutlinedButton("Cancel", on_click=lambda e: close()),
                    ft.FilledButton("Save", on_click=save_set),
                ],
                modal=True,
            )
        )

    page.add(
        ft.Row([ft.Text(f"{display_name} - {APP_ROOT}", size=24, weight=ft.FontWeight.BOLD), ft.Container(expand=True), ft.OutlinedButton("Settings", on_click=open_settings), ft.FilledButton("Add Gem", on_click=lambda e: open_edit(None))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),

        ft.Text("Restart to apply gems and settings.", size=11),
        ft.Divider(),
        ft.Container(
            content=ft.ReorderableListView(ref=gem_list_ref, on_reorder=on_reorder, spacing=8, expand=True),
            expand=True,
        ),
    )
    rebuild_gems()

import atexit
import flet as ft
def launch_ui(lock_path: Path | None = None) -> None:
    """
    Start the RenaClip UI window in a background thread (non-blocking).
    Uses lock_path for .renaclip_ui.lock; lock is removed when the window is closed
    via page.window.on_close.
    """
    path = lock_path or UI_LOCK_PATH

    try:
        path.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    atexit.register(_remove_ui_lock)

    ft.run(main=lambda page: _ui_main(page, lock_path=path), name=APP_NAME)
    print("UI finished")
