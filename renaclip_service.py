"""
Launcher for renaclip clipboard service. Spawns renaclip_app as subprocess.
Keeps flet_demo decoupled from gemini_client, keyboard, pyperclip.
"""

import subprocess
import sys
from pathlib import Path

RENACLIP_APP = Path(__file__).resolve().parent / "renaclip_app.py"


def start() -> subprocess.Popen | None:
    """Start renaclip_app as subprocess. Returns process or None on failure."""
    try:
        return subprocess.Popen(
            [sys.executable, str(RENACLIP_APP)],
            cwd=Path(__file__).resolve().parent,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0,
        )
    except Exception:
        return None


def stop(proc: subprocess.Popen | None) -> bool:
    """Terminate renaclip process. Returns True if terminated."""
    if proc is None:
        return False
    try:
        proc.terminate()
        proc.wait(timeout=3)
        return True
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return True


def is_running(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None
