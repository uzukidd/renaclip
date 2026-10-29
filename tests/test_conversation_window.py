import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from interfaces.conversation_window import ConversationWindow
import backend

APP = QApplication.instance() or QApplication([])
PROVIDER = {'name': 'test', 'api_interface': 'responses', 'model': 'vision', 'models': ['vision', 'other'], 'base_url': 'https://example.com/v1'}


class Executor:
    def __init__(self):
        self.calls = []
    def submit(self, coro, **callbacks):
        coro.close()
        future = Future()
        self.calls.append((future, callbacks))
        return future
    def post(self, callback, *args):
        callback(*args)


class ChatWindowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.executor = Executor()
        self.window = ConversationWindow(self.executor, Path(self.folder.name) / 'history.json')
        self.window.configure([PROVIDER])
        self.window.show_chat()
        self.addCleanup(self.window.shutdown)
        APP.processEvents()

    def send(self, text='hello'):
        self.window.input.setPlainText(text)
        self.assertTrue(self.window.send_message())
        return self.executor.calls[-1][1]

    def test_persist_response_chain_and_close_reopen(self):
        callbacks = self.send()
        self.window.close()
        self.assertTrue(self.window.pending)
        callbacks['on_result']({'text': r'Answer \(x^2\)', 'response_id': 'resp_1'})
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.window.state['previous_response_id'], 'resp_1')
        self.window.show_chat()
        self.assertEqual(len(self.window.state['messages']), 2)
        restored = ConversationWindow(self.executor, self.window.store.path)
        self.addCleanup(restored.shutdown)
        restored.configure([PROVIDER])
        self.assertEqual(restored.state['previous_response_id'], 'resp_1')
        self.assertEqual(restored.state['messages'][0]['text'], 'hello')
        self.assertEqual(len(restored._browsers), 2)
        self.assertEqual(len(restored._browsers[1].formula_sources), 1)

    def test_image_model_effort_and_clear_ignore_late_result(self):
        image_path = Path(self.folder.name) / 'sample.png'
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        image.save(str(image_path))
        self.window.add_images([image_path])
        self.window.model_combo.setCurrentText('other')
        self.window.effort_combo.setCurrentText('high')
        callbacks = self.send('describe')
        self.assertEqual(len(self.window._turn['images']), 1)
        self.assertIn('data:image/png;base64,', self.window._turn['images'][0]['data_url'])
        self.window.clear_history()
        self.assertTrue(self.executor.calls[-1][0].cancelled())
        callbacks['on_result']({'text': 'late', 'response_id': 'resp_late'})
        self.assertEqual(self.window.state['messages'], [])
        self.assertIsNone(self.window.state['previous_response_id'])
        self.assertEqual(self.window.store.load()['messages'], [])
        self.assertFalse(self.window.attachments)

    def test_failure_and_stop_restore_draft_without_committing(self):
        callbacks = self.send('draft')
        self.window._delta(self.window.generation, 'partial')
        callbacks['on_error'](RuntimeError('secret'))
        self.assertEqual(self.window.input.toPlainText(), 'draft')
        self.assertEqual(self.window.state['messages'], [])
        self.assertNotIn('secret', self.window.status_label.text())
        self.send('second')
        self.window.stop_request()
        self.assertEqual(self.window.input.toPlainText(), 'second')
        self.assertTrue(self.executor.calls[-1][0].cancelled())

    def test_scroll_history_drag_and_double_click_hide(self):
        self.window.state['messages'] = [{'role': 'assistant', 'text': 'line\n\n' * 30} for _ in range(8)]
        self.window._rebuild_history()
        APP.processEvents()
        self.assertGreater(self.window.scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        original = self.window.pos()
        point = self.window.title_label.rect().center()
        global_point = self.window.title_label.mapToGlobal(point)
        APP.sendEvent(self.window.title_label, QMouseEvent(QEvent.Type.MouseButtonPress, point, global_point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        delta = QPoint(30, 20)
        APP.sendEvent(self.window.title_label, QMouseEvent(QEvent.Type.MouseMove, point + delta, global_point + delta,
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.window.pos(), original + delta)
        QTest.mouseDClick(self.window.title_label, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.isVisible())
        self.window.show_chat()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.window.state['messages']), 8)

    def test_other_provider_requires_clear_before_sending_chain(self):
        callbacks = self.send()
        callbacks['on_result']({'text': 'answer', 'response_id': 'resp_1'})
        self.window.configure([{**PROVIDER, 'name': 'other provider'}])
        self.window.input.setPlainText('next')
        self.assertFalse(self.window.send_message())
        self.assertIn('another provider', self.window.status_label.text())
        self.window.clear_history()
        self.assertTrue(self.window.send_message() is False)  # input cleared
        self.send('new chain')

    def test_final_reply_is_updated_even_when_streaming_text_is_selected(self):
        callbacks = self.send()
        self.window._delta(self.window.generation, 'partial')
        self.window._flush_partial()
        browser = self.window._reply_browser
        browser.selectAll()
        callbacks['on_result']({'text': 'partial plus final answer', 'response_id': 'resp_1'})
        self.assertEqual(browser.toPlainText(), 'partial plus final answer')
        self.send('next')
        self.assertEqual(browser.toPlainText(), 'partial plus final answer')

    def test_shutdown_restores_draft_and_images_after_restart(self):
        self.window.attachments = [{'name': 'x.png', 'data_url': 'data:image/png;base64,YQ=='}]
        self.send('unfinished question')
        self.window.shutdown()
        restored = ConversationWindow(self.executor, self.window.store.path)
        self.addCleanup(restored.shutdown)
        self.assertEqual(restored.input.toPlainText(), 'unfinished question')
        self.assertEqual(len(restored.attachments), 1)
        self.assertEqual(restored.state['messages'], [])

    def test_minus_hotkey_registration_and_cleanup_on_failure(self):
        keyboard = Mock()
        keyboard.add_hotkey.side_effect = lambda chord, callback, **kw: chord
        callback = Mock()
        handles = backend.register_app_hotkeys(keyboard, 'ctrl+shift', Mock(), None, callback)
        self.assertIn('ctrl+shift+-', handles)
        entry = keyboard.add_hotkey.call_args_list[-1]
        self.assertIs(entry.args[1], callback)
        keyboard.add_hotkey.side_effect = [str(i) for i in range(9)] + [RuntimeError('denied')]
        with self.assertRaises(RuntimeError):
            backend.register_app_hotkeys(keyboard, 'ctrl', Mock(), None, callback)
        self.assertEqual(keyboard.remove_hotkey.call_count, 9)


if __name__ == '__main__':
    unittest.main()
