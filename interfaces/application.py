"""Single Qt application: windows, tray and hooks; async requests run off the UI."""
import asyncio
import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, QLockFile, QStandardPaths
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox

import backend
from config_loader import load_config, get_active_openai_provider
from constants import CONFIG_PATH, DATA_DIR, TRAY_ICON_PATH
from interfaces.material_window import MainWindow
from interfaces.qt_async import AsyncExecutor
from interfaces.screenshot_qa import ScreenshotQAController
from interfaces.theme import apply_qt_theme


class Runtime(QObject):
    skill_requested = Signal(int)
    screenshot_requested = Signal()
    notification = Signal(str, str)

    def __init__(self, app, *, hooks=True, show=True):
        super().__init__()
        self.app = app
        self.executor = AsyncExecutor(self)
        self.window = MainWindow(executor=self.executor)
        self.screenshot = ScreenshotQAController(self.executor, self)
        self.handles = []
        self.hooks_enabled = hooks
        self.closed = False
        self.skill_requested.connect(self.run_skill)
        self.screenshot_requested.connect(self.run_screenshot)
        self.notification.connect(self.notify)
        backend._notification_handler = self.notification.emit
        self.tray = QSystemTrayIcon(QIcon(str(TRAY_ICON_PATH)), self)
        self.tray.setToolTip("RenaClip")
        menu = QMenu()
        menu.addAction("Open RenaClip", self.show_window)
        menu.addAction("Screenshot Q&A", self.run_screenshot)
        menu.addSeparator()
        menu.addAction("Exit", app.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window()
                                   if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.window.hide_on_close = QSystemTrayIcon.isSystemTrayAvailable()
        if self.window.hide_on_close:
            self.tray.show()
        else:
            app.setQuitOnLastWindowClosed(True)
        self.window.configuration_changed.connect(self.reload_config)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.check_config)
        self.timer.start()
        self.signature = None
        self.mtime = None
        self.reload_config()
        if show:
            self.show_window()
        app.aboutToQuit.connect(self.close)

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def notify(self, title, message):
        logging.info("%s: %s", title, message)
        if self.tray.isVisible():
            self.tray.showMessage(title, message)
        else:
            self.window.statusBar().showMessage(message, 10000)

    def check_config(self):
        value = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else None
        if value != self.mtime:
            self.reload_config()
            self.window.refresh()

    def reload_config(self):
        self.state = backend.RenaClipApp.load_config(CONFIG_PATH)
        self.state.openai_chats = [dict(spec) for spec in self.state.specs]
        self.mtime = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else None
        signature = (self.state.hotkey_modifier, self.state.screenshot_qa_enabled)
        if signature != self.signature and self.hooks_enabled:
            import keyboard
            for handle in self.handles:
                keyboard.remove_hotkey(handle)
            self.handles = []
            try:
                self.handles = backend.register_app_hotkeys(
                    keyboard, self.state.hotkey_modifier, self.skill_requested.emit,
                    self.screenshot_requested.emit if self.state.screenshot_qa_enabled else None,
                )
            except Exception:
                logging.exception("Global hotkey registration failed")
                self.notify("RenaClip", "Could not register shortcuts. Check keyboard permissions.")
            else:
                self.signature = signature

    def run_screenshot(self):
        if not self.state.screenshot_qa_enabled or self.closed:
            return
        _, settings = load_config()
        provider = dict(get_active_openai_provider(settings))
        provider.update(web_search=self.state.screenshot_qa_web_search,
                        reasoning_effort=self.state.openai_reasoning_effort)
        try:
            self.screenshot.start(provider)
        except Exception:
            logging.exception("Screenshot window failed")
            self.notify("RenaClip", "Could not capture this desktop. Try again.")

    def run_skill(self, index):
        if self.closed or index >= len(self.state.specs):
            return
        # Clipboard access stays on the Qt UI thread, including under async callbacks.
        text = self.app.clipboard().text()
        if not text.strip():
            self.notify("RenaClip", "Clipboard is empty.")
            return
        from copy import deepcopy
        state = deepcopy(self.state)
        spec = state.specs[index]
        self.notify("RenaClip", f"Processing with {spec['name']}...")
        self.executor.submit(self._request(state, index, text),
                             on_result=lambda result: self._copy_result(result, spec['name']),
                             on_error=lambda exc: self.notify("RenaClip", "Request failed. See renaclip.log."))

    def set_clipboard(self, text):
        if not self.closed:
            self.app.clipboard().setText(text)

    def _copy_result(self, result, name):
        self.app.clipboard().setText(result)
        self.notify("RenaClip", f"Clipboard updated by {name}.")

    async def _request(self, state, index, text):
        if state.backend == "openai":
            from openai_client import get_openai_client
            from config_loader import reasoning_request_options
            client = get_openai_client(state.openai_api_key, state.openai_base_url)
            if client is None:
                raise RuntimeError("No OpenAI client")
            spec = state.specs[index]
            try:
                prompt = spec.get("prompt", "").strip()
                params = {"model": state.openai_model}
                params.update(reasoning_request_options(state.openai_api_interface, state.openai_reasoning_effort))
                if spec.get("web_search"):
                    params["tools"] = [{"type": "web_search"}]
                if state.openai_api_interface == "responses":
                    params.update(input=text, store=False)
                    if prompt:
                        params["instructions"] = prompt
                    if state.openai_streaming:
                        stream = await client.responses.create(**params, stream=True)
                        parts = []
                        try:
                            async for event in stream:
                                if event.type in ("error", "response.failed", "response.incomplete"):
                                    raise RuntimeError("Response did not complete")
                                if event.type == "response.output_text.delta":
                                    parts.append(event.delta)
                                    self.executor.post(self.set_clipboard, "".join(parts))
                        finally:
                            await stream.close()
                        return "".join(parts)
                    return (await client.responses.create(**params)).output_text
                params["messages"] = ([{"role": "system", "content": prompt}] if prompt else []) + [{"role": "user", "content": text}]
                if state.openai_streaming:
                    stream = await client.chat.completions.create(**params, stream=True)
                    parts = []
                    try:
                        async for chunk in stream:
                            if chunk.choices and chunk.choices[0].delta.content:
                                parts.append(chunk.choices[0].delta.content)
                                self.executor.post(self.set_clipboard, "".join(parts))
                    finally:
                        await stream.close()
                    return "".join(parts)
                return (await client.chat.completions.create(**params)).choices[0].message.content or ""
            finally:
                await client.close()
        from gemini_client import get_or_create_gem, update_gem, delete_chat_after, RENACLIP_PREFIX
        client = await backend.initialize_client(state)
        chat = None
        try:
            spec = state.specs[index]
            gem, _ = await get_or_create_gem(client, name=RENACLIP_PREFIX + spec["name"],
                                           prompt=spec.get("prompt", ""), description=spec.get("description", ""))
            gem = await update_gem(client, gem, name=RENACLIP_PREFIX + spec["name"],
                                   prompt=spec.get("prompt", ""), description=spec.get("description", ""))
            chat = client.start_chat(gem=gem, model=state.model)
            return (await chat.send_message(text)).text or ""
        finally:
            if chat is not None:
                await delete_chat_after(client, chat)
            await client.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.screenshot.close()
        if self.handles:
            import keyboard
            for handle in self.handles:
                keyboard.remove_hotkey(handle)
        self.handles.clear()
        self.executor.close()
        self.window.dispose()
        self.tray.hide()
        backend._notification_handler = None


def run(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="RenaClip Qt desktop application")
    parser.add_argument("--no-hotkeys", action="store_true", help="Disable hooks for diagnostics")
    parser.add_argument("--hidden", action="store_true", help="Start minimized to tray")
    parser.add_argument("--smoke-test", action="store_true", help="Validate startup and exit without API calls")
    args = parser.parse_args(argv)
    logging.basicConfig(filename=str(DATA_DIR / "renaclip.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RenaClip")
    app.setQuitOnLastWindowClosed(False)
    apply_qt_theme(app)
    lock = QLockFile(str(DATA_DIR / "renaclip-instance.lock"))
    lock.setStaleLockTime(0)
    if not args.smoke_test and not lock.tryLock(100):
        QMessageBox.information(None, "RenaClip", "RenaClip is already running. Open it from the tray.")
        return 0
    runtime = Runtime(app, hooks=not (args.no_hotkeys or args.smoke_test), show=not args.hidden)
    if args.smoke_test:
        QTimer.singleShot(800, app.quit)
    result = app.exec()
    runtime.close()
    if lock.isLocked():
        lock.unlock()
    return result
