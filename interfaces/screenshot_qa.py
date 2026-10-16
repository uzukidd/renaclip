"""Qt screenshot controller; no child processes or secondary UI runtime."""
from PySide6.QtCore import QObject


class ScreenshotQAController(QObject):
    def __init__(self, executor, parent=None):
        super().__init__(parent)
        self.executor = executor
        self.overlay = None

    def start(self, provider):
        if self.overlay is not None:
            self.overlay.raise_()
            self.overlay.activateWindow()
            return
        from interfaces.screenshot_overlay import ScreenshotOverlay
        self.overlay = ScreenshotOverlay(provider, self.executor)
        self.overlay.closed.connect(self._closed)
        self.overlay.start()

    def _closed(self):
        old, self.overlay = self.overlay, None
        if old is not None:
            old.deleteLater()

    def close(self):
        if self.overlay is not None:
            self.overlay.close()
