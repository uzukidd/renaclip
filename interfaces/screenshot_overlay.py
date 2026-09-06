"""Persistent native screenshot selection with editable single-question answers."""

import asyncio
import base64
from concurrent.futures import TimeoutError
from copy import deepcopy
import math
import queue
import threading

from PIL import ImageEnhance, ImageGrab

from interfaces.screen_capture import _WindowsDesktop, _crop_png, normalize_region
from openai_client.screenshot_chat import ScreenshotConversation
from interfaces.markdown_text import parse_markdown, TkMarkdownRenderer
from interfaces.answer_layout import (
    MAX_PANEL_HEIGHT, MAX_ANSWER_HEIGHT, measure_wrapped_lines,
    answer_content_height, compute_answer_bounds,
)

from interfaces.theme import (
    ACCENT, SURFACE_ACCENT as QUESTION_BG, SURFACE as ANSWER_BG,
    TEXT as TEXT_COLOR, ACCENT_LIGHT as CAPTION_COLOR,
    FONT_FAMILY, BODY_SIZE, RADIUS, CONTENT_PADDING, SCROLLBAR_WIDTH, SELECTION_WIDTH,
    configure_tk_scrollbar,
)


def fit_caption(text, max_width, measure):
    """Ellipsize one header line to leave a fixed area for the spinner."""
    if measure(text) <= max_width:
        return text
    suffix = "..."
    if measure(suffix) > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle] + suffix) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + suffix


