"""Current-user Windows login startup; no administrator privileges required."""
from pathlib import Path
import subprocess
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'RenaClip'


def startup_command():
    executable = Path(sys.executable).resolve()
    if getattr(sys, 'frozen', False):
        args = [str(executable), '--hidden']
    else:
        # pythonw keeps login startup quiet while preserving the Conda environment.
        pythonw = executable.with_name('pythonw.exe')
        if pythonw.is_file():
            executable = pythonw
        args = [str(executable), str(Path(__file__).resolve().parents[1] / 'main.py'), '--hidden']
    return subprocess.list2cmdline(args)


def read_startup_command():
    if sys.platform != 'win32':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, kind = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except FileNotFoundError:
        return None


def write_startup_command(command):
    if sys.platform != 'win32':
        if command is not None:
            raise OSError('Windows login startup is only available on Windows')
        return
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
