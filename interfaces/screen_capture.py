"""Windows region capture. Call in the child process's main thread before Flet.

CaptureResult coordinates are physical pixels, including negative monitor origins.
Only adjacent_chat_geometry returns logical (physical / monitor scale) coordinates.
This module does not create processes, files, network connections, or a Flet app.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from io import BytesIO
import math
import sys
import threading

from PIL import Image, ImageEnhance, ImageGrab

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class CaptureResult:
    png_bytes: bytes
    bounds: Rect
    work_area: Rect
    scale: float


def normalize_region(bounds: Rect, minimum: int = 8) -> Rect | None:
    """Order physical drag endpoints; reject either dimension below minimum."""
    if minimum < 1:
        raise ValueError("minimum must be positive")
    x1, y1, x2, y2 = bounds
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (left, top, right, bottom) if right - left >= minimum and bottom - top >= minimum else None


def adjacent_chat_geometry(bounds: Rect, work_area: Rect,
                           scale: float = 1.0) -> tuple[int, int, int, int]:
    """Place a 440x640 logical chat right, then left, else inside the work area.

    A 12-logical-pixel gap separates chat from the selection when space permits.
    Integer rounding is inward so all returned edges fit the logical work area.
    """
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    left, top = (math.ceil(v / scale) for v in work_area[:2])
    right, bottom = (math.floor(v / scale) for v in work_area[2:])
    if right <= left or bottom <= top:
        raise ValueError("work_area must contain at least one logical pixel")
    region = normalize_region(bounds, minimum=1)
    if region is None:
        raise ValueError("bounds must have positive dimensions")
    width, height = min(440, right - left), min(640, bottom - top)
    after = math.ceil(region[2] / scale) + 12
    before = math.floor(region[0] / scale) - 12 - width
    x = after if after + width <= right else before if before >= left else after
    x = max(left, min(x, right - width))
    y = max(top, min(math.floor(region[1] / scale), bottom - height))
    return x, y, width, height


def _crop_png(desktop: Image.Image, bounds: Rect, virtual: Rect) -> bytes:
    """Translate physical screen coordinates into the captured image's origin."""
    region = normalize_region(bounds)
    if region is None:
        raise ValueError("capture region must be at least 8x8")
    x1, y1, x2, y2 = region
    vx1, vy1, vx2, vy2 = virtual
    if not (vx1 <= x1 < x2 <= vx2 and vy1 <= y1 < y2 <= vy2):
        raise ValueError("capture region is outside the virtual screen")
    with BytesIO() as output:
        desktop.crop((x1 - vx1, y1 - vy1, x2 - vx1, y2 - vy1)).save(output, "PNG")
        return output.getvalue()


