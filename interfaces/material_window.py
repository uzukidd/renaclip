"""Production Qt Quick Material window and its Python configuration bridge."""
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from constants import RESOURCE_ROOT, TRAY_ICON_PATH
from interfaces import theme
from interfaces.material_bridge import MaterialBridge

APP_NAME = 'RenaClip'


def palette_tokens():
    return {'background':theme.BACKGROUND, 'surface':theme.SURFACE,
            'raised':theme.SURFACE_RAISED, 'accentSurface':theme.SURFACE_ACCENT,
            'accent':theme.ACCENT, 'accentLight':theme.ACCENT_LIGHT,
            'text':theme.TEXT, 'muted':theme.TEXT_MUTED, 'divider':theme.DIVIDER}


class MainWindow(QObject):
    configuration_changed = Signal()

    def __init__(self, parent=None, *, executor=None):
        super().__init__(parent)
        self.hide_on_close = False
        self._owns_executor = executor is None
        if executor is None:
            from interfaces.qt_async import AsyncExecutor
            executor = AsyncExecutor(self)
        self.executor = executor
        if not QQuickStyle.name():
            QQuickStyle.setStyle('Material')
        self.bridge = MaterialBridge(executor, self)
        self.bridge.configuration_changed.connect(self.configuration_changed)
        self.bridge.closeRequested.connect(self._requested_close)
        self.engine = QQmlApplicationEngine(self)
        self.engine.rootContext().setContextProperty('bridge', self.bridge)
        self.engine.rootContext().setContextProperty('paletteTokens', palette_tokens())
        path = RESOURCE_ROOT / 'interfaces' / 'qml' / 'Main.qml'
        self.engine.load(QUrl.fromLocalFile(str(path)))
        if not self.engine.rootObjects():
            raise RuntimeError('Could not load Material UI: ' + str(path))
        self.window = self.engine.rootObjects()[0]
        self.window.setIcon(QIcon(str(TRAY_ICON_PATH)))

    @property
    def gems(self):
        return self.bridge.skills

    @property
    def settings(self):
        return self.bridge.settings

    def refresh(self):
        self.bridge.refresh()

    def showNormal(self):
        self.window.showNormal()

    def show(self):
        self.window.show()

    def hide(self):
        self.window.hide()

    def raise_(self):
        self.window.raise_()

    def activateWindow(self):
        self.window.requestActivate()

    def close(self):
        if self.window is not None:
            self.window.hide()
        if self._owns_executor:
            self.executor.close()

    def dispose(self):
        import shiboken6
        if self.window is not None:
            self.window.hide()
            shiboken6.delete(self.engine)
            self.window = None
        if self._owns_executor:
            self.executor.close()

    def _requested_close(self):
        if self.hide_on_close:
            self.hide()
        else:
            QApplication.instance().quit()

    def statusBar(self):
        return self

    def showMessage(self, message, timeout=10000):
        self.window.setProperty('statusMessage', message)


def launch_ui(lock_path=None):
    from interfaces.application import run
    return run()
