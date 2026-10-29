import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
