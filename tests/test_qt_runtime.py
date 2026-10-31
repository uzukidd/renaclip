import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import backend

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from interfaces.qt_async import AsyncExecutor
from interfaces.application import Runtime
import config_loader


class QtRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_executor_dispatches_to_main_thread_and_closes(self):
        executor = AsyncExecutor()
        seen = []
        async def job():
            return "done"
        executor.submit(job(), on_result=lambda value: seen.append((value, QThread.currentThread())))
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(seen[0][0], "done")
        self.assertEqual(seen[0][1], self.app.thread())
        executor.close()
        self.assertFalse(executor.thread.is_alive())

    def test_runtime_chat_reuses_window_after_close(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            with patch.object(config_loader, 'CONFIG_PATH', path), \
                    patch('interfaces.application.CONFIG_PATH', path), \
                    patch('interfaces.application.DATA_DIR', Path(folder)):
                config_loader.save_config([], {'OPENAI_PROVIDERS': [
                    {'name': 'Responses', 'api_interface': 'responses', 'model': 'vision'}]})
                runtime = Runtime(self.app, hooks=False, show=False)
                try:
                    runtime.run_chat()
                    first = runtime.chat_window
                    self.assertTrue(first.isVisible())
                    first.input.setPlainText('draft')
                    first.close()
                    runtime.run_chat()
                    self.assertIs(runtime.chat_window, first)
                    self.assertEqual(first.input.toPlainText(), 'draft')
                finally:
                    runtime.close()
                    runtime.window.hide_on_close = False
                    runtime.window.close()
                self.assertTrue(first.disposed)

    def test_hotkey_slots_callbacks_and_registration_cleanup(self):
        for screenshot_enabled in (False, True):
            for chat_enabled in (False, True):
                keyboard = Mock()
                keyboard.add_hotkey.side_effect = lambda chord, callback, **kw: chord
                skill, screenshot, chat = Mock(), Mock(), Mock()
                handles = backend.register_app_hotkeys(
                    keyboard, "ctrl+shift", skill,
                    screenshot if screenshot_enabled else None,
                    chat if chat_enabled else None)
                expected = [f"ctrl+shift+{i}" for i in range(1, 9)]
                if screenshot_enabled:
                    expected.append("ctrl+shift+0")
                if chat_enabled:
                    expected.append("ctrl+shift+9")
                self.assertEqual(handles, expected)
                for index, call in enumerate(keyboard.add_hotkey.call_args_list[:8]):
                    call.args[1]()
                    skill.assert_called_with(index)
                if chat_enabled:
                    keyboard.add_hotkey.call_args_list[-1].args[1]()
                    chat.assert_called_once_with()
                if screenshot_enabled:
                    call = keyboard.add_hotkey.call_args_list[8]
                    self.assertFalse(call.kwargs["trigger_on_release"])
                    call.args[1]()
                    screenshot.assert_called_once_with()
        keyboard = Mock()
        keyboard.add_hotkey.side_effect = [str(i) for i in range(8)] + [RuntimeError("denied")]
        with self.assertRaises(RuntimeError):
            backend.register_app_hotkeys(keyboard, "ctrl", Mock(), None, Mock())
        self.assertEqual(keyboard.remove_hotkey.call_count, 8)

    def test_chat_toggle_reregisters_hides_preserves_and_guards_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            with patch.object(config_loader, "CONFIG_PATH", path), \
                    patch("interfaces.application.CONFIG_PATH", path), \
                    patch("interfaces.application.DATA_DIR", Path(folder)), \
                    patch("keyboard.add_hotkey", side_effect=lambda chord, callback, **kw: chord), \
                    patch("keyboard.remove_hotkey") as remove:
                skills = [{"name": f"Skill {i}"} for i in range(10)]
                config_loader.save_config(skills, {})
                runtime = Runtime(self.app, hooks=True, show=False)
                try:
                    self.assertTrue(runtime.state.continuous_chat_enabled)
                    self.assertIn("ctrl+9", runtime.handles)
                    self.assertNotIn("ctrl+-", runtime.handles)
                    runtime.run_chat()
                    first = runtime.chat_window
                    first.input.setPlainText("preserved draft")
                    with patch.object(first, "shutdown") as shutdown:
                        config_loader.save_config(skills, {"CONTINUOUS_CHAT_ENABLED": False})
                        runtime.reload_config()
                        self.assertFalse(first.isVisible())
                        self.assertFalse(first.disposed)
                        shutdown.assert_not_called()
                    self.assertFalse(runtime.chat_action.isEnabled())
                    self.assertNotIn("ctrl+9", runtime.handles)
                    self.assertEqual(remove.call_count, 9)
                    runtime.chat_requested.emit()
                    runtime.chat_action.trigger()
                    runtime.run_chat()
                    self.assertFalse(first.isVisible())
                    with patch.object(runtime.app, "clipboard") as clipboard:
                        for index in (-1, 8, 9, 10):
                            runtime.run_skill(index)
                        clipboard.assert_not_called()
                    config_loader.save_config(skills, {"CONTINUOUS_CHAT_ENABLED": True})
                    runtime.reload_config()
                    self.assertTrue(runtime.chat_action.isEnabled())
                    self.assertIn("ctrl+9", runtime.handles)
                    runtime.chat_action.trigger()
                    self.assertIs(runtime.chat_window, first)
                    self.assertTrue(first.isVisible())
                    self.assertEqual(first.input.toPlainText(), "preserved draft")
                finally:
                    runtime.close()
                    runtime.window.hide_on_close = False
                    runtime.window.close()
                self.assertTrue(first.disposed)

    def test_runtime_config_and_screenshot_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            with patch.object(config_loader, 'CONFIG_PATH', path), \
                    patch('interfaces.application.CONFIG_PATH', path):
                config_loader.save_config([{'name':'Skill','prompt':''}], {
                    'SCREENSHOT_QA_ENABLED': True, 'BACKEND':'openai',
                    'OPENAI_REASONING_EFFORT':'high', 'SCREENSHOT_QA_WEB_SEARCH':True,
                })
                runtime = Runtime(self.app, hooks=False, show=False)
                try:
                    self.assertEqual(runtime.state.openai_reasoning_effort, 'high')
                    with patch('interfaces.screenshot_overlay.capture_desktop') as grab:
                        from PySide6.QtGui import QPixmap
                        from PySide6.QtCore import QRect
                        image = QPixmap(800,600)
                        image.fill()
                        grab.return_value = (image,QRect(0,0,800,600))
                        runtime.run_screenshot()
                        first = runtime.screenshot.overlay
                        runtime.run_screenshot()
                        self.assertIs(first, runtime.screenshot.overlay)
                        self.assertTrue(first.provider['web_search'])
                        first.close()
                        self.app.processEvents()
                        self.assertIsNone(runtime.screenshot.overlay)
                        runtime.run_screenshot()
                        self.assertIsNotNone(runtime.screenshot.overlay)
                finally:
                    runtime.close()
                    runtime.window.hide_on_close=False
                    runtime.window.close()
                self.assertFalse(runtime.executor.thread.is_alive())

if __name__=='__main__':
    unittest.main()
