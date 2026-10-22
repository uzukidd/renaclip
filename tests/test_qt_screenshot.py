"""Real Qt controls with synthetic screenshots and a non-network executor."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QPoint, QRect, Qt, QUrl
from PySide6.QtGui import QPixmap, QColor, QTextCharFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from interfaces.screenshot_overlay import ScreenshotOverlay, safe_url, capture_desktop

APP = QApplication.instance() or QApplication([])

class Executor:
    def __init__(self):
        self.calls = []
    def submit(self, coro, on_result=None, on_error=None):
        coro.close()
        future = Mock()
        self.calls.append((future, on_result, on_error))
        return future
    def post(self, callback, *args):
        callback(*args)

class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.executor = Executor()
        pixmap = QPixmap(1000, 900)
        pixmap.fill(QColor("#334455"))
        self.overlay = ScreenshotOverlay({"name":"SECRET-PROVIDER", "model":"vision", "reasoning_effort":"high"}, self.executor,
            screenshot=(pixmap, QRect(-100, -40, 1000, 900)), work_area=QRect(0,0,1000,860))
        self.overlay.start()
        APP.processEvents()
        self.addCleanup(self.overlay.close)
    def select(self):
        self.overlay.begin_selection(20, 30)
        self.overlay.finish_selection(240, 230)
        APP.processEvents()
    def test_initial_question_and_enter(self):
        self.select()
        self.assertEqual(self.overlay.geometry().topLeft(), QPoint(-100,-40))
        self.assertEqual(self.overlay.question_widget.placeholderText(), "Ask something...")
        self.assertFalse(self.overlay.answer_visible)
        QTest.keyClick(self.overlay.question_widget, Qt.Key.Key_Return)
        self.assertEqual(len(self.executor.calls), 0)
        self.overlay.question_widget.setPlainText("Question")
        QTest.keyClick(self.overlay.question_widget, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertIn("\n", self.overlay.question_widget.toPlainText())
        QTest.keyClick(self.overlay.question_widget, Qt.Key.Key_Return)
        self.assertTrue(self.overlay.pending)
        self.assertTrue(self.overlay.waiting_visible)
        self.assertFalse(self.overlay.answer_visible)
        self.assertEqual(len(self.executor.calls), 1)
    def test_long_answer_wraps_without_horizontal_scrollbar(self):
        self.select()
        self.overlay.send_question('What?')
        self.overlay.receive_result(self.overlay.generation,
            '**Long answer**\n\n' + 'https://example.com/' + 'longsegment' * 70 + '\n\n' + 'A normal answer. ' * 200)
        APP.processEvents()
        browser = self.overlay.answer_widget
        self.assertEqual(browser.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertFalse(browser.horizontalScrollBar().isVisible())
        self.assertGreater(browser.verticalScrollBar().maximum(), 0)
        self.assertGreater(browser.viewport().height(), 0)
        self.assertLessEqual(browser.document().idealWidth(), browser.viewport().width() + 1)

    def test_progressive_markdown_and_caption(self):
        self.select()
        self.overlay.send_question("What?")
        token=self.overlay.generation
        self.overlay.receive_delta(token, " ")
        self.assertFalse(self.overlay.answer_visible)
        self.overlay.receive_delta(token, "**Bold** [Source](https://example.com)")
        APP.processEvents()
        self.assertTrue(self.overlay.answer_visible)
        self.assertFalse(self.overlay.waiting_visible)
        doc=self.overlay.answer_widget.document()
        self.assertNotIn("**", doc.toPlainText())
        cursor=doc.find("Bold")
        self.assertGreater(cursor.charFormat().fontWeight(), 400)
        self.assertEqual(doc.find("Source").charFormat().anchorHref(), "https://example.com")
        self.assertEqual(self.overlay.model_caption, "vision / High")
        self.assertNotIn("SECRET", self.overlay.answer_caption.text())
    def test_progressive_matrix_and_inline_formulas(self):
        self.select()
        self.overlay.send_question("Explain the matrix")
        token = self.overlay.generation
        self.overlay.receive_delta(token, r"Inline \(\hat{x}_i^m\)." + "\n\n" + r"\[\begin{bmatrix}x\\")
        APP.processEvents()
        browser = self.overlay.answer_widget
        self.assertEqual(len(browser.formula_sources), 1)
        self.assertIn(r"\begin{bmatrix}", browser.toPlainText())
        self.overlay.receive_delta(token, r"y\\1\end{bmatrix}\]")
        APP.processEvents()
        self.assertEqual(len(browser.formula_sources), 2)
        self.assertNotIn(r"\begin{bmatrix}", browser.toPlainText())
        self.assertTrue(self.overlay.answer_visible)
        self.assertLessEqual(self.overlay.answer_bounds.height(), 560)
        self.overlay.receive_result(token, self.overlay.answer)
        self.assertEqual(len(browser.formula_sources), 2)
        self.assertFalse(self.overlay.pending)

    def test_reselection_cancel_and_stale_callbacks(self):
        self.select(); self.overlay.send_question("old")
        token=self.overlay.generation
        future=self.executor.calls[-1][0]
        self.overlay.begin_selection(10,700)
        self.overlay.finish_selection(100,800)
        future.cancel.assert_called_once()
        self.overlay.receive_delta(token,"stale")
        self.overlay.receive_result(token,"stale")
        self.assertEqual(self.overlay.answer, "")
        self.assertFalse(self.overlay.pending)
    def test_completed_click_edit_preserves_answer(self):
        self.select(); self.overlay.send_question("old")
        self.overlay.receive_result(self.overlay.generation,"old answer")
        self.assertFalse(self.overlay.send_question("locked"))
        QTest.mouseClick(self.overlay.question_widget.viewport(), Qt.MouseButton.LeftButton)
        self.assertTrue(self.overlay.editing)
        self.assertEqual(self.overlay.answer,"old answer")
        self.assertTrue(self.overlay.send_question("edited"))
        self.assertFalse(self.overlay.answer_visible)
        self.assertTrue(self.overlay.waiting_visible)
    def test_escape_and_reopen(self):
        self.select(); self.overlay.send_question("pending")
        signal=Mock(); self.overlay.closed.connect(signal)
        QTest.keyClick(self.overlay.question_widget, Qt.Key.Key_Escape)
        self.assertFalse(self.overlay.isVisible())
        self.executor.calls[-1][0].cancel.assert_called_once()
        signal.assert_called_once()
        self.overlay.close(); signal.assert_called_once()
        another=ScreenshotOverlay({"model":"new"},self.executor,screenshot=QPixmap(600,500),work_area=QRect(0,0,600,500))
        another.start(); APP.processEvents()
        self.assertTrue(another.isVisible()); another.close()
    def test_adaptive_height_and_failure_retry(self):
        self.select(); self.overlay.send_question("ask")
        self.overlay.receive_delta(self.overlay.generation,"short")
        short=self.overlay.answer_bounds.height()
        self.overlay.receive_delta(self.overlay.generation," long text "*1000)
        self.assertGreater(self.overlay.answer_bounds.height(),short)
        self.assertLessEqual(self.overlay.answer_bounds.height(),560)
        self.assertLessEqual(self.overlay.answer_bounds.bottom(),860)
        self.overlay.receive_error(self.overlay.generation,RuntimeError("secret error"))
        self.assertNotIn("secret",self.overlay.answer)
        self.assertTrue(self.overlay.send_question("retry"))
    def test_links_validate_and_do_not_edit(self):
        self.select(); self.overlay.send_question("ask")
        self.overlay.receive_result(self.overlay.generation,"[Source](https://example.com)")
        with patch("interfaces.screenshot_overlay.QDesktopServices.openUrl") as opening:
            self.overlay._open_link(QUrl("https://example.com"))
            self.overlay._open_link(QUrl("file:///C:/test"))
            opening.assert_called_once()
        self.assertFalse(self.overlay.editing)
        APP.processEvents()
        cursor = self.overlay.answer_widget.document().find("Source")
        cursor.setPosition(cursor.selectionStart() + 1)
        position = self.overlay.answer_widget.cursorRect(cursor).center()
        with patch("interfaces.screenshot_overlay.QDesktopServices.openUrl") as opening:
            QTest.mouseClick(self.overlay.answer_widget.viewport(), Qt.MouseButton.LeftButton, pos=position)
            opening.assert_called_once()
        self.assertFalse(self.overlay.editing)
        self.assertFalse(safe_url("javascript:alert(1)"))
        self.assertFalse(safe_url("https://user:pass@example.com"))
    def test_remote_resources_blocked_and_dpr_crop(self):
        import base64
        from PySide6.QtGui import QImage
        from PySide6.QtGui import QTextDocument
        self.assertTrue(self.overlay.answer_widget.loadResource(QTextDocument.ResourceType.ImageResource, QUrl("https://example.com/a.png")).isEmpty())
        pixmap = QPixmap(1200, 1000)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(QColor("red"))
        self.overlay.snapshot = pixmap
        self.overlay.bounds = QRect(10, 20, 100, 80)
        payload = self.overlay._image_url().split(",", 1)[1]
        image = QImage.fromData(base64.b64decode(payload))
        self.assertEqual((image.width(), image.height()), (200, 160))

    def test_composite_respects_negative_origin_and_dpr(self):
        class Screen:
            def __init__(self, geometry, dpr, color):
                self.rect,self.dpr,self.color=geometry,dpr,color
            def geometry(self): return self.rect
            def devicePixelRatio(self): return self.dpr
            def grabWindow(self, handle):
                p=QPixmap(int(self.rect.width()*self.dpr),int(self.rect.height()*self.dpr)); p.setDevicePixelRatio(self.dpr); p.fill(QColor(self.color)); return p
        pixmap,virtual=capture_desktop([Screen(QRect(-100,0,100,100),1,"red"),Screen(QRect(0,0,100,100),2,"blue")])
        self.assertEqual(virtual,QRect(-100,0,200,100))
        self.assertEqual(pixmap.devicePixelRatio(),2)
        self.assertEqual(pixmap.toImage().pixelColor(50,50),QColor("red"))
        self.assertEqual(pixmap.toImage().pixelColor(250,50),QColor("blue"))

if __name__ == "__main__": unittest.main()
