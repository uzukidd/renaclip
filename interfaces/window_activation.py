"""Bring a user-invoked Qt tool window to the Windows foreground."""
import sys


def foreground_window(window_id):
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.restype = wintypes.BOOL
    user.SetActiveWindow.argtypes = [wintypes.HWND]
    user.SetActiveWindow.restype = wintypes.HWND
    user.SetFocus.argtypes = [wintypes.HWND]
    user.SetFocus.restype = wintypes.HWND
    user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user.AttachThreadInput.restype = wintypes.BOOL
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    handle = wintypes.HWND(int(window_id))
    foreground = user.GetForegroundWindow()
    current_thread = kernel.GetCurrentThreadId()
    foreground_thread = user.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = False
    try:
        # A global keyboard hook dispatches asynchronously to the Qt UI thread;
        # temporarily join the foreground input queue for this user-requested
        # activation. Never synthesize keys or change foreground-lock settings.
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(user.AttachThreadInput(current_thread, foreground_thread, True))
        user.SetForegroundWindow(handle)
        user.SetActiveWindow(handle)
        user.SetFocus(handle)
        return user.GetForegroundWindow() == int(window_id)
    finally:
        if attached:
            user.AttachThreadInput(current_thread, foreground_thread, False)
