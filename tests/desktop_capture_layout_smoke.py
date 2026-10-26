"""Native screenshot layout/pin smoke with synthetic image and no API calls."""
import sys
from concurrent.futures import Future
from pathlib import Path
from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QPixmap, QColor, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from interfaces.screenshot_overlay import ScreenshotOverlay
from interfaces.theme import apply_qt_theme


class Executor:
    def submit(self, coro, **kwargs):
        coro.close()
        return Future()
    def post(self, callback, *args):
        callback(*args)


def main():
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    apply_qt_theme(app)
    image = QPixmap(1100, 850)
    image.fill(QColor('#36313a'))
    painter = QPainter(image)
    painter.fillRect(QRect(40, 680, 230, 130), QColor('#fce4ec'))
    painter.setPen(QColor('#19171d'))
    painter.drawText(55, 710, 'Bottom-edge screenshot')
    painter.end()
    overlay = ScreenshotOverlay({'name':'Demo','model':'Vision','reasoning_effort':'high'}, Executor(),
        screenshot=image, work_area=QRect(0,0,1100,850))
    overlay.start()
    errors = []

    def verify():
        try:
            overlay.begin_selection(40, 680)
            overlay.finish_selection(270, 810)
            original_question = QRect(overlay.question_bounds)
            assert original_question.top() == 680
            overlay.send_question('Explain this screenshot')
            overlay.receive_result(overlay.generation, '**Adaptive answer**\n\n' + 'The question remains beside the screenshot. ' * 130)
            app.processEvents()
            assert overlay.answer_direction == 'above'
            assert overlay.question_bounds == original_question
            assert overlay.answer_bounds.bottom() < original_question.top()
            output = Path('review/screenshot-layout')
            output.mkdir(parents=True, exist_ok=True)
            assert overlay.grab().save(str(output/'bottom-answer-above.png'))
            QTest.mouseClick(overlay.pin_button, Qt.MouseButton.LeftButton)
            assert overlay.is_pinned
            assert overlay.mask().contains(overlay.pin_button.geometry().center())
            assert not overlay.mask().contains(QPoint(1050,50))
            QTest.mouseDClick(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, overlay.bounds.center())
            assert overlay._closed
            print('Native question anchoring, upward answer, corner pin and image double-click passed.', flush=True)
        except Exception as exc:
            errors.append(exc)
        finally:
            overlay.close()
            app.quit()
    QTimer.singleShot(300, verify)
    app.exec()
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
