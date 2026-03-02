"""
RenaClip UI host. Run the settings/gem list window via launch_ui() (non-blocking).
"""

import os
import threading
from pathlib import Path

from config_loader import load_config, save_config, VALID_MODIFIERS, AVAILABLE_MODELS
from constants import APP_ROOT, IS_DEV

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
    icon_path = SCRIPT_DIR / "assets" / "renaclip_icon.ico"
    if not icon_path.is_file():
        icon_path = SCRIPT_DIR / "assets" / "renaclip_icon.png"
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
        use_browser_cookie = settings.get("USE_BROWSER_COOKIE") is True
        pf = ft.TextField(label="GEMINI_1PSID", value=settings.get("GEMINI_1PSID", ""), width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)
        pt = ft.TextField(label="GEMINI_1PSIDTS", value=settings.get("GEMINI_1PSIDTS", ""), width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)
        cb = ft.Checkbox(label="Log in via browser-cookie3", value=use_browser_cookie)

        def on_cb_change(e):
            use = cb.value
            pf.disabled = use
            pt.disabled = use
            try:
                pf.update()
                pt.update()
            except Exception:
                pass

        cb.on_change = on_cb_change

        px = ft.TextField(label="Proxy (SOCKS5_PROXY)", value=settings.get("SOCKS5_PROXY", ""), width=450, hint_text="e.g. socks5://127.0.0.1:8889")
        dd = ft.Dropdown(label="Hotkey modifier", value=settings.get("HOTKEY_MODIFIER", "ctrl"), width=450, options=[ft.dropdown.Option(m) for m in VALID_MODIFIERS])
        model_dd = ft.Dropdown(label="Model", value=settings.get("MODEL", "unspecified"), width=450, options=[ft.dropdown.Option(m) for m in AVAILABLE_MODELS])

        def close():
            page.pop_dialog()

        def save_set(e):
            settings["GEMINI_1PSID"] = (pf.value or "").strip()
            settings["GEMINI_1PSIDTS"] = (pt.value or "").strip()
            settings["SOCKS5_PROXY"] = (px.value or "").strip()
            settings["USE_BROWSER_COOKIE"] = bool(cb.value)
            m = (dd.value or "ctrl").strip().lower()
            settings["HOTKEY_MODIFIER"] = m if m in VALID_MODIFIERS else "ctrl"
            model_val = (model_dd.value or "unspecified").strip()
            settings["MODEL"] = model_val if model_val in AVAILABLE_MODELS else "unspecified"
            save_config(gems, settings)
            close()

        page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Settings"),
                content=ft.Column(
                    [ft.Text("Set GEMINI_1PSID to 'auto' to trigger browser login (may not work every time).", size=11, color=ft.Colors.GREY_600), pf, pt, cb, px, dd, model_dd, ft.Row([ft.OutlinedButton("Cancel", on_click=lambda e: close()), ft.FilledButton("Save", on_click=save_set)], alignment=ft.MainAxisAlignment.END)],
                    tight=True,
                    spacing=12,
                ),
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

