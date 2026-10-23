"""Own capture overlays and the same overlays pinned without desktop shading."""
from PySide6.QtCore import QObject


class ScreenshotQAController(QObject):
    def __init__(self, executor, parent=None):
        super().__init__(parent)
        self.executor = executor
        self.overlay = None
        self.pinned_answers = set()

    def start(self, provider):
        if self.overlay is not None:
            self.overlay.raise_()
            self.overlay.activateWindow()
            return
        from interfaces.screenshot_overlay import ScreenshotOverlay
        overlay = ScreenshotOverlay(provider, self.executor)
        self.overlay = overlay
        overlay.closed.connect(lambda: self._closed(overlay))
        overlay.pin_requested.connect(self.pin_answer)
        overlay.start()

    def _closed(self, overlay):
        if self.overlay is overlay:
            self.overlay = None
        self.pinned_answers.discard(overlay)
        overlay.deleteLater()

    def pin_answer(self, overlay):
        if self.overlay is overlay:
            self.overlay = None
        self.pinned_answers.add(overlay)
        return overlay

    def close(self):
        if self.overlay is not None:
            self.overlay.close()
        for overlay in tuple(self.pinned_answers):
            overlay.close()
