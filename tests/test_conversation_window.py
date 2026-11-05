import os
import base64
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QImage, QMouseEvent, QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from interfaces.conversation_window import ConversationWindow, ImageThumbnail
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

    def test_paste_clipboard_image_as_attachment_and_preserve_text_paste(self):
        self.addCleanup(APP.clipboard().clear)
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.green)
        APP.clipboard().setImage(image)
        self.window.input.setPlainText('Describe this: ')
        QTest.keyClick(self.window.input, Qt.Key.Key_End)
        QTest.keyClick(self.window.input, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.window.attachments), 1)
        self.assertEqual(self.window.input.toPlainText(), 'Describe this: ')
        attachment = self.window.attachments[0]
        self.assertEqual(attachment['name'], 'clipboard-1.png')
        decoded = QImage.fromData(base64.b64decode(attachment['data_url'].split(',', 1)[1]))
        self.assertEqual(decoded.size(), image.size())
        self.assertEqual(decoded.pixelColor(0, 0), image.pixelColor(0, 0))
        self.assertEqual(len(self.window.store.load()['draft_images']), 1)
        APP.clipboard().setText('ordinary text')
        self.window.input.paste()
        self.assertEqual(self.window.input.toPlainText(), 'Describe this: ordinary text')
        self.send('question')
        self.assertEqual(self.window._turn['images'], [attachment])

    def test_thumbnail_hover_remove_and_sent_images_above_bubble(self):
        image = QImage(80, 60, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.window.add_clipboard_image(image)
        self.window.add_clipboard_image(image)
        APP.processEvents()
        thumbnails = [self.window.attachment_layout.itemAt(i).widget() for i in range(2)]
        self.assertTrue(self.window.attachment_strip.isVisible())
        self.assertFalse(self.window.attachment_label.isVisible())
        self.assertFalse(thumbnails[0].remove_button.isVisible())
        APP.sendEvent(thumbnails[0], QEnterEvent(QPoint(10, 10), QPoint(10, 10), QPoint(10, 10)))
        self.assertTrue(thumbnails[0].remove_button.isVisible())
        QTest.mouseClick(thumbnails[0].remove_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.window.attachments), 1)
        self.assertEqual(len(self.window.store.load()['draft_images']), 1)
        callbacks = self.send('describe this')
        browser = self.window._browsers[-2]
        bubble = browser.parentWidget()
        self.assertEqual(bubble.objectName(), 'userBubble')
        turn = bubble.parentWidget()
        sent = turn.findChildren(ImageThumbnail)
        self.assertEqual(len(sent), 1)
        self.assertIsNot(sent[0].parentWidget(), bubble)
        self.assertFalse(sent[0].removable)
        self.assertEqual(turn.layout().indexOf(bubble), 1)
        self.assertFalse(self.window.attachment_strip.isVisible())

    def test_model_name_fits_and_escape_hides_without_canceling(self):
        name = 'gpt-6.1-super-long-model-name'
        self.window.configure([{**PROVIDER, 'model': name, 'models': [name]}])
        self.window.model_combo.setCurrentText(name)
        APP.processEvents()
        required = self.window.model_combo.fontMetrics().horizontalAdvance(name) + 48
        self.assertGreaterEqual(self.window.model_combo.minimumWidth(), required)
        self.assertEqual(self.window.model_combo.toolTip(), name)
        self.send('question')
        future = self.executor.calls[-1][0]
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.pending)
        self.assertFalse(future.cancelled())
        self.window.show_chat()
        self.assertTrue(self.window.isVisible())

    def test_empty_chat_title_centers_hides_on_send_and_returns_on_clear(self):
        APP.processEvents()
        label = self.window.empty_chat_label
        self.assertEqual(label.text(), 'RenaChat')
        self.assertTrue(label.isVisible())
        self.assertEqual(label.geometry(), self.window.scroll.viewport().rect())
        self.window.resize(740, 720)
        APP.processEvents()
        self.assertEqual(label.geometry(), self.window.scroll.viewport().rect())
        callbacks = self.send('hello')
        self.assertFalse(label.isVisible())
        callbacks['on_result']({'text': 'answer', 'response_id': 'resp_1'})
        self.assertFalse(label.isVisible())
        self.window.clear_history()
        self.assertTrue(label.isVisible())

    def test_user_bubble_width_tracks_text_and_wraps_long_messages(self):
        short = self.window._add_message({'role': 'user', 'text': 'Hi'})
        medium = self.window._add_message({'role': 'user', 'text': 'Hello there'})
        long = self.window._add_message({'role': 'user', 'text': 'A very long question with many words. ' * 20})
        APP.processEvents()
        short_bubble, medium_bubble, long_bubble = [b.parentWidget() for b in (short, medium, long)]
        self.assertLess(short_bubble.width(), medium_bubble.width())
        self.assertLess(medium_bubble.width(), long_bubble.width())
        self.assertLessEqual(long_bubble.width(), round(self.window.width() * 0.78))
        self.assertGreater(long.height(), short.height())
        for bubble in (short_bubble, medium_bubble, long_bubble):
            turn = bubble.parentWidget()
            self.assertEqual(bubble.geometry().right(), turn.contentsRect().right())
        original = long_bubble.width()
        self.window.resize(self.window.width() + 200, self.window.height())
        APP.processEvents()
        self.assertGreater(long_bubble.width(), original)
        self.assertLess(short_bubble.width(), medium_bubble.width())

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

    def test_api_400_does_not_misdiagnose_web_search_as_expired_response(self):
        self.window.state['previous_response_id'] = 'resp_previous'
        self.window.state['provider_identity'] = self.window.state['selected_provider']
        callbacks = self.send('question')
        error = RuntimeError('secret')
        error.status_code = 400
        error.body = {'error': {'param': 'tools', 'message': 'web_search is not supported by this model'}}
        callbacks['on_error'](error)
        self.assertIn('Web search was rejected', self.window.status_label.text())
        self.assertNotIn('expired', self.window.status_label.text())
        self.assertEqual(self.window.state['previous_response_id'], 'resp_previous')
        self.assertEqual(self.window.input.toPlainText(), 'question')

    def test_expired_response_error_keeps_history_and_shows_api_detail(self):
        self.window.state['previous_response_id'] = 'resp_previous'
        self.window.state['provider_identity'] = self.window.state['selected_provider']
        callbacks = self.send('question')
        error = RuntimeError('secret')
        error.status_code = 404
        error.body = {'param': 'previous_response_id', 'message': 'Previous response not found'}
        callbacks['on_error'](error)
        self.assertIn('unavailable or expired', self.window.status_label.text())
        self.assertIn('Previous response not found', self.window.status_label.text())
        self.assertEqual(self.window.state['previous_response_id'], 'resp_previous')

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

    def test_chat_completions_provider_still_exposes_selectable_models(self):
        self.window.configure([{**PROVIDER, 'api_interface': 'chat_completions'}])
        self.assertEqual(self.window.provider_combo.count(), 1)
        self.assertEqual(self.window.model_combo.count(), 2)
        self.window.model_combo.setCurrentIndex(1)
        self.assertEqual(self.window.model_combo.currentText(), 'other')
        self.assertFalse(self.window.model_combo.isEditable())
        self.assertIsNone(self.window.model_combo.lineEdit())
        self.assertTrue(self.window.clear_button.text() == '')
        self.assertFalse(self.window.clear_button.icon().isNull())
        self.assertEqual(self.window.clear_button.toolTip(), 'New conversation')
        self.assertFalse(hasattr(self.window, 'close_button'))
        self.assertEqual(self.window.status_label.text(), '')

    def test_refresh_models_preserves_selection_and_ignores_stale_results(self):
        self.window.refresh_models()
        callback = self.executor.calls[-1][1]['on_result']
        callback(['new-model', 'vision'])
        self.assertNotEqual(self.window.model_combo.findText('new-model'), -1)
        self.assertEqual(self.window.model_combo.currentText(), 'vision')
        self.assertTrue(self.window.refresh_models_button.isEnabled())
        self.window.refresh_models()
        stale = self.executor.calls[-1][1]['on_result']
        self.window.configure([{**PROVIDER, 'name': 'changed'}])
        stale(['wrong-provider-model'])
        self.assertEqual(self.window.model_combo.findText('wrong-provider-model'), -1)

    def test_web_search_preference_persists_and_survives_new_conversation(self):
        self.assertFalse(self.window.web_search_checkbox.isChecked())
        self.window.web_search_checkbox.setChecked(True)
        self.assertTrue(self.window.store.load()['web_search'])
        self.window.clear_history()
        self.assertTrue(self.window.state['web_search'])
        self.send('search question')
        self.assertFalse(self.window.web_search_checkbox.isEnabled())
        self.window.stop_request()
        self.assertTrue(self.window.web_search_checkbox.isEnabled())
        restored = ConversationWindow(self.executor, self.window.store.path)
        self.addCleanup(restored.shutdown)
        self.assertTrue(restored.web_search_checkbox.isChecked())

    def test_drag_edges_and_corners_resize_window(self):
        for edge, movement, expected in (
                ('right', QPoint(50, 0), (50, 0)),
                ('left', QPoint(-30, 0), (30, 0)),
                ('top', QPoint(0, -25), (0, 25)),
                ('bottom', QPoint(0, 35), (0, 35)),
                ('corner', QPoint(40, 30), (40, 30))):
            local = {'right': QPoint(self.window.width() - 2, 100), 'left': QPoint(2, 100),
                     'top': QPoint(100, 2), 'bottom': QPoint(100, self.window.height() - 2),
                     'corner': QPoint(self.window.width() - 2, self.window.height() - 2)}[edge]
            original = self.window.size()
            global_point = self.window.mapToGlobal(local)
            APP.sendEvent(self.window, QMouseEvent(QEvent.Type.MouseButtonPress, local, global_point,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            APP.sendEvent(self.window, QMouseEvent(QEvent.Type.MouseMove, local + movement, global_point + movement,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            APP.sendEvent(self.window, QMouseEvent(QEvent.Type.MouseButtonRelease, local + movement, global_point + movement,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(self.window.width() - original.width(), expected[0])
            self.assertEqual(self.window.height() - original.height(), expected[1])
            self.assertIsNone(self.window._resize_origin)

    def test_nine_hotkey_registration_and_cleanup_on_failure(self):
        keyboard = Mock()
        keyboard.add_hotkey.side_effect = lambda chord, callback, **kw: chord
        callback = Mock()
        handles = backend.register_app_hotkeys(keyboard, 'ctrl+shift', Mock(), None, callback)
        self.assertIn('ctrl+shift+9', handles)
        self.assertEqual(len(handles), 9)
        self.assertNotIn('ctrl+shift+-', handles)
        entry = keyboard.add_hotkey.call_args_list[-1]
        self.assertIs(entry.args[1], callback)
        keyboard.add_hotkey.side_effect = [str(i) for i in range(8)] + [RuntimeError('denied')]
        with self.assertRaises(RuntimeError):
            backend.register_app_hotkeys(keyboard, 'ctrl', Mock(), None, callback)
        self.assertEqual(keyboard.remove_hotkey.call_count, 8)


if __name__ == '__main__':
    unittest.main()
