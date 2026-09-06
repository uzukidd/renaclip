"""
Path constants based on the directory of the running program (script or frozen executable).
"""

import flet as ft
import sys
from pathlib import Path


def _get_app_root() -> Path:
    """Return the directory containing the main script or the frozen .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = _get_app_root()

# # Environment flags
# print(getattr(sys, "FLET_PLATFORM", None))
IS_DEV = False

# Config and data
CONFIG_PATH = APP_ROOT / "gem_config.json"
UI_LOCK_PATH = APP_ROOT / ".renaclip_ui.lock"

# Assets
ASSETS_DIR = APP_ROOT / "assets"
TRAY_ICON_PATH = ASSETS_DIR / "renaclip_icon.png"