class _WindowsDesktop:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user32
        # Per-monitor awareness must precede both ImageGrab and creation of Tk.
        try:
            set_process = u.SetProcessDpiAwarenessContext
            set_process.argtypes = [wintypes.HANDLE]
            set_process.restype = wintypes.BOOL
            if not set_process(ctypes.c_void_p(-4)):
                set_thread = u.SetThreadDpiAwarenessContext
                set_thread.argtypes = [wintypes.HANDLE]
                set_thread.restype = wintypes.HANDLE
                if not set_thread(ctypes.c_void_p(-4)):
                    raise ctypes.WinError(ctypes.get_last_error())
        except AttributeError:
            try:
                shcore = ctypes.WinDLL("shcore")
                shcore.SetProcessDpiAwareness(2)
            except OSError:
                u.SetProcessDPIAware()
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        x, y, w, h = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        self.virtual = (x, y, x + w, y + h)
        u.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
        u.MonitorFromRect.restype = wintypes.HANDLE
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        u.GetMonitorInfoW.restype = wintypes.BOOL
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL

    def place_overlay(self, root):
        x, y, right, bottom = self.virtual
        hwnd = self.user32.GetParent(root.winfo_id()) or root.winfo_id()
        # Tk's '-1920' geometry syntax means offset from the right, not screen x.
        if not self.user32.SetWindowPos(hwnd, -1, x, y, right - x, bottom - y, 0x0040):
            raise ctypes.WinError(ctypes.get_last_error())

    def monitor(self, bounds: Rect) -> tuple[Rect, float]:
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        rect = wintypes.RECT(*bounds)
        handle = self.user32.MonitorFromRect(ctypes.byref(rect), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        scale = 1.0
        try:
            get_dpi = ctypes.WinDLL("shcore").GetDpiForMonitor
            get_dpi.argtypes = [wintypes.HANDLE, ctypes.c_int,
                               ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
            get_dpi.restype = ctypes.c_long
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if get_dpi(handle, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                scale = dx.value / 96.0
        except (OSError, AttributeError):
            pass  # Windows versions predating per-monitor DPI use 96 DPI here.
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom), scale


def select_screen_region(stop_event=None) -> CaptureResult | None:
    """Block for a drag; return in-memory PNG or None on Escape/right-click.

    Windows only, in a fresh child process's main thread. Operational errors are
    re-raised after cleanup. A release smaller than 8x8 is treated as cancellation.
    Cross-monitor selections use the monitor with the largest intersection.
    An optional threading/multiprocessing event is polled every 150 ms; setting
    it cancels capture and returns None after destroying the overlay.
    """
    if sys.platform != "win32":
        raise OSError("screen capture requires Windows")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("screen capture must run in the main thread")
    native = _WindowsDesktop()
    virtual = native.virtual
    vx, vy, right, bottom = virtual
    desktop = ImageGrab.grab(all_screens=True, include_layered_windows=True)
    root = None
    try:
        if desktop.size != (right - vx, bottom - vy):
            raise RuntimeError("desktop geometry changed during capture; retry")
        import tkinter as tk
        from PIL import ImageTk

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{right - vx}x{bottom - vy}+0+0")
        canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        dimmed = ImageTk.PhotoImage(ImageEnhance.Brightness(desktop).enhance(0.35), master=root)
        canvas.create_image(0, 0, image=dimmed, anchor="nw")
        selected = canvas.create_image(0, 0, anchor="nw")
        outline = canvas.create_rectangle(0, 0, 0, 0, outline="#ffffff", width=2, state="hidden")
        start = None
        selection_photo = None
        result = None
        error = None

        def point(event):
            return max(0, min(event.x, desktop.width)), max(0, min(event.y, desktop.height))

        def cancel(event=None):
            root.quit()

        def check_stop():
            if stop_event.is_set():
                cancel()
            else:
                root.after(150, check_stop)

        def press(event):
            nonlocal start
            start = point(event)

        def motion(event):
            nonlocal selection_photo
            if start is None:
                return
            x, y = point(event)
            rect = normalize_region((*start, x, y), minimum=1)
            if rect is None:
                canvas.itemconfigure(selected, state="hidden")
                canvas.itemconfigure(outline, state="hidden")
                return
            selection_photo = ImageTk.PhotoImage(desktop.crop(rect), master=root)
            canvas.coords(selected, rect[0], rect[1])
            canvas.itemconfigure(selected, image=selection_photo, state="normal")
            canvas.coords(outline, *rect)
            canvas.itemconfigure(outline, state="normal")

        def release(event):
            nonlocal result
            if start is not None:
                x, y = point(event)
                bounds = normalize_region((start[0] + vx, start[1] + vy, x + vx, y + vy))
                if bounds is not None:
                    area, scale = native.monitor(bounds)
                    result = CaptureResult(_crop_png(desktop, bounds, virtual), bounds, area, scale)
            root.quit()

        def callback_error(exc_type, exc, traceback):
            nonlocal error
            error = exc
            root.quit()

        root.report_callback_exception = callback_error
        root.bind("<Escape>", cancel)
        root.bind("<Button-3>", cancel)
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", motion)
        canvas.bind("<ButtonRelease-1>", release)
        root.protocol("WM_DELETE_WINDOW", cancel)
        root.update_idletasks()
        root.deiconify()
        native.place_overlay(root)
        root.wait_visibility()
        root.grab_set_global()
        root.focus_force()
        if stop_event is not None:
            root.after(150, check_stop)
        root.mainloop()
        if error is not None:
            raise error
        return result
    finally:
        try:
            if root is not None:
                root.destroy()  # Also releases the global mouse/keyboard grab.
        finally:
            desktop.close()
