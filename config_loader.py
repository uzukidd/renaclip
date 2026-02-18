"""
Shared config loader for gem_config.json. Used by flet_demo and renaclip_app.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "gem_config.json"

DEFAULT_SETTINGS = {
    "GEMINI_1PSID": "",
    "GEMINI_1PSIDTS": "",
    "SOCKS5_PROXY": "",
    "HOTKEY_MODIFIER": "ctrl",
    "MODEL": "unspecified",
}

VALID_MODIFIERS = ("ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt")
AVAILABLE_MODELS = (
    "unspecified",
    "gemini-3.0-pro",
    "gemini-3.0-flash",
    "gemini-3.0-flash-thinking",
)


def load_config() -> tuple[list[dict], dict]:
    gems: list[dict] = []
    settings = dict(DEFAULT_SETTINGS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                gems = data.get("gems", gems)
                settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        except (json.JSONDecodeError, KeyError):
            pass
    return gems, settings
