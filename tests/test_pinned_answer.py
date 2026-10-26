"""Pin preserves the original capture widgets while releasing the desktop."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch
from PySide6.QtCore import QPoint, QRect, Qt, QCoreApplication, QEvent
from PySide6.QtGui import QPixmap, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from interfaces.screenshot_qa import ScreenshotQAController

APP = QApplication.instance() or QApplication([])


class PinnedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.controller = ScreenshotQAController(Mock())
        self.addCleanup(self.cleanup_windows)
        self.start_overlay()

    def cleanup_windows(self):
        self.controller.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def start_overlay(self):
        pixmap = QPixmap(1000, 900)
        pixmap.fill(Qt.GlobalColor.white)
        with patch("interfaces.screenshot_overlay.capture_desktop", return_value=(pixmap, QRect(-100, -40, 1000, 900))):
            self.controller.start({"model": "vision"})
        overlay = self.controller.overlay
        overlay._work_area = QRect(0, 0, 1000, 860)
        overlay.begin_selection(20, 30)
        overlay.finish_selection(240, 230)
        APP.processEvents()
        return overlay

    def complete(self, answer):
        overlay = self.controller.overlay
        overlay.question = "Explain this?"
        overlay.pending = True
        overlay.receive_result(overlay.generation, answer)
        APP.processEvents()
        return overlay

    def test_pin_is_icon_at_image_corner_before_question_and_doubleclick_cancels(self):
        overlay = self.controller.overlay
        self.assertTrue(overlay.pin_button.isVisible())
        self.assertEqual(overlay.pin_button.text(), '')
        self.assertFalse(overlay.pin_button.icon().isNull())
        self.assertIs(overlay.pin_button.parentWidget(), overlay)
        self.assertTrue(overlay.bounds.contains(overlay.pin_button.geometry()))
        self.assertFalse(hasattr(overlay, 'waiting_pin_button'))
        QTest.mouseClick(overlay.pin_button, Qt.MouseButton.LeftButton)
        self.assertTrue(overlay.is_pinned)
        self.assertIsNone(self.controller.overlay)
        QTest.mouseDClick(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, overlay.bounds.center())
        self.assertTrue(overlay._closed)
        self.assertFalse(self.controller.pinned_answers)

    def test_pin_keeps_same_widgets_layout_and_releases_outside_region(self):
        overlay = self.complete(r"**Result** \(\frac{a}{b}\) [Source](https://example.com)")
        widgets = (overlay.question_widget, overlay.answer_widget)
        rects = (QRect(overlay.bounds), QRect(overlay.question_bounds), QRect(overlay.answer_bounds))
        geometry = overlay.geometry()
        overlay.answer_widget.selectAll()
        selected = overlay.answer_widget.textCursor().selectedText()
        QTest.mouseClick(overlay.pin_button, Qt.MouseButton.LeftButton)
        self.assertIsNone(self.controller.overlay)
        self.assertEqual(self.controller.pinned_answers, {overlay})
        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.is_pinned)
        self.assertEqual((overlay.question_widget, overlay.answer_widget), widgets)
        self.assertEqual((overlay.bounds, overlay.question_bounds, overlay.answer_bounds), rects)
        self.assertEqual(overlay.geometry(), geometry)
        self.assertEqual(overlay.answer_widget.textCursor().selectedText(), selected)
        mask = overlay.mask()
        for rect in rects:
            self.assertTrue(mask.contains(rect.center()))
        self.assertFalse(mask.contains(QPoint(950, 800)))
        self.assertFalse(mask.contains(QPoint(246, 50)))  # gap between screenshot and panels
        self.assertEqual(len(overlay.answer_widget.formula_sources), 1)
        self.assertEqual(overlay.answer_widget.document().find("Source").charFormat().anchorHref(), "https://example.com")
        self.assertFalse(overlay.begin_selection(900, 700))

    def test_streaming_continues_in_original_browser_and_mask_grows(self):
        overlay = self.controller.overlay
        overlay.pending = True
        overlay.request = Future()
        future = overlay.request
        token = overlay.generation
        self.assertTrue(overlay.pin_answer())
        self.assertFalse(future.cancelled())
        browser = overlay.answer_widget
        new_overlay = self.start_overlay()
        overlay.receive_delta(token, r"Start \(x")
        self.assertIs(overlay.answer_widget, browser)
        self.assertTrue(overlay.mask().contains(overlay.answer_bounds.center()))
        overlay.receive_delta(token, r"^2\) done")
        overlay.receive_result(token, overlay.answer)
        self.assertFalse(overlay.pending)
        self.assertEqual(len(browser.formula_sources), 1)
        self.assertEqual(new_overlay.answer, "")
        self.assertTrue(overlay.isVisible())

    def test_drag_moves_whole_ui_without_changing_crop(self):
        overlay = self.complete("answer")
        overlay.pin_answer()
        position = overlay.pos()
        bounds = QRect(overlay.bounds)
        caption = overlay.answer_caption
        local = caption.rect().center()
        global_point = caption.mapToGlobal(local)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, local, global_point,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(caption, press)
        movement = QPoint(50, 35)
        move = QMouseEvent(QEvent.Type.MouseMove, local + movement, global_point + movement,
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(caption, move)
        self.assertEqual(overlay.pos(), position + movement)
        self.assertEqual(overlay.bounds, bounds)
        self.assertIsNone(overlay.drag_start)

    def test_close_pin_cancels_and_does_not_close_new_capture(self):
        overlay = self.controller.overlay
        overlay.pending = True
        overlay.request = Future()
        future = overlay.request
        overlay.pin_answer()
        new_overlay = self.start_overlay()
        QTest.mouseDClick(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, overlay.bounds.center())
        self.assertTrue(future.cancelled())
        self.assertIs(self.controller.overlay, new_overlay)
        self.assertFalse(self.controller.pinned_answers)
        self.assertTrue(new_overlay.isVisible())
        overlay.receive_delta(overlay.generation, "late")
        self.assertEqual(overlay.answer, "")

    def test_pinned_question_can_be_edited_and_resubmitted(self):
        overlay = self.complete("answer")
        overlay.pin_answer()
        self.assertTrue(overlay.begin_editing())
        self.controller.executor.submit.side_effect = lambda coro, **kw: (coro.close(), Future())[1]
        self.assertTrue(overlay.send_question("follow up"))
        self.assertTrue(overlay.is_pinned)
        self.assertTrue(overlay.pending)
        self.assertTrue(overlay.mask().contains(overlay.question_bounds.center()))
        self.assertFalse(overlay.mask().contains(QPoint(950, 800)))

    def test_multiple_pins_and_shutdown(self):
        first = self.complete("first")
        first.pin_answer()
        second = self.start_overlay()
        self.complete("second").pin_answer()
        self.assertEqual(self.controller.pinned_answers, {first, second})
        first.close()
        self.assertEqual(self.controller.pinned_answers, {second})
        self.controller.close()
        self.assertFalse(second.isVisible())


if __name__ == "__main__":
    unittest.main()
