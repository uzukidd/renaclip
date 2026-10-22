"""Cursor-adjacent Skill output, owned entirely by the Qt UI thread."""
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QEvent, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

from interfaces import theme
from interfaces.screenshot_overlay import AnswerBrowser, SpinnerWidget, safe_url


class SkillPopup(QWidget):
    closed = Signal()

    def __init__(self, title, position: QPoint, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.is_closed = False
        self._request = None
        self._source = ''
        self._rendered = None
        self._terminal = False
        self._failed = False
        self._drag_offset = None
        self._position = QPoint(position)
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        self._available = screen.availableGeometry() if screen else QRect(position, self.size())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setObjectName('skillPopup')
        self.setToolTip('Double-click to close')
        self.setStyleSheet(f'''
            QWidget#skillPopup {{background:{theme.SURFACE}; border:1px solid {theme.DIVIDER}; border-radius:{theme.RADIUS}px;}}
            QLabel {{color:{theme.ACCENT_LIGHT}; background:transparent; font-family:'{theme.FONT_FAMILY}'; font-size:{theme.BODY_SIZE}px;}}
            QPushButton {{color:{theme.ACCENT_LIGHT}; background:{theme.SURFACE_ACCENT}; border:0; border-radius:6px; padding:5px 12px;}}
            QPushButton:hover {{background:{theme.SURFACE_RAISED};}}
            QTextBrowser {{color:{theme.TEXT}; background:transparent; border:0; font-family:'{theme.FONT_FAMILY}'; font-size:{theme.BODY_SIZE}px; selection-background-color:{theme.ACCENT};}}
            QScrollBar:vertical {{background:{theme.SURFACE};width:{theme.SCROLLBAR_WIDTH}px;margin:0;}}
            QScrollBar::handle:vertical {{background:{theme.ACCENT};min-height:24px;border-radius:4px;}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.CONTENT_PADDING, theme.CONTENT_PADDING,
                                  theme.CONTENT_PADDING, theme.CONTENT_PADDING)
        layout.setSpacing(8)
        self.header = QWidget(self)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(str(title), self.header)
        self.title_label.setMinimumWidth(0)
        self.title_label.setToolTip(str(title))
        self._title = str(title)
        self.copy_button = QPushButton('Copy', self.header)
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self.copy_text)
        row.addWidget(self.title_label, 1)
        row.addWidget(self.copy_button)
        layout.addWidget(self.header)
        self.waiting = QWidget(self)
        waiting_row = QHBoxLayout(self.waiting)
        waiting_row.setContentsMargins(0, 8, 0, 8)
        self.spinner = SpinnerWidget(self.waiting)
        self.thinking_label = QLabel('Thinking...', self.waiting)
        waiting_row.addWidget(self.spinner)
        waiting_row.addWidget(self.thinking_label, 1)
        layout.addWidget(self.waiting)
        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(60)
        self.spinner_timer.timeout.connect(self._spin)
        self.browser = AnswerBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        self.browser.document().setDefaultStyleSheet(
            f'a {{ color:{theme.ACCENT_LIGHT}; text-decoration:underline; }}')
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.browser.selectionChanged.connect(self._selection_changed)
        layout.addWidget(self.browser, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._flush)
        self._rendering = False
        for widget in (self, self.header, self.title_label, self.copy_button, self.waiting,
                       self.spinner, self.thinking_label, self.browser,
                       self.browser.viewport(), self.browser.verticalScrollBar(),
                       self.browser.horizontalScrollBar()):
            widget.installEventFilter(self)
        self.resize(min(440, self._available.width()), min(120, self._available.height()))
        self._clamp(self._position + QPoint(16, 16))
        self.browser.hide()
        self.copy_button.hide()
        self.spinner_timer.start()
        self.show()
        self.raise_()

    def _spin(self):
        self.spinner.angle = (self.spinner.angle + 24) % 360
        self.spinner.update()

    def _stop_waiting(self):
        self.spinner_timer.stop()
        self.waiting.hide()

    def attach_request(self, future):
        if self.is_closed:
            future.cancel()
        else:
            self._request = future

    def append_text(self, delta):
        if self.is_closed or self._terminal:
            return
        self._source += str(delta or '')
        self._schedule()

    def finish(self, text):
        if self.is_closed or self._terminal:
            return
        self._source = str(text or '')
        if not self._source.strip():
            self._failed = True
            self._source = 'The model returned no text. Please try again.'
        self._stop_waiting()
        self._terminal = True
        self._request = None
        self._timer.stop()
        self._flush()

    def fail(self, message):
        if self.is_closed or self._terminal:
            return
        self._failed = self._terminal = True
        self._stop_waiting()
        self._request = None
        self._source = str(message or 'Request failed. Please try again.')
        self._timer.stop()
        self._flush()

    def _schedule(self):
        if self._source.strip() and not self._timer.isActive():
            self._timer.start()

    def _selection_changed(self):
        if not self._rendering and not self.browser.textCursor().hasSelection():
            if self._rendered != self._source and not self.is_closed:
                self._schedule()

    def _flush(self):
        if self.is_closed or not self._source.strip() or self._rendered == self._source:
            return
        # Preserve an active user's selection; render queued text once it is cleared.
        if self.browser.textCursor().hasSelection():
            return
        self._stop_waiting()
        self.browser.show()
        self.copy_button.show()
        bar = self.browser.verticalScrollBar()
        old, tail = bar.value(), bar.value() >= bar.maximum() - 2
        self._rendering = True
        try:
            if self._failed:
                self.browser.setPlainText(self._source)
            else:
                self.browser.setMarkdown(self._source)
            self._rendered = self._source
            self._fit()
        finally:
            self._rendering = False
        if not self.isVisible():
            self.show()
            self.raise_()
        bar.setValue(bar.maximum() if tail else old)

    def _fit(self):
        width = min(440, self._available.width())
        text_width = max(1, width - 2 * theme.CONTENT_PADDING - theme.SCROLLBAR_WIDTH - 8)
        self.browser.document().setTextWidth(text_width)
        height = int(self.browser.document().size().height()) + self.header.sizeHint().height() + 36
        self.resize(width, min(560, self._available.height(), max(90, height)))
        self.title_label.setText(self.title_label.fontMetrics().elidedText(
            self._title, Qt.TextElideMode.ElideRight,
            max(1, width - 2 * theme.CONTENT_PADDING - self.copy_button.sizeHint().width() - 12)))
        self._clamp(self.pos())

    def _clamp(self, position):
        area = self._available
        self.move(max(area.left(), min(position.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(position.y(), area.bottom() - self.height() + 1)))

    def copy_text(self):
        if self.is_closed:
            return
        # Reuse formula-aware plain-text extraction without changing user selection.
        cursor = self.browser.textCursor()
        self.browser.blockSignals(True)
        try:
            all_text = QTextCursor(self.browser.document())
            all_text.select(QTextCursor.SelectionType.Document)
            self.browser.setTextCursor(all_text)
            text = self.browser.createMimeDataFromSelection().text()
            self.browser.setTextCursor(cursor)
        finally:
            self.browser.blockSignals(False)
        QApplication.clipboard().setText(text)

    def _open_link(self, url):
        if not self.is_closed and safe_url(url.toString()):
            QDesktopServices.openUrl(url)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.close()
            return True
        if watched in (self, self.header, self.title_label, self.waiting, self.spinner, self.thinking_label):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                self._clamp(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if not self.is_closed:
            self.is_closed = True
            self.spinner_timer.stop()
            self._timer.stop()
            request, self._request = self._request, None
            if request is not None:
                request.cancel()
            self.closed.emit()
        super().closeEvent(event)
