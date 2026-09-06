"""
RenaClip UI host. Run the settings/gem list window via launch_ui() (non-blocking).
"""

import atexit
import json
import flet as ft
import os
import threading
from pathlib import Path

from config_loader import (
    load_config,
    save_config,
    VALID_MODIFIERS,
    AVAILABLE_MODELS,
    VALID_BACKENDS,
    VALID_REASONING_EFFORTS,
    normalize_reasoning_effort,
    get_active_openai_provider,
    normalize_openai_providers,
)
from constants import APP_ROOT, IS_DEV
from interfaces import theme as ui_theme
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

    ui_theme.apply_flet_theme(page)
    page.title = f"{display_name} - {APP_ROOT}"  if IS_DEV else display_name
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
            col.controls.append(ft.Text(
                "No gems yet. Click 'Add Gem' to create one.", color=ui_theme.TEXT_MUTED))
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
                                                    ft.Text(
                                                        g.get("name", "Unnamed"), weight=ft.FontWeight.W_600, size=16),
                                                    ft.Container(
                                                        content=ft.Text(
                                                            shortcut, size=11, color=ui_theme.TEXT),
                                                        bgcolor=ui_theme.ACCENT,
                                                        padding=ft.padding.symmetric(
                                                            horizontal=8, vertical=4),
                                                        border_radius=4,
                                                    ),
                                                    *([
                                                        ft.Icon(
                                                            ft.Icons.PUBLIC,
                                                            size=18,
                                                            color=ui_theme.ACCENT_LIGHT,
                                                            tooltip="Web search enabled",
                                                        )
                                                    ] if g.get("web_search", False) else []),
                                                ],
                                                spacing=8,
                                                wrap=True,
                                            ),
                                            ft.Text((g.get("description", "") or "")[
                                                    :80] + ("..." if len(g.get("description", "")) > 80 else ""), size=12, color=ui_theme.TEXT_MUTED),
                                        ],
                                        expand=True,
                                        alignment=ft.MainAxisAlignment.START,
                                    ),
                                    ft.Row([ft.OutlinedButton("Edit", on_click=mk_edit(idx)), ft.OutlinedButton(
                                        "Delete", on_click=mk_del(idx))], spacing=8),
                                    # Space for drag handle
                                    ft.Container(width=24),
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
        g = gems[index] if index is not None else {
            "name": "", "description": "", "prompt": "", "web_search": False}
        nf = ft.TextField(label="Title", value=g.get("name", ""))
        df = ft.TextField(label="Description", value=g.get(
            "description", ""), multiline=True, min_lines=2)
        pf = ft.TextField(label="Prompt", value=g.get(
            "prompt", ""), multiline=True, min_lines=4)
        wf = ft.Checkbox(
            label="Enable web search",
            value=bool(g.get("web_search", False)),
        )

        def close():
            page.pop_dialog()

        def save(idx, _nf, _df, _pf, _wf):
            def _save(e):
                item = {
                    "name": _nf.value or "Unnamed",
                    "description": _df.value or "",
                    "prompt": _pf.value or "",
                    "web_search": bool(_wf.value),
                }
                if idx is not None:
                    gems[idx] = item
                else:
                    gems.append(item)
                save_config(gems, settings)
                rebuild_gems()
                close()
            return _save

        d = ft.AlertDialog(
            title=ft.Text("Edit Skill" if index is not None else "Add Skill"),
            content=ft.Column(
                [nf, df, pf, wf],
                tight=True,
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.OutlinedButton("Cancel", on_click=lambda e: close()),
                ft.FilledButton("Save", on_click=save(index, nf, df, pf, wf)),
            ],
            modal=True,
        )
        page.show_dialog(d)

    def open_settings(e=None):
        backend = (settings.get("BACKEND") or "gemini").strip().lower()
        if backend not in VALID_BACKENDS:
            backend = "gemini"

        # --- Hotkey (always enabled, at top) ---------------------------------
        dd = ft.Dropdown(label="Hotkey modifier", value=settings.get(
            "HOTKEY_MODIFIER", "ctrl"), width=450, options=[ft.dropdown.Option(m) for m in VALID_MODIFIERS])

        screenshot_qa_cb = ft.Checkbox(
            label="Screenshot Q&A (Hotkey + 0)",
            value=settings.get("SCREENSHOT_QA_ENABLED", False) is True,
        )

        screenshot_web_search_cb = ft.Checkbox(
            label="Web search",
            value=settings.get("SCREENSHOT_QA_WEB_SEARCH", False) is True,
            disabled=not screenshot_qa_cb.value,
        )

        def on_screenshot_toggle(e):
            screenshot_web_search_cb.disabled = not screenshot_qa_cb.value
            screenshot_web_search_cb.update()

        screenshot_qa_cb.on_change = on_screenshot_toggle

        # --- Gemini fields ---------------------------------------------------
        use_browser_cookie = settings.get("GEMINI_USE_BROWSER_COOKIE") is True
        pf = ft.TextField(label="GEMINI_PSID", value=settings.get("GEMINI_PSID", ""),
                          width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)
        pt = ft.TextField(label="GEMINI_PSIDTS", value=settings.get("GEMINI_PSIDTS", ""),
                          width=450, password=True, can_reveal_password=True, disabled=use_browser_cookie)

        def on_cb_change(e):
            use = cb.value
            pf.disabled = use
            pt.disabled = use
            pf.update()
            pt.update()

        cb = ft.Checkbox(label="Log in via browser-cookie3",
                         value=use_browser_cookie, on_change=on_cb_change)
        cb2 = ft.Dropdown(label="GEMINI_COOKIE_BROWSER", value=settings.get(
            "GEMINI_COOKIE_BROWSER", "edge"), width=450, options=[ft.dropdown.Option("edge"), ft.dropdown.Option("chrome")])
        px = ft.TextField(label="GEMINI_PROXY", value=settings.get(
            "GEMINI_PROXY", ""), width=450, hint_text="e.g. socks5://127.0.0.1:8889")
        model_dd = ft.Dropdown(label="GEMINI_MODEL", value=settings.get(
            "GEMINI_MODEL", "unspecified"), width=450, options=[ft.dropdown.Option(m) for m in AVAILABLE_MODELS])

        gemini_section = ft.Container(
            content=ft.Column([pf, pt, cb, cb2, px, model_dd], spacing=12),
            disabled=backend != "gemini",
        )

        # --- OpenAI-compatible providers --------------------------------------
        providers = normalize_openai_providers(settings)
        provider_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)

        def provider_model_ids(provider: dict) -> list[str]:
            models = list(provider.get("models") or [])
            current_model = (provider.get("model") or "").strip()
            if current_model and current_model not in models:
                models.insert(0, current_model)
            return models or ["gpt-4o"]

        def provider_interface_label(provider: dict) -> str:
            return "Responses API" if provider.get("api_interface") == "responses" else "Chat Completions"

        def rebuild_provider_list() -> None:
            provider_list.controls.clear()
            for index, provider in enumerate(providers):
                summary = (
                    f"{provider_interface_label(provider)}  |  "
                    f"{provider.get('model') or 'gpt-4o'}  |  "
                    f"{provider.get('base_url') or 'default endpoint'}"
                )

                def open_item(e, item_index=index):
                    open_provider_editor(item_index)

                def delete_item(e, item_index=index):
                    delete_provider(item_index)

                provider_list.controls.append(
                    ft.Card(
                        content=ft.ListTile(
                            title=ft.Text(provider["name"], weight=ft.FontWeight.W_600),
                            subtitle=ft.Text(summary, size=12),
                            trailing=ft.Row([
                                ft.IconButton(
                                    icon=ft.Icons.EDIT_OUTLINED,
                                    tooltip="Edit provider",
                                    on_click=open_item,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    tooltip="Delete provider",
                                    on_click=delete_item,
                                ),
                            ], tight=True),
                            on_click=open_item,
                        )
                    )
                )

        def delete_provider(index: int) -> None:
            if len(providers) <= 1:
                return
            deleted_name = providers[index]["name"]
            providers.pop(index)
            if settings.get("OPENAI_ACTIVE_PROVIDER") == deleted_name:
                settings["OPENAI_ACTIVE_PROVIDER"] = providers[0]["name"]
            settings["OPENAI_PROVIDERS"] = providers
            save_config(gems, settings)
            rebuild_provider_list()
            page.update()
            refresh_main_provider_options()

        async def refresh_editor_models(e, draft: dict, model_dd, models_field, refresh_btn, key_field, url_field):
            draft["api_key"] = (key_field.value or "").strip()
            draft["base_url"] = (url_field.value or "").strip()
            refresh_btn.disabled = True
            refresh_btn.update()
            try:
                model_ids = await fetch_available_models(
                    api_key=draft["api_key"],
                    base_url=draft["base_url"] or None,
                )
                if model_ids:
                    draft["models"] = model_ids
                    if draft.get("model") not in model_ids:
                        draft["model"] = model_ids[0]
                    models_field.value = "\n".join(model_ids)
                    model_dd.options = [ft.dropdown.Option(model) for model in model_ids]
                    model_dd.value = draft["model"]
                    model_dd.update()
                    models_field.update()
            finally:
                refresh_btn.disabled = False
                refresh_btn.update()

        def open_provider_editor(index: int, is_new: bool = False) -> None:
            provider = providers[index]
            draft = dict(provider)
            draft["models"] = list(provider.get("models") or [])
            name_field = ft.TextField(label="Provider name", value=provider["name"], width=500)
            key_field = ft.TextField(
                label="API key", value=provider.get("api_key", ""), width=500,
                password=True, can_reveal_password=True,
            )
            url_field = ft.TextField(
                label="Base URL", value=provider.get("base_url", ""), width=500,
                hint_text="https://api.openai.com/v1",
            )
            interface_dd = ft.Dropdown(
                label="API interface", width=500,
                value=provider.get("api_interface", "chat_completions"),
                options=[
                    ft.dropdown.Option("chat_completions", "Chat Completions"),
                    ft.dropdown.Option("responses", "Responses API"),
                ],
            )
            model_ids = provider_model_ids(provider)
            model_dd = ft.Dropdown(
                label="Default model", width=450,
                value=provider.get("model") or model_ids[0],
                options=[ft.dropdown.Option(model) for model in model_ids],
            )
            models_field = ft.TextField(
                label="Model IDs (one per line)", value="\n".join(provider.get("models") or []),
                width=500, multiline=True, min_lines=4,
                hint_text="Enter model IDs manually or refresh from the endpoint",
            )
            streaming_cb = ft.Checkbox(label="Streaming mode", value=bool(provider.get("streaming", False)))
            refresh_btn = ft.IconButton(
                icon=ft.Icons.REFRESH,
                tooltip="Refresh model list",
                on_click=lambda e: page.run_task(
                    refresh_editor_models, e, draft, model_dd, models_field, refresh_btn,
                    key_field, url_field
                ),
            )

            def cancel_editor(e=None):
                if is_new:
                    providers.pop(index)
                    rebuild_provider_list()
                    page.update()
                page.pop_dialog()

            def save_editor(e):
                old_name = provider["name"]
                new_name = (name_field.value or "Unnamed provider").strip()
                existing_names = {item["name"] for i, item in enumerate(providers) if i != index}
                if new_name in existing_names:
                    suffix = 2
                    base_name = new_name
                    while f"{base_name} ({suffix})" in existing_names:
                        suffix += 1
                    new_name = f"{base_name} ({suffix})"
                models = [
                    line.strip()
                    for line in (models_field.value or "").replace(",", "\n").splitlines()
                    if line.strip()
                ]
                selected_model = (model_dd.value or "gpt-4o").strip()
                if selected_model not in models:
                    models.insert(0, selected_model)
                provider.clear()
                provider.update({
                    "name": new_name,
                    "api_key": (key_field.value or "").strip(),
                    "base_url": (url_field.value or "").strip(),
                    "api_interface": interface_dd.value or "chat_completions",
                    "model": selected_model,
                    "models": models,
                    "streaming": bool(streaming_cb.value),
                })
                if settings.get("OPENAI_ACTIVE_PROVIDER") == old_name or is_new:
                    settings["OPENAI_ACTIVE_PROVIDER"] = new_name
                settings["OPENAI_PROVIDERS"] = providers
                save_config(gems, settings)
                rebuild_provider_list()
                page.update()
                refresh_main_provider_options()
                page.pop_dialog()

            page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(f"Edit Provider: {provider['name']}"),
                    content=ft.Column(
                        [
                            name_field, key_field, url_field, interface_dd,
                            ft.Row([model_dd, refresh_btn], spacing=8),
                            models_field, streaming_cb,
                        ],
                        tight=True, spacing=12, scroll=ft.ScrollMode.AUTO,
                    ),
                    actions=[
                        ft.OutlinedButton("Cancel", on_click=cancel_editor),
                        ft.FilledButton("Save", on_click=save_editor),
                    ],
                    modal=True,
                )
            )

        def add_provider(e):
            existing_names = {provider["name"] for provider in providers}
            name = "New provider"
            suffix = 2
            while name in existing_names:
                name = f"New provider ({suffix})"
                suffix += 1
            providers.append({
                "name": name, "api_key": "", "base_url": "",
                "api_interface": "chat_completions", "model": "gpt-4o",
                "models": ["gpt-4o"], "streaming": False,
            })
            open_provider_editor(len(providers) - 1, is_new=True)

        add_provider_btn = ft.OutlinedButton("Add provider", on_click=add_provider)
        openai_section = ft.Container(
            content=ft.Column([provider_list, add_provider_btn], spacing=12),
            disabled=backend != "openai",
        )
        rebuild_provider_list()

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

        _btn_on = ft.ButtonStyle(
            bgcolor=ui_theme.SURFACE_ACCENT, color=ui_theme.ACCENT_LIGHT, shape=ft.RoundedRectangleBorder(radius=ui_theme.RADIUS))
        _btn_off = ft.ButtonStyle(
            bgcolor=None, color=ui_theme.ACCENT_LIGHT, shape=ft.RoundedRectangleBorder(radius=ui_theme.RADIUS))
        gemini_btn = ft.ElevatedButton("Gemini Web (unstable)", on_click=lambda e: switch_backend(
            "gemini"), style=_btn_on if backend == "gemini" else _btn_off)
        openai_btn = ft.ElevatedButton("OpenAI", on_click=lambda e: switch_backend(
            "openai"), style=_btn_on if backend == "openai" else _btn_off)

        backend_label = ft.Text(
            f"Backend: {backend.capitalize()}", weight=ft.FontWeight.W_600, size=14)

        def close():
            page.pop_dialog()

        def save_set(e):
            settings["BACKEND"] = backend
            settings["SCREENSHOT_QA_ENABLED"] = bool(screenshot_qa_cb.value)
            settings["SCREENSHOT_QA_WEB_SEARCH"] = bool(screenshot_web_search_cb.value)
            settings["GEMINI_PSID"] = (pf.value or "").strip()
            settings["GEMINI_PSIDTS"] = (pt.value or "").strip()
            settings["GEMINI_PROXY"] = (px.value or "").strip()
            settings["GEMINI_USE_BROWSER_COOKIE"] = bool(cb.value)
            settings["GEMINI_COOKIE_BROWSER"] = (cb2.value or "edge").strip()
            m = (dd.value or "ctrl").strip().lower()
            settings["HOTKEY_MODIFIER"] = m if m in VALID_MODIFIERS else "ctrl"
            model_val = (model_dd.value or "unspecified").strip()
            settings["GEMINI_MODEL"] = model_val if model_val in AVAILABLE_MODELS else "unspecified"
            save_config(gems, settings)
            refresh_main_provider_options()
            close()

        page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Settings"),
                content=ft.Column(
                    [
                        ft.Text("Hotkey", weight=ft.FontWeight.W_600, size=14),
                        dd,
                        ft.Divider(),
                        ft.Text("Screenshot QA", weight=ft.FontWeight.W_600, size=14),
                        ft.Column([
                            ft.Row([screenshot_qa_cb, screenshot_web_search_cb], spacing=16, wrap=True),
                        ], spacing=8),
                        ft.Divider(),
                        backend_label,
                        ft.Row([openai_btn, gemini_btn], spacing=8),
                        ft.Divider(),
                        ft.Text("OpenAI", weight=ft.FontWeight.W_600, size=14),
                        openai_section,
                        ft.Divider(),
                        ft.Text("Gemini", weight=ft.FontWeight.W_600, size=14),
                        ft.Text("Set GEMINI_PSID to 'auto' to trigger browser login (may not work every time).",
                                size=11, color=ui_theme.TEXT_MUTED),
                        gemini_section,

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

    # Model keys include the provider so duplicate model IDs stay distinct.
    reasoning_dd = ft.Dropdown(
        label="Reasoning", width=180,
        value=normalize_reasoning_effort(settings.get("OPENAI_REASONING_EFFORT")),
        options=[ft.dropdown.Option(key=effort, text=effort.capitalize())
                 for effort in VALID_REASONING_EFFORTS],
    )

    def on_reasoning_change(e):
        settings["OPENAI_REASONING_EFFORT"] = normalize_reasoning_effort(reasoning_dd.value)
        save_config(gems, settings)

    reasoning_dd.on_select = on_reasoning_change
    main_model_dd = ft.Dropdown(label="Model", width=280, menu_height=400)
    model_routes: dict[str, tuple[str, str]] = {}

    def refresh_main_provider_options() -> None:
        refresh_main_model_options()
        main_model_dd.update()

    def refresh_main_model_options() -> None:
        providers = normalize_openai_providers(settings)
        active_name = settings["OPENAI_ACTIVE_PROVIDER"]
        model_routes.clear()
        options = []
        for index, provider in enumerate(providers):
            name = provider["name"]
            options.append(ft.dropdown.Option(
                key=f"provider:{index}", text=name, disabled=True,
                content=ft.Text(name, weight=ft.FontWeight.W_600, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, tooltip=name),
            ))
            models = list(provider.get("models") or [])
            current_model = provider["model"]
            if current_model not in models:
                models.insert(0, current_model)
            for model in models:
                key = json.dumps([name, model])
                model_routes[key] = (name, model)
                options.append(ft.dropdown.Option(
                    key=key, text=model,
                    content=ft.Container(
                        content=ft.Text(model, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                        padding=ft.Padding.only(left=16),
                        tooltip=f"{name}: {model}",
                    ),
                ))
            if name == active_name:
                main_model_dd.value = json.dumps([name, current_model])
        main_model_dd.options = options

    def on_main_model_change(e):
        route = model_routes.get(main_model_dd.value)
        if route is None:
            return
        name, model = route
        settings["OPENAI_ACTIVE_PROVIDER"] = name
        provider = get_active_openai_provider(settings)
        provider["model"] = model
        save_config(gems, settings)
        refresh_main_provider_options()

    main_model_dd.on_select = on_main_model_change
    refresh_main_model_options()

    page.add(
        ft.Row(
            [
                ft.Text(f"{display_name}", size=24, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                reasoning_dd,
                main_model_dd,
                ft.OutlinedButton("Settings", on_click=open_settings),
                ft.FilledButton("Add Skill", on_click=lambda e: open_edit(None)),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),

        ft.Divider(),
        ft.Container(
            content=ft.ReorderableListView(
                ref=gem_list_ref, on_reorder=on_reorder, spacing=8, expand=True),
            expand=True,
        ),
    )
    rebuild_gems()


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
