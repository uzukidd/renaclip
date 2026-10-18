"""
Path constants based on the directory of the running program (script or frozen executable).
"""

import os
import sys
from pathlib import Path


def _get_app_root() -> Path:
    """Return the directory containing the main script or the frozen .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = _get_app_root()

# Environment flags
IS_DEV = False

# Packaged installs use a writable user directory; portable builds can opt in
# to exe-adjacent config with an empty portable.flag file.
if getattr(sys, "frozen", False) and not (APP_ROOT / "portable.flag").exists():
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RenaClip"
else:
    DATA_DIR = APP_ROOT
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "gem_config.json"
UI_LOCK_PATH = DATA_DIR / ".renaclip_ui.lock"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", str(APP_ROOT)))
ASSETS_DIR = RESOURCE_ROOT / "assets"
TRAY_ICON_PATH = ASSETS_DIR / "renaclip_icon.png"
