"""Native Skill popup smoke with simulated streaming; never calls a provider."""
import sys
from pathlib import Path
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from interfaces.skill_popup import SkillPopup
from interfaces.theme import apply_qt_theme


def main():
    app=QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    apply_qt_theme(app)
    original=app.clipboard().text()
    point=app.primaryScreen().availableGeometry().center()
    popup=SkillPopup('Quick answer',point)
    failures=[]
    def first():
        popup.append_text('**A streamed answer** starts here. ')
        QTimer.singleShot(150,second)
    def second():
        popup.append_text('You can select this text, or use Copy.\n\n[Source](https://example.com)')
        QTimer.singleShot(150,finish)
    def finish():
        try:
            popup.finish('**A streamed answer** starts here. You can select this text, or use Copy.\n\n[Source](https://example.com)')
            assert popup.isVisible()
            assert popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            assert app.clipboard().text()==original
            output=Path('review/skill-popup.png')
            output.parent.mkdir(exist_ok=True)
            assert popup.grab().save(str(output))
            start=popup.pos()
            middle=popup.header.rect().center()
            QTest.mousePress(popup.header,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,middle)
            QTest.mouseMove(popup.header,middle+QPoint(-40,-30),50)
            QTest.mouseRelease(popup.header,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,middle+QPoint(-40,-30))
            assert popup.pos()!=start
            QTest.mouseDClick(popup.browser.viewport(),Qt.MouseButton.LeftButton)
            assert popup.is_closed
            print('Native streamed popup, clipboard isolation, drag and double-click close passed.',flush=True)
        except Exception as exc:
            failures.append(exc)
        finally:
            popup.close()
            app.quit()
    QTimer.singleShot(100,first)
    app.exec()
    if failures:
        raise failures[0]


if __name__=='__main__':
    main()