def contains(rect, x, y):
    return rect is not None and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def panel_geometry(bounds, work_area, scale=1.0):
    """Return physical layout bounds, preferring free right/left/below/above space."""
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be positive")
    left, top, right, bottom = work_area
    if right <= left or bottom <= top:
        raise ValueError("empty work area")
    width, height = min(round(360 * scale), right - left), min(round(330 * scale), bottom - top)
    gap = max(1, round(8 * scale))
    x1, y1, x2, y2 = bounds
    y = max(top, min(y1, bottom - height))
    x = max(left, min(x1, right - width))
    candidates = [(x2 + gap, y), (x1 - gap - width, y),
                  (x, y2 + gap), (x, y1 - gap - height)]
    for px, py in candidates:
        if left <= px and px + width <= right and top <= py and py + height <= bottom:
            return px, py, px + width, py + height
    spaces = [(max(left, x2 + gap), top, right, bottom),
              (left, top, min(right, x1 - gap), bottom),
              (left, max(top, y2 + gap), right, bottom),
              (left, top, right, min(bottom, y1 - gap))]
    usable = [r for r in spaces if r[2] - r[0] >= min(width, round(180 * scale))
              and r[3] - r[1] >= min(height, round(140 * scale))]
    if usable:
        l, t, r, b = max(usable, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        return l, t, min(r, l + width), min(b, t + height)
    x = max(left, min(x2 + gap, right - width))
    return x, y, x + width, y + height


def bubble_geometry(panel, scale):
    l, t, r, b = panel
    gap = min(max(2, round(10 * scale)), max(2, (b - t) // 12))
    question_height = min(round(96 * scale), max(1, (b - t - gap) // 3))
    return (l, t, r, t + question_height), (l, t + question_height + gap, r, b)


class AsyncRequestRunner:
    """Only this worker thread touches asyncio; only Tk consumes result events."""

    def __init__(self, conversation_factory=ScreenshotConversation):
        self.factory = conversation_factory
        self.loop = asyncio.new_event_loop()
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="screenshot-request")
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    def submit(self, png, question, provider, generation, events):
        async def request():
            try:
                url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                conversation = self.factory(deepcopy(provider), url)
                answer = await conversation.send(
                    question, on_delta=lambda text: events.put((generation, "delta", text)),
                )
                events.put((generation, "done", answer))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                message = f"Request failed (HTTP {status})." if isinstance(status, int) else "Request failed. Please retry."
                events.put((generation, "error", message))
        if self.closed:
            raise RuntimeError("request runner is closed")
        return asyncio.run_coroutine_threadsafe(request(), self.loop)

    def cancel(self, handle):
        if handle is not None:
            handle.cancel()

    def close(self):
        if self.closed:
            return
        self.closed = True

        async def shutdown():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=4)
        except TimeoutError:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)


class ScreenshotOverlay:
    """Physical-coordinate state API; owns injected desktop and request runner."""

    def __init__(self, provider, stop_event=None, focus_event=None, *,
                 desktop=None, native=None, request_runner=None, on_close=None):
        self.provider = deepcopy(provider)
        self.on_close = on_close
        self.stop_event, self.focus_event = stop_event, focus_event
        self.native = native if native is not None else _WindowsDesktop()
        self.virtual = self.native.virtual
        self.desktop = desktop if desktop is not None else ImageGrab.grab(
            all_screens=True, include_layered_windows=True)
        self.runner = request_runner
        self.events = queue.Queue()
        self.root = None
        self.bounds = self.panel_bounds = self.start = None
        self.question_bubble_bounds = self.answer_bubble_bounds = None
        self.request = None
        self.generation = 0
        self.pending = self.sent = self.closed = self.editing = False
        self.question = self.answer = ""
        self._markdown_source = None
        self._answer_document = None
        self._answer_renderer = None
        effort = str(provider.get("reasoning_effort") or "default").lower()
        label = {"low": "Low", "medium": "Medium", "high": "High"}.get(effort, "Default")
        self.model_caption = f"{provider.get('model') or 'Model'} / {label}"
        self.answer_visible = self.waiting_visible = False
        self.hint_visible = True
        self.png = None
        self.scale = 1.0
        self.spinner_angle = 0
        self.spinner_visible = False
        self._spinner_after = None
        self._question_needs_sync = True
        self._layout_key = None
        self._enter_pressed = False

    def _point(self, x, y):
        l, t, r, b = self.virtual
        return max(l, min(x, r)), max(t, min(y, b))

    def begin_selection(self, x, y):
        if self.closed:
            return False
        if (contains(self.question_bubble_bounds, x, y) or
                (self.answer_visible and contains(self.answer_bubble_bounds, x, y))):
            if self.sent and not self.pending:
                self.begin_editing(focus=contains(self.question_bubble_bounds, x, y))
            return False
        if contains(self.bounds, x, y):
            return False
        self.generation += 1
        if self.runner is not None:
            self.runner.cancel(self.request)
        self.request = None
        self._stop_spinner()
        self.bounds = self.panel_bounds = self.png = None
        self.question_bubble_bounds = self.answer_bubble_bounds = None
        self.pending = self.sent = self.editing = False
        self.question = self.answer = ""
        self.answer_visible = self.waiting_visible = False
        self.hint_visible = True
        self._question_needs_sync = True
        self._enter_pressed = False
        self.start = self._point(x, y)
        self._render_selection(None)
        self._sync_panel()
        return True

    def begin_editing(self, focus=True):
        if self.closed or self.pending or not self.sent:
            return False
        self.editing = True
        self._sync_panel()
        if self.root is not None and focus:
            self.question_widget.focus_set()
        return True

    def drag_selection(self, x, y):
        if self.start is not None:
            self._render_selection(normalize_region((*self.start, *self._point(x, y)), minimum=1))

    def finish_selection(self, x, y):
        if self.start is None or self.closed:
            return
        self.bounds = normalize_region((*self.start, *self._point(x, y)))
        self.start = None
        if self.bounds is not None:
            self.png = _crop_png(self.desktop, self.bounds, self.virtual)
            area, self.scale = self.native.monitor(self.bounds)
            self.panel_bounds = panel_geometry(self.bounds, area, self.scale)
            l, t, r, _ = self.panel_bounds
            max_bottom = area[3]
            # A panel placed above the selection must not grow into the image.
            if l < self.bounds[2] and r > self.bounds[0] and t < self.bounds[1]:
                max_bottom = min(max_bottom, self.bounds[1] - round(8 * self.scale))
            self.panel_bounds = (l, t, r, min(max_bottom, t + round(MAX_PANEL_HEIGHT * self.scale)))
            self.question_bubble_bounds, self.answer_bubble_bounds = bubble_geometry(self.panel_bounds, self.scale)
            self._fit_answer_height()
            self.editing = True
        self._render_selection(self.bounds)
        self._sync_panel()
        if self.root is not None and self.bounds is not None:
            self.question_widget.focus_set()

    def send_question(self, question=None):
        if self.closed or self.bounds is None or self.pending or (self.sent and not self.editing):
            return False
        text = (question if question is not None else self.question_widget.get("1.0", "end")).strip()
        if not text:
            return False
        self.question, self.answer = text, ""
        self.answer_visible = False
        self.waiting_visible = True
        self.hint_visible = False
        self._question_needs_sync = True
        self.pending, self.sent, self.editing = True, False, False
        self.generation += 1
        self._sync_panel()
        self._start_spinner()
        try:
            if self.runner is None:
                self.runner = AsyncRequestRunner()
            self.request = self.runner.submit(self.png, text, self.provider, self.generation, self.events)
        except Exception:
            self.events.put((self.generation, "error", "Request failed. Please retry."))
        return True

    def _ime_composing(self):
        # Native IMEs normally consume Return; also guard an active Win32 composition.
        if self.root is None:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            ime = ctypes.WinDLL("imm32")
            ime.ImmGetContext.argtypes = [wintypes.HWND]
            ime.ImmGetContext.restype = wintypes.HANDLE
            ime.ImmGetCompositionStringW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            ime.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
            hwnd = self.question_widget.winfo_id()
            context = ime.ImmGetContext(hwnd)
            try:
                return bool(context and ime.ImmGetCompositionStringW(context, 8, None, 0) > 0)
            finally:
                if context:
                    ime.ImmReleaseContext(hwnd, context)
        except (AttributeError, OSError):
            return False

    def handle_enter(self, event):
        if getattr(event, "state", 0) & 1:
            return None  # Let Tk Text insert one newline for Shift+Enter.
        if self._ime_composing():
            return None
        if not self._enter_pressed:
            self._enter_pressed = True
            self.send_question()
        return "break"

    def release_enter(self, event=None):
        self._enter_pressed = False

    def drain_events(self):
        changed = False
        while True:
            try:
                token, kind, text = self.events.get_nowait()
            except queue.Empty:
                break
            if self.closed or token != self.generation or not self.pending:
                continue
            if kind == "delta":
                self.answer += text
                if self.answer.strip():
                    self.answer_visible = True
                    self.waiting_visible = False
            elif kind in ("done", "error"):
                self.answer = text or "The model returned no text. Please retry."
                self.answer_visible = True
                self.waiting_visible = False
                self.pending = False
                self.sent = kind == "done" and bool(text.strip())
                self.editing = not self.sent
                self.request = None
                self._stop_spinner()
            changed = True
        if changed:
            self._sync_panel()

    def _start_spinner(self):
        self._stop_spinner()
        self.spinner_visible = True
        self.spinner_angle = 0
        self._tick_spinner()

    def _tick_spinner(self):
        self._spinner_after = None
        if self.closed or not self.pending or not self.spinner_visible:
            return
        self.spinner_angle = (self.spinner_angle + 24) % 360
        if self.root is not None:
            self.canvas.itemconfigure(self.spinner_item, start=self.spinner_angle, state="normal")
            self._spinner_after = self.root.after(60, self._tick_spinner)

    def _stop_spinner(self):
        self.spinner_visible = False
        if self.root is not None and hasattr(self, "spinner_item"):
            if self._spinner_after is not None:
                self.root.after_cancel(self._spinner_after)
            self.canvas.itemconfigure(self.spinner_item, state="hidden")
        self._spinner_after = None

    def _render_selection(self, rect):
        if self.root is None:
            return
        if rect is None:
            self.canvas.itemconfigure(self.selected_item, state="hidden")
            self.canvas.itemconfigure(self.outline_item, state="hidden")
            return
        from PIL import ImageTk
        vx, vy = self.virtual[:2]
        local = (rect[0] - vx, rect[1] - vy, rect[2] - vx, rect[3] - vy)
        self.selection_photo = ImageTk.PhotoImage(self.desktop.crop(local), master=self.root)
        self.canvas.coords(self.selected_item, *local[:2])
        self.canvas.itemconfigure(self.selected_item, image=self.selection_photo, state="normal")
        self.canvas.coords(self.outline_item, *local)
        self.canvas.itemconfigure(self.outline_item, state="normal", width=max(SELECTION_WIDTH, round(SELECTION_WIDTH * self.scale)))

    def _layout_bubbles(self):
        key = (self.question_bubble_bounds, self.answer_bubble_bounds, self.scale,
               self.answer_visible, self.waiting_visible)
        if key == self._layout_key:
            return
        self._layout_key = key
        if self.panel_bounds is None:
            self.canvas.itemconfigure("bubble", state="hidden")
            return
        self.canvas.itemconfigure("question", state="normal")
        self.canvas.itemconfigure("answer", state="normal" if self.answer_visible else "hidden")
        self.canvas.itemconfigure(self.waiting_item, state="normal" if self.waiting_visible else "hidden")
        vx, vy = self.virtual[:2]
        for bounds, shape, widget, item in (
            (self.question_bubble_bounds, self.question_shape, self.question_widget, self.question_item),
            (self.answer_bubble_bounds, self.answer_shape, self.answer_widget, self.answer_item),
        ):
            l, t, r, b = bounds
            l, t, r, b = l - vx, t - vy, r - vx, b - vy
            pad = min(round(CONTENT_PADDING * self.scale), max(2, (b - t) // 8), max(2, (r - l) // 8))
            radius = min(round(RADIUS * self.scale), pad)
            points = [l + radius, t, r - radius, t, r, t, r, t + radius,
                      r, b - radius, r, b, r - radius, b, l + radius, b,
                      l, b, l, b - radius, l, t + radius, l, t]
            self.canvas.coords(shape, *points)
            font_size = max(8, min(round(BODY_SIZE * self.scale), (b - t) // 4))
            is_answer = item == self.answer_item
            title_height = font_size + max(2, round(4 * self.scale)) if is_answer else 0
            widget.configure(font=(FONT_FAMILY, -font_size))
            extra = max(8, round(SCROLLBAR_WIDTH * self.scale)) if is_answer else 0
            self.canvas.coords(item, l + pad, t + pad + title_height)
            self.canvas.itemconfigure(item, width=max(1, r - l - 2 * pad - extra),
                                      height=max(1, b - t - 2 * pad - title_height))
            if not is_answer:
                self.question_hint.configure(font=(FONT_FAMILY, -font_size))
            else:
                from tkinter.font import Font
                header_font = Font(root=self.root, family=FONT_FAMILY, size=-font_size, weight="bold")
                diameter = min(round(16 * self.scale), title_height)
                title = fit_caption(self.model_caption, max(0, r - l - 3 * pad - diameter), header_font.measure)
                self.canvas.coords(self.answer_caption, l + pad, t + pad)
                self.canvas.itemconfigure(self.answer_caption, text=title,
                                          font=(FONT_FAMILY, -font_size, "bold"))
                self.canvas.coords(self.scrollbar_item, r - pad - extra, t + pad + title_height)
                self.canvas.itemconfigure(self.scrollbar_item, width=extra,
                                          height=max(1, b - t - 2 * pad - title_height))
                if self.waiting_visible:
                    self.canvas.coords(self.spinner_item, l + pad, t + pad, l + pad + diameter, t + pad + diameter)
                else:
                    self.canvas.coords(self.spinner_item, r - pad - diameter, t + pad,
                                       r - pad, t + pad + diameter)
                self.canvas.coords(self.waiting_item, l + 2 * pad + diameter, t + pad)
                self.canvas.itemconfigure(self.waiting_item, font=(FONT_FAMILY, -font_size))
                self.canvas.itemconfigure(self.spinner_item, width=max(2, round(2 * self.scale)))
        self.canvas.itemconfigure(self.spinner_item, state="normal" if self.spinner_visible else "hidden")

    def _sync_hint(self, event=None):
        if self.root is None or not hasattr(self, "question_hint"):
            return
        self.hint_visible = not bool(self.question_widget.get("1.0", "end-1c")) and not self.pending
        if self.hint_visible:
            self.question_hint.place(x=2, y=2)
        else:
            self.question_hint.place_forget()
        if self.question_widget.edit_modified():
            self.question_widget.edit_modified(False)

    def _fit_answer_height(self):
        if self._markdown_source != self.answer:
            self._answer_document = parse_markdown(self.answer)
            self._markdown_source = self.answer
        if self.question_bubble_bounds is None or self.panel_bounds is None:
            return
        pixel_size = max(8, round(BODY_SIZE * self.scale))
        line_height = math.ceil(pixel_size * 1.4)
        measure = lambda value: len(value) * pixel_size * 0.6
        if hasattr(self, "_answer_font"):
            self._answer_font.configure(size=-pixel_size)
            measure = self._answer_font.measure
            line_height = self._answer_font.metrics("linespace")
        width = max(1, self.panel_bounds[2] - self.panel_bounds[0]
                    - round((2 * CONTENT_PADDING + SCROLLBAR_WIDTH) * self.scale) - 6)
        lines = measure_wrapped_lines(
            self._answer_document.text, width, measure,
            max_lines=max(1, math.ceil(MAX_ANSWER_HEIGHT * self.scale / line_height)),
        )
        rect = compute_answer_bounds(
            self.question_bubble_bounds, self.panel_bounds,
            answer_content_height(lines, line_height, self.scale), self.scale,
        )
        if rect is not None:
            self.answer_bubble_bounds = rect

    def _sync_panel(self):
        self._fit_answer_height()
        if self.root is None:
            return
        self._layout_bubbles()
        self.question_widget.configure(state="normal")
        if self._question_needs_sync:
            self.question_widget.delete("1.0", "end")
            self.question_widget.insert("1.0", self.question)
            self._question_needs_sync = False
        self.question_widget.configure(state="normal" if self.editing and not self.pending else "disabled")
        self._sync_hint()
        if self._answer_renderer is not None:
            self._answer_renderer.render(self._answer_document, max(8, round(BODY_SIZE * self.scale)))

    def _poll(self):
        if self.closed:
            return
        if self.stop_event is not None and self.stop_event.is_set():
            self.close()
            return
        if self.focus_event is not None and self.focus_event.is_set():
            self.focus_event.clear()
            self.root.lift()
            self.root.focus_force()
        self.drain_events()
        self.root.after(100, self._poll)

    def run(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("overlay must run on the main thread")
        import tkinter as tk
        from PIL import ImageTk
        callback_error = None

        def on_callback_error(exc_type, exc, traceback):
            nonlocal callback_error
            callback_error = exc
            self.close()

        try:
            l, t, r, b = self.virtual
            if self.desktop.size != (r - l, b - t):
                raise RuntimeError("desktop geometry changed during capture")
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.geometry(f"{r - l}x{b - t}+0+0")
            self.canvas = tk.Canvas(self.root, highlightthickness=0, cursor="crosshair")
            self.canvas.pack(fill="both", expand=True)
            self.dimmed_photo = ImageTk.PhotoImage(ImageEnhance.Brightness(self.desktop).enhance(0.35), master=self.root)
            self.canvas.create_image(0, 0, image=self.dimmed_photo, anchor="nw")
            self.selected_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
            self.outline_item = self.canvas.create_rectangle(0, 0, 0, 0, outline=ACCENT, width=SELECTION_WIDTH, state="hidden")
            self.question_shape = self.canvas.create_polygon(0, 0, 0, 0, fill=QUESTION_BG, outline="", smooth=True, tags=("bubble", "question"))
            self.answer_shape = self.canvas.create_polygon(0, 0, 0, 0, fill=ANSWER_BG, outline="", smooth=True, tags=("bubble", "answer"))
            self.answer_caption = self.canvas.create_text(0, 0, text=self.model_caption, anchor="nw", fill=CAPTION_COLOR, tags=("bubble", "answer"))
            self.waiting_item = self.canvas.create_text(0, 0, text="Rena Thinking...", anchor="nw", fill=TEXT_COLOR, tags="bubble")
            self.question_widget = tk.Text(self.canvas, height=1, width=1, wrap="word", undo=True,
                                           bg=QUESTION_BG, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                                           selectbackground=ACCENT, selectforeground=TEXT_COLOR,
                                           relief="flat", borderwidth=0, highlightthickness=0)
            self.question_hint = tk.Label(self.question_widget, text="Ask something...", bg=QUESTION_BG,
                                           fg=CAPTION_COLOR, borderwidth=0, padx=0, pady=0, anchor="nw")
            self.question_hint.bind("<ButtonPress-1>", lambda e: self.question_widget.focus_set())
            self.question_widget.bind("<<Modified>>", self._sync_hint)
            self.question_widget.bind("<FocusIn>", self._sync_hint)
            self.answer_widget = tk.Text(self.canvas, width=1, height=1, wrap="word", state="disabled",
                                         bg=ANSWER_BG, fg=TEXT_COLOR, selectbackground=ACCENT, selectforeground=TEXT_COLOR,
                                         relief="flat", borderwidth=0, highlightthickness=0)
            self._answer_renderer = TkMarkdownRenderer(self.answer_widget)
            from tkinter.font import Font
            self._answer_font = Font(root=self.root, family=FONT_FAMILY, size=-BODY_SIZE)
            from tkinter import ttk
            # Clam paints configured colors on Windows instead of native system colors.
            self.scrollbar_style = ttk.Style(self.root)
            style_name = configure_tk_scrollbar(self.scrollbar_style)
            self.scrollbar = ttk.Scrollbar(self.canvas, orient="vertical", command=self.answer_widget.yview,
                                           style=style_name)
            self.answer_widget.configure(yscrollcommand=self.scrollbar.set)
            self.question_item = self.canvas.create_window(0, 0, window=self.question_widget, anchor="nw", tags=("bubble", "question"))
            self.answer_item = self.canvas.create_window(0, 0, window=self.answer_widget, anchor="nw", tags=("bubble", "answer"))
            self.scrollbar_item = self.canvas.create_window(0, 0, window=self.scrollbar, anchor="nw", tags=("bubble", "answer"))
            self.spinner_item = self.canvas.create_arc(0, 0, 1, 1, style="arc", extent=250, outline=ACCENT, tags="bubble")
            self.canvas.itemconfigure("bubble", state="hidden")
            self.question_widget.bind("<Return>", self.handle_enter)
            self.question_widget.bind("<KP_Enter>", self.handle_enter)
            self.question_widget.bind("<KeyRelease-Return>", self.release_enter)
            self.question_widget.bind("<KeyRelease-KP_Enter>", self.release_enter)
            self.question_widget.bind("<FocusOut>", self.release_enter)
            self.root.bind("<ButtonPress-1>", lambda e: self.begin_selection(e.x_root, e.y_root))
            self.root.bind("<B1-Motion>", lambda e: self.drag_selection(e.x_root, e.y_root))
            self.root.bind("<ButtonRelease-1>", lambda e: self.finish_selection(e.x_root, e.y_root))
            self.root.bind("<Escape>", lambda e: self.close())
            self.root.protocol("WM_DELETE_WINDOW", self.close)
            self.root.report_callback_exception = on_callback_error
            self.root.update_idletasks()
            self.root.deiconify()
            self.native.place_overlay(self.root)
            self.root.wait_visibility()
            self.root.grab_set_global()
            self.root.focus_force()
            self.root.after(100, self._poll)
            self.root.mainloop()
            if callback_error is not None:
                raise callback_error
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.waiting_visible = False
        self.generation += 1
        try:
            if self.on_close is not None:
                self.on_close()
        finally:
            try:
                try:
                    self._stop_spinner()
                finally:
                    if self.root is not None:
                        try:
                            # A reused worker must not inherit Tcl timers from the previous root.
                            for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                                self.root.after_cancel(timer)
                        finally:
                            self.root.destroy()
            finally:
                try:
                    if self.runner is not None:
                        try:
                            self.runner.cancel(self.request)
                        finally:
                            self.runner.close()
                finally:
                    self.desktop.close()


def run_screenshot_overlay(provider, stop_event=None, focus_event=None, *, on_close=None):
    from interfaces.tk_lifecycle import ui_thread_gc

    with ui_thread_gc():
        overlay = ScreenshotOverlay(provider, stop_event, focus_event, on_close=on_close)
        try:
            overlay.run()
        finally:
            overlay.close()
            # Drop the owner before the main-thread cyclic collection at scope exit.
            del overlay

