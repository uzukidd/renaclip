"""Warm, reusable screenshot-overlay worker and its bounded command mailbox."""

import json
import multiprocessing
from queue import Empty, Full

from interfaces.tk_lifecycle import ui_thread_gc, collect_ui_cycles


_PROVIDER_CAPACITY = 256 * 1024


def launch_screenshot_qa(provider, stop_event=None, focus_event=None, on_close=None):
    from interfaces.screenshot_overlay import run_screenshot_overlay

    kwargs = {"stop_event": stop_event, "focus_event": focus_event}
    if on_close is not None:
        kwargs["on_close"] = on_close
    run_screenshot_overlay(provider, **kwargs)


def _preload_overlay():
    # Importing these modules does not capture a screen or create a Tk root.
    import tkinter
    from PIL import ImageTk
    from interfaces.screenshot_overlay import run_screenshot_overlay
    return run_screenshot_overlay


def _read_provider(buffer, length):
    return json.loads(bytes(buffer[:length.value]).decode("utf-8"))


def _overlay_worker(commands, buffer, length, state_lock, stop_event,
                    focus_event, active_event, pending_event, ready_event):
    # Keep automatic GC disabled across sessions, including late network cleanup.
    with ui_thread_gc():
        _overlay_worker_loop(commands, buffer, length, state_lock, stop_event,
                             focus_event, active_event, pending_event, ready_event)


def _overlay_worker_loop(commands, buffer, length, state_lock, stop_event,
                         focus_event, active_event, pending_event, ready_event):
    run_overlay = _preload_overlay()
    ready_event.set()

    def overlay_closed():
        # Called before network cleanup, allowing one reopen to queue immediately.
        with state_lock:
            active_event.clear()

    try:
        while not stop_event.is_set():
            try:
                commands.get(timeout=0.2)
            except Empty:
                collect_ui_cycles()
                continue
            with state_lock:
                if stop_event.is_set():
                    break
                provider = _read_provider(buffer, length)
                pending_event.clear()
                focus_event.clear()
                active_event.set()
            try:
                run_overlay(provider, stop_event=stop_event, focus_event=focus_event,
                            on_close=overlay_closed)
            except Exception as exc:
                # Keep the warm process reusable; do not log provider response bodies.
                print(f"[Screenshot Q&A] Overlay failed ({type(exc).__name__}). Retry the shortcut.", flush=True)
            finally:
                overlay_closed()
                collect_ui_cycles()
    finally:
        active_event.clear()
        ready_event.clear()


class ScreenshotQAController:
    """One warm child, one active overlay, and at most one latest pending request.

    warmup/start are quick event-loop operations. close may join the child and
    should be called with asyncio.to_thread by the service on shutdown.
    """

    def __init__(self):
        self.process = None
        self.stop_event = self.focus_event = None
        self.active_event = self.pending_event = self.ready_event = None
        self.commands = None
        self.provider_buffer = self.provider_length = self.state_lock = None
        self._closing = False

    def _release_commands(self):
        if self.commands is not None:
            self.commands.cancel_join_thread()
            self.commands.close()
            self.commands = None

    def warmup(self):
        """Preload a worker without taking a screenshot or displaying a window."""
        if self._closing:
            return
        if self.process is not None:
            if self.process.is_alive():
                return
            self.process.join(timeout=0)
            self.process.close()
            self.process = None
            self._release_commands()
        context = multiprocessing.get_context("spawn")
        self.stop_event, self.focus_event = context.Event(), context.Event()
        self.active_event, self.pending_event, self.ready_event = context.Event(), context.Event(), context.Event()
        self.state_lock = context.Lock()
        self.provider_buffer = context.Array("B", _PROVIDER_CAPACITY, lock=False)
        self.provider_length = context.Value("I", 0, lock=False)
        # Queue only a wakeup token. The shared mailbox always contains the latest
        # provider, even if a newer shortcut arrives before Queue's feeder runs.
        self.commands = context.Queue(maxsize=1)
        self.process = context.Process(
            target=_overlay_worker,
            args=(self.commands, self.provider_buffer, self.provider_length, self.state_lock,
                  self.stop_event, self.focus_event, self.active_event,
                  self.pending_event, self.ready_event),
            name="renaclip-screenshot-worker",
        )
        try:
            self.process.start()
        except Exception:
            self.process.close()
            self.process = None
            self._release_commands()
            raise

    def start(self, provider):
        if self._closing:
            return
        payload = json.dumps(provider, ensure_ascii=False).encode("utf-8")
        if len(payload) > _PROVIDER_CAPACITY:
            raise ValueError("Screenshot provider configuration is too large")
        self.warmup()
        with self.state_lock:
            if self.active_event.is_set():
                self.focus_event.set()
                return
            self.provider_buffer[:len(payload)] = payload
            self.provider_length.value = len(payload)
            if self.pending_event.is_set():
                return
            self.pending_event.set()
            try:
                self.commands.put_nowait(True)
            except Full:
                pass

    def close(self):
        if self.process is None or self._closing:
            return
        self._closing = True
        try:
            self.stop_event.set()
            self.process.join(timeout=4)
            if self.process.is_alive():
                import psutil
                try:
                    children = psutil.Process(self.process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in reversed(children):
                    try:
                        child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                self.process.terminate()
                self.process.join(timeout=2)
            if not self.process.is_alive():
                self.process.close()
                self.process = None
                self._release_commands()
        finally:
            self._closing = False
