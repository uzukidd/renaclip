"""Real production Material UI render check with isolated demo configuration."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import Future
from PySide6.QtCore import QTimer, QRect, QObject, QMetaObject, Qt
from PySide6.QtGui import QPixmap, QColor, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import config_loader
from interfaces.material_window import MainWindow
from interfaces.screenshot_overlay import ScreenshotOverlay
from interfaces.theme import apply_qt_theme


class FakeExecutor:
    def submit(self, coro, **kwargs):
        coro.close()
        return Future()
    def post(self, callback, *args):
        callback(*args)


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    apply_qt_theme(app)
    image = QPixmap(1500,900)
    image.fill(QColor('#333333'))
    painter = QPainter(image)
    painter.fillRect(QRect(60,90,400,310), QColor('#f3edf1'))
    painter.setPen(QColor('#19171d'))
    painter.drawText(80,120,'Synthetic screenshot')
    painter.end()
    output = Path('review/material-production')
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory() as folder, patch.object(config_loader, 'CONFIG_PATH', Path(folder) / 'config.json'):
        config_loader.save_config([
            {'name':'Chinese to English Translator','description':'Translates Chinese text to English.','prompt':'Translate faithfully.','web_search':False},
            {'name':'Quick answer','description':'A concise answer with web search.','prompt':'','web_search':True},
        ], {'BACKEND':'openai','HOTKEY_MODIFIER':'ctrl+shift','SCREENSHOT_QA_ENABLED':True,
            'OPENAI_REASONING_EFFORT':'high', 'OPENAI_PROVIDERS':[{'name':'Demo','model':'Vision','models':['Vision']}]})
        window = MainWindow(executor=FakeExecutor())
        window.show()
        def capture():
            try:
                assert window.window.grabWindow().save(str(output / 'home.png'))
                QMetaObject.invokeMethod(window.window,'openSettings',Qt.ConnectionType.DirectConnection)
                QTest.qWait(250)
                assert window.window.grabWindow().save(str(output / 'settings.png'))
                dialog = window.window.findChild(QObject,'settingsDialog')
                QMetaObject.invokeMethod(dialog,'reject',Qt.ConnectionType.DirectConnection)
                QTest.qWait(250)
                window.hide()
                overlay = ScreenshotOverlay({'name':'Demo','model':'Vision','reasoning_effort':'high'},
                    FakeExecutor(),screenshot=(image,QRect(0,0,1500,900)),work_area=QRect(0,0,1500,900))
                overlay.start()
                overlay.begin_selection(60,90)
                overlay.finish_selection(460,400)
                overlay.question_widget.setPlainText('Explain this screenshot')
                overlay.send_question()
                app.processEvents()
                assert overlay.grab().save(str(output / 'thinking.png'))
                overlay.receive_result(overlay.generation,'**A bold response** with [a link](https://example.com).\n\n'+'A longer answer. '*150)
                app.processEvents()
                assert overlay.grab().save(str(output / 'answer.png'))
                overlay.close()
                for _ in range(4):
                    repeat = ScreenshotOverlay({'model':'Vision'},FakeExecutor(),screenshot=image,work_area=QRect(0,0,1500,900))
                    repeat.start()
                    repeat.close()
                print('Production Material page/settings, Markdown overlay and repeated reopen passed.',flush=True)
                print(output,flush=True)
            except Exception as exc:
                errors.append(exc)
            finally:
                window.dispose()
                app.quit()
        QTimer.singleShot(500,capture)
        app.exec()
    if errors:
        raise errors[0]


if __name__=='__main__':
    main()
