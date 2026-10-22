"""Real Qt Skill output popup tests; no model requests or browser launches."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication
from interfaces.skill_popup import SkillPopup

APP = QApplication.instance() or QApplication([])


class SkillPopupTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        APP.clipboard().clear()
        APP.processEvents()

    def setUp(self):
        self.popup = SkillPopup('Translate / vision', QPoint(100,100))
        self.addCleanup(self.dispose, self.popup)
        APP.clipboard().setText('original clipboard')

    @staticmethod
    def dispose(popup):
        import shiboken6
        if shiboken6.isValid(popup):
            popup.close()
            shiboken6.delete(popup)
        APP.processEvents()

    def flush(self):
        QTest.qWait(65)
        APP.processEvents()

    def test_immediate_thinking_until_text_and_does_not_change_clipboard(self):
        self.assertTrue(self.popup.isVisible())
        self.assertTrue(self.popup.waiting.isVisible())
        self.assertTrue(self.popup.spinner_timer.isActive())
        angle = self.popup.spinner.angle
        self.popup.append_text('   ')
        QTest.qWait(140)
        APP.processEvents()
        self.assertTrue(self.popup.waiting.isVisible())
        self.assertNotEqual(self.popup.spinner.angle,angle)
        self.assertFalse(self.popup.browser.isVisible())
        self.popup.append_text('**Bold answer**')
        self.flush()
        self.assertTrue(self.popup.isVisible())
        self.assertIn('Bold answer',self.popup.browser.toPlainText())
        self.assertFalse(self.popup.waiting.isVisible())
        self.assertFalse(self.popup.spinner_timer.isActive())
        self.assertNotIn('**',self.popup.browser.toPlainText())
        self.assertGreater(self.popup.browser.document().find('Bold answer').charFormat().fontWeight(),400)
        self.popup.finish('Finished **answer**')
        self.assertEqual(APP.clipboard().text(),'original clipboard')
        self.assertTrue(self.popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.popup.windowFlags() & Qt.WindowType.FramelessWindowHint)
        QTest.mouseClick(self.popup.copy_button,Qt.MouseButton.LeftButton)
        self.assertEqual(APP.clipboard().text(),'Finished answer')

    def test_waiting_close_and_empty_completion_stop_spinner(self):
        request=Mock()
        self.popup.attach_request(request)
        QTest.mouseDClick(self.popup.thinking_label,Qt.MouseButton.LeftButton)
        self.assertTrue(self.popup.is_closed)
        self.assertFalse(self.popup.spinner_timer.isActive())
        request.cancel.assert_called_once()
        popup=SkillPopup('Empty',QPoint(100,100))
        self.addCleanup(self.dispose,popup)
        popup.finish('   ')
        self.assertFalse(popup.spinner_timer.isActive())
        self.assertIn('no text',popup.browser.toPlainText())

    def test_failure_shows_literal_error_and_terminal_stale_updates_ignored(self):
        self.popup.fail('Failed **literal**')
        self.assertTrue(self.popup.isVisible())
        self.assertEqual(self.popup.browser.toPlainText(),'Failed **literal**')
        self.popup.append_text('stale')
        self.popup.finish('stale')
        self.flush()
        self.assertEqual(self.popup.browser.toPlainText(),'Failed **literal**')
        self.assertEqual(APP.clipboard().text(),'original clipboard')

    def test_doubleclick_body_closes_and_cancels_request_once(self):
        request=Mock()
        self.popup.attach_request(request)
        self.popup.append_text('Output')
        self.flush()
        closed=QSignalSpy(self.popup.closed)
        QTest.mouseDClick(self.popup.browser.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(10,10))
        self.assertTrue(self.popup.is_closed)
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(closed.count(),1)
        request.cancel.assert_called_once_with()
        self.popup.append_text('more')
        self.popup.finish('late')
        self.popup.fail('late error')
        self.popup.close()
        self.assertEqual(closed.count(),1)
        late=Mock()
        self.popup.attach_request(late)
        late.cancel.assert_called_once_with()

    def test_header_drag_and_doubleclick_copy_surface(self):
        self.popup.finish('answer')
        self.flush()
        start=self.popup.pos()
        point=QPoint(20,self.popup.title_label.height()//2)
        QTest.mousePress(self.popup.title_label,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(self.popup.title_label,point+QPoint(50,35),30)
        QTest.mouseRelease(self.popup.title_label,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(50,35))
        self.assertNotEqual(self.popup.pos(),start)
        self.assertTrue(self.popup._available.contains(self.popup.geometry()))
        QTest.mouseDClick(self.popup.copy_button,Qt.MouseButton.LeftButton)
        self.assertTrue(self.popup.is_closed)

    def test_near_screen_edge_and_growing_answer_clamped(self):
        self.popup.close()
        area=APP.primaryScreen().availableGeometry()
        self.popup=SkillPopup('Long title '*30,area.bottomRight())
        self.addCleanup(self.dispose, self.popup)
        self.popup.append_text('Short answer')
        self.flush()
        short=self.popup.height()
        self.popup.append_text('\n\n'+'Long streamed output. '*1000)
        self.flush()
        self.assertGreater(self.popup.height(),short)
        self.assertLessEqual(self.popup.width(),440)
        self.assertLessEqual(self.popup.height(),560)
        self.assertTrue(area.contains(self.popup.geometry()))
        self.assertFalse(self.popup.browser.horizontalScrollBar().isVisible())
        self.assertGreater(self.popup.browser.verticalScrollBar().maximum(),0)

    def test_active_selection_survives_stream_and_resumes_after_clear(self):
        self.popup.append_text('Select this output')
        self.flush()
        cursor=self.popup.browser.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(6,QTextCursor.MoveMode.KeepAnchor)
        self.popup.browser.setTextCursor(cursor)
        self.popup.append_text(' with more')
        self.flush()
        self.assertEqual(self.popup.browser.textCursor().selectedText(),'Select')
        self.assertEqual(self.popup.browser.toPlainText(),'Select this output')
        self.popup.finish('Select this output with more and final')
        self.assertEqual(self.popup.browser.textCursor().selectedText(),'Select')
        cursor.clearSelection()
        self.popup.browser.setTextCursor(cursor)
        self.flush()
        self.assertEqual(self.popup.browser.toPlainText(),'Select this output with more and final')

    def test_stream_keeps_user_scroll_and_native_selection_copy(self):
        self.popup.append_text('A line of answer.\n\n'*100)
        self.flush()
        bar=self.popup.browser.verticalScrollBar()
        bar.setValue(0)
        self.popup.append_text('another line\n')
        self.flush()
        self.assertEqual(bar.value(),0)
        cursor=self.popup.browser.document().find('answer')
        self.popup.browser.setTextCursor(cursor)
        self.popup.browser.setFocus()
        QTest.keyClick(self.popup.browser,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(APP.clipboard().text(),'answer')

    def test_http_links_only_open_on_explicit_link_action(self):
        self.popup.finish('[Source](https://example.com)')
        with patch('interfaces.skill_popup.QDesktopServices.openUrl') as open_url:
            self.popup._open_link(QUrl('javascript:alert(1)'))
            open_url.assert_not_called()
            self.popup._open_link(QUrl('https://example.com'))
            open_url.assert_called_once()
        self.assertEqual(APP.clipboard().text(),'original clipboard')


if __name__=='__main__':
    unittest.main()
