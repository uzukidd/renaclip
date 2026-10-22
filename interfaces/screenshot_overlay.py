"""Qt screenshot selection and progressive Markdown answer bubbles.

All coordinates are Qt logical pixels. Network callbacks arrive through the
application's AsyncExecutor; the overlay never owns a thread or event loop.
"""
import base64
import html
import hashlib
from copy import deepcopy
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QRect, QRectF, Qt, QTimer, Signal, QEvent, QThread, QUrl, QMimeData
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen, QPixmap, QTextDocument, QTextCursor, QTextImageFormat, QTextCharFormat, QTextOption
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QTextBrowser, QWidget, QVBoxLayout, QHBoxLayout

from interfaces import theme
from interfaces.formula_rendering import FormulaRenderer, protect_formulas
from openai_client.screenshot_chat import ScreenshotConversation


def safe_url(value):
    value = str(value)
    if any(ord(c) < 32 for c in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname) and not (parsed.username or parsed.password)
    except ValueError:
        return False


def capture_desktop(screens):
    """Grab all screens before showing UI, composing each at its logical size."""
    virtual = QRect()
    for screen in screens:
        virtual = virtual.united(screen.geometry())
    if virtual.isEmpty():
        raise RuntimeError("No screen is available")
    ratio = max(float(screen.devicePixelRatio()) for screen in screens)
    result = QPixmap(round(virtual.width() * ratio), round(virtual.height() * ratio))
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.GlobalColor.black)
    painter = QPainter(result)
    for screen in screens:
        geometry = screen.geometry().translated(-virtual.topLeft())
        grabbed = screen.grabWindow(0)
        painter.drawPixmap(QRectF(geometry), grabbed, QRectF(grabbed.rect()))
    painter.end()
    return result, virtual


class QuestionEdit(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not event.isAutoRepeat():
                self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AnswerBrowser(QTextBrowser):
    """Markdown with local formula images; remote resources remain disabled."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.formula_renderer = FormulaRenderer()
        self.formula_sources = {}

    def clear(self):
        self.formula_sources.clear()
        super().clear()

    def setMarkdown(self, source):
        protected, formulas = protect_formulas(source)
        self.formula_sources.clear()
        super().setMarkdown(protected)
        document = self.document()
        for formula in formulas:
            cursor = document.find(formula.marker)
            if cursor.isNull():
                continue
            if not formula.complete:
                cursor.insertText(formula.source)
                continue
            try:
                image, width, height = self.formula_renderer.render(
                    formula.tex, formula.display, theme.TEXT, theme.BODY_SIZE,
                    self.devicePixelRatioF())
            except (OSError, RuntimeError, ValueError):
                # Unsupported/unfinished TeX remains readable without allowing
                # Markdown to reinterpret its underscores and matrix rows.
                cursor.insertText(formula.source)
                continue
            # Stable names reuse Qt's image resources across streaming updates.
            digest = hashlib.sha256((formula.source + str(self.devicePixelRatioF())).encode("utf-8")).hexdigest()
            name = "renaclip-math:" + digest
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
            self.formula_sources[name] = formula.source
            image_format = QTextImageFormat()
            image_format.setName(name)
            image_format.setWidth(width)
            image_format.setHeight(height)
            image_format.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
            if formula.display and cursor.selectionStart() > cursor.block().position():
                cursor.setPosition(cursor.selectionStart())
                cursor.insertBlock()
                cursor = document.find(formula.marker)
            cursor.insertImage(image_format)
            if formula.display and not cursor.atBlockEnd():
                cursor.insertBlock()

    def createMimeDataFromSelection(self):
        original_mime = super().createMimeDataFromSelection()
        mime = QMimeData()
        for format_name in original_mime.formats():
            mime.setData(format_name, original_mime.data(format_name))
        selection = self.textCursor()
        start, end = selection.selectionStart(), selection.selectionEnd()
        text = selection.selectedText().encode("utf-16-le", errors="surrogatepass")
        replacements = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    original = self.formula_sources.get(fmt.toImageFormat().name())
                    if original is not None:
                        for position in range(max(start, fragment.position()), min(end, fragment.position() + fragment.length())):
                            replacements.append((position - start, original))
                iterator += 1
            block = block.next()
        for offset, original in reversed(replacements):
            text = text[:offset * 2] + original.encode("utf-16-le") + text[(offset + 1) * 2:]
        text = text.decode("utf-16-le", errors="surrogatepass").replace("\u2029", "\n")
        mime.setText(text)
        # An external app cannot resolve document-local formula image URLs.
        if replacements:
            mime.setHtml("<pre>" + html.escape(text) + "</pre>")
        return mime

    def loadResource(self, resource_type, name):
        return QByteArray()


class SpinnerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.setFixedSize(22, 22)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        painter.drawArc(QRectF(2, 2, 18, 18), self.angle * 16, 250 * 16)


class ScreenshotOverlay(QWidget):
    closed = Signal()

    def __init__(self, provider, executor, parent=None, *, screenshot=None, screens=None, work_area=None):
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("ScreenshotOverlay requires the Qt UI thread")
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.provider = deepcopy(provider)
        self.executor = executor
        self.screens = list(screens if screens is not None else app.screens())
        if screenshot is None:
            self.snapshot, self.virtual_geometry = capture_desktop(self.screens)
        elif isinstance(screenshot, tuple):
            self.snapshot, self.virtual_geometry = screenshot
        else:
            self.snapshot = screenshot
            size = screenshot.deviceIndependentSize().toSize()
            self.virtual_geometry = QRect(QPoint(), size)
        self.setGeometry(self.virtual_geometry)
        self._work_area = QRect(work_area) if work_area is not None else None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.bounds = QRect()
        self.question_bounds = QRect()
        self.answer_bounds = QRect()
        self._max_panel = QRect()
        self.drag_start = None
        self.generation = 0
        self.pending = self.sent = self.editing = False
        self.answer_visible = self.waiting_visible = False
        self.question = self.answer = ""
        self.request = None
        self._closed = False
        effort = str(provider.get("reasoning_effort") or "default").lower()
        effort = effort if effort in ("low", "medium", "high") else "default"
        self.model_caption = f"{provider.get('model') or 'Model'} / {effort.capitalize()}"
        self.spinner_angle = 0
        self.spinner = QTimer(self)
        self.spinner.setInterval(60)
        self.spinner.timeout.connect(self._spin)
        self.question_bubble = QWidget(self)
        self.question_bubble.setObjectName("questionBubble")
        qlayout = QVBoxLayout(self.question_bubble)
        qlayout.setContentsMargins(theme.CONTENT_PADDING, theme.CONTENT_PADDING, theme.CONTENT_PADDING, theme.CONTENT_PADDING)
        self.question_widget = QuestionEdit(self.question_bubble)
        self.question_widget.setPlaceholderText("Ask something...")
        self.question_widget.submit.connect(self.send_question)
        qlayout.addWidget(self.question_widget)
        self.answer_bubble = QWidget(self)
        self.answer_bubble.setObjectName("answerBubble")
        alayout = QVBoxLayout(self.answer_bubble)
        alayout.setContentsMargins(theme.CONTENT_PADDING, theme.CONTENT_PADDING, theme.CONTENT_PADDING, theme.CONTENT_PADDING)
        header = QHBoxLayout()
        self.answer_caption = QLabel(self.model_caption, self.answer_bubble)
        self.answer_caption.setStyleSheet(f"color:{theme.ACCENT_LIGHT};font-weight:600;")
        self.answer_caption.setMinimumWidth(0)
        header.addWidget(self.answer_caption, 1)
        self.header_spinner = SpinnerWidget(self.answer_bubble)
        self.header_spinner.hide()
        header.addWidget(self.header_spinner)
        alayout.addLayout(header)
        self.answer_widget = AnswerBrowser(self.answer_bubble)
        self.answer_widget.setOpenLinks(False)
        self.answer_widget.setOpenExternalLinks(False)
        self.answer_widget.anchorClicked.connect(self._open_link)
        self.answer_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.answer_widget.document().setDefaultStyleSheet(f"a {{ color:{theme.ACCENT_LIGHT}; text-decoration:underline; }}")
        alayout.addWidget(self.answer_widget, 1)
        self.setStyleSheet(f"""
            QWidget#questionBubble {{background:{theme.SURFACE_ACCENT};border-radius:{theme.RADIUS}px;}}
            QWidget#answerBubble {{background:{theme.SURFACE};border-radius:{theme.RADIUS}px;}}
            QPlainTextEdit,QTextBrowser {{background:transparent;color:{theme.TEXT};border:0;selection-background-color:{theme.ACCENT};font-family:'{theme.FONT_FAMILY}';font-size:{theme.BODY_SIZE}px;}}
            QScrollBar:vertical {{background:{theme.SURFACE};width:{theme.SCROLLBAR_WIDTH}px;margin:0;}}
            QScrollBar::handle:vertical {{background:{theme.ACCENT};min-height:24px;border-radius:4px;}}
            QScrollBar::handle:vertical:hover {{background:{theme.ACCENT_LIGHT};}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}
        """)
        self.question_bubble.hide()
        self.answer_bubble.hide()
        for child in (self.question_bubble, self.question_widget, self.question_widget.viewport(),
                      self.answer_bubble, self.answer_caption, self.answer_widget, self.answer_widget.viewport(),
                      self.answer_widget.verticalScrollBar(), self.header_spinner):
            child.installEventFilter(self)

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        return self

    def _point(self, x, y=None):
        point = QPoint(x) if isinstance(x, QPoint) else QPoint(int(x), int(y))
        return QPoint(max(0, min(point.x(), self.width() - 1)), max(0, min(point.y(), self.height() - 1)))

    def begin_selection(self, x, y=None):
        point = self._point(x, y)
        if self._closed:
            return False
        if self.question_bubble.isVisible() and self.question_bounds.contains(point):
            if self.sent:
                self.begin_editing()
            return False
        if self.answer_visible and self.answer_bounds.contains(point):
            if self.sent:
                self.begin_editing()
            return False
        if self.bounds.contains(point):
            return False
        self._cancel_request()
        self.generation += 1
        self.pending = self.sent = self.editing = False
        self.answer_visible = self.waiting_visible = False
        self.question = self.answer = ""
        self.question_widget.clear()
        self.answer_widget.clear()
        self.question_bubble.hide()
        self.answer_bubble.hide()
        self.spinner.stop()
        self.bounds = QRect()
        self.drag_start = point
        self.update()
        return True

    def drag_selection(self, x, y=None):
        if self.drag_start is not None:
            self.bounds = QRect(self.drag_start, self._point(x, y)).normalized()
            self.update()

    def finish_selection(self, x, y=None):
        if self.drag_start is None or self._closed:
            return False
        self.bounds = QRect(self.drag_start, self._point(x, y)).normalized()
        self.drag_start = None
        if self.bounds.width() < 8 or self.bounds.height() < 8:
            self.bounds = QRect()
            self.update()
            return False
        self._place_panel()
        self.question_bubble.setGeometry(self.question_bounds)
        self.question_bubble.show()
        self.question_widget.setReadOnly(False)
        self.question_widget.setFocus()
        self.editing = True
        self.update()
        return True

    def _available(self):
        if self._work_area is not None:
            return self._work_area.intersected(self.rect())
        global_rect = self.bounds.translated(self.virtual_geometry.topLeft())
        screen = max(self.screens, key=lambda s: s.geometry().intersected(global_rect).width() * s.geometry().intersected(global_rect).height())
        return screen.availableGeometry().translated(-self.virtual_geometry.topLeft()).intersected(self.rect())

    def _place_panel(self):
        area = self._available()
        width = min(360, area.width())
        height = min(720, area.height())
        y = max(area.top(), min(self.bounds.top(), area.bottom() - height + 1))
        if self.bounds.right() + 12 + width <= area.right() + 1:
            x = self.bounds.right() + 12
        elif self.bounds.left() - 12 - width >= area.left():
            x = self.bounds.left() - 12 - width
        else:
            x = max(area.left(), min(self.bounds.right() + 12, area.right() - width + 1))
        self._max_panel = QRect(x, y, width, min(720, area.bottom() - y + 1))
        self.question_bounds = QRect(x, y, width, min(96, max(24, self._max_panel.height() // 3)))

    def _image_url(self):
        # Snapshot and crop are logical; crop QPixmap.copy requires physical pixels.
        ratio = self.snapshot.devicePixelRatio()
        physical = QRect(round(self.bounds.x() * ratio), round(self.bounds.y() * ratio),
                         round(self.bounds.width() * ratio), round(self.bounds.height() * ratio))
        crop = self.snapshot.copy(physical)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        crop.save(buffer, "PNG")
        return "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode("ascii")

    def send_question(self, question=None):
        if self._closed or self.bounds.isEmpty() or self.pending or (self.sent and not self.editing):
            return False
        text = (self.question_widget.toPlainText() if question is None else str(question)).strip()
        if not text:
            return False
        self.question = text
        self.question_widget.setPlainText(text)
        self.question_widget.setReadOnly(True)
        self.answer = ""
        self.answer_widget.clear()
        self.answer_bubble.hide()
        self.answer_visible = False
        self.waiting_visible = True
        self.pending, self.sent, self.editing = True, False, False
        self.generation += 1
        token = self.generation
        self.spinner.start()
        self.update()
        image = self._image_url()
        async def request():
            conversation = ScreenshotConversation(self.provider, image)
            return await conversation.send(text, on_delta=lambda delta: self.executor.post(self.receive_delta, token, delta))
        coro = request()
        try:
            self.request = self.executor.submit(coro,
                on_result=lambda result: self.receive_result(token, result),
                on_error=lambda error: self.receive_error(token, error))
        except Exception as error:
            coro.close()
            self.receive_error(token, error)
        return True

    def receive_delta(self, token, text):
        if self._closed or token != self.generation or not self.pending:
            return
        self.answer += text
        if self.answer.strip():
            self.answer_visible, self.waiting_visible = True, False
            self._render_answer()
        self.update()

    def receive_result(self, token, text):
        if self._closed or token != self.generation or not self.pending:
            return
        if not str(text or "").strip():
            self.receive_error(token, RuntimeError("Empty answer"))
            return
        self.answer = str(text)
        self.pending, self.sent, self.editing = False, True, False
        self.answer_visible, self.waiting_visible = True, False
        self.request = None
        self.spinner.stop()
        self._render_answer()
        self.update()

    def receive_error(self, token, error):
        if self._closed or token != self.generation or not self.pending:
            return
        status = getattr(error, "status_code", None)
        self.answer = f"Request failed (HTTP {status}). Please retry." if isinstance(status, int) else "Request failed. Please retry."
        self.pending = self.sent = self.waiting_visible = False
        self.editing = self.answer_visible = True
        self.request = None
        self.question_widget.setReadOnly(False)
        self.spinner.stop()
        self._render_answer()
        self.update()

    def _render_answer(self):
        bar = self.answer_widget.verticalScrollBar()
        old_value, at_bottom = bar.value(), bar.value() >= bar.maximum() - 2
        self.answer_widget.setMarkdown(self.answer)
        document = self.answer_widget.document()
        document.setTextWidth(max(1, self.question_bounds.width() - 2 * theme.CONTENT_PADDING - theme.SCROLLBAR_WIDTH - 8))
        desired = int(document.size().height()) + 2 * theme.CONTENT_PADDING + 34
        available = max(1, self._max_panel.bottom() - self.question_bounds.bottom() - 10)
        height = min(560, available, max(90, desired))
        self.answer_bounds = QRect(self.question_bounds.x(), self.question_bounds.bottom() + 11, self.question_bounds.width(), height)
        self.answer_bubble.setGeometry(self.answer_bounds)
        self.answer_bubble.show()
        self.header_spinner.setVisible(self.pending)
        fm = self.answer_caption.fontMetrics()
        self.answer_caption.setText(fm.elidedText(self.model_caption, Qt.TextElideMode.ElideRight, max(1, self.answer_bounds.width() - 2 * theme.CONTENT_PADDING - 24)))
        bar.setValue(bar.maximum() if at_bottom else old_value)

    def begin_editing(self):
        if self._closed or self.pending or not self.sent:
            return False
        self.editing = True
        self.question_widget.setReadOnly(False)
        self.question_widget.setFocus()
        return True

    def _open_link(self, url):
        if safe_url(url.toString()):
            QDesktopServices.openUrl(url)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if watched is self.answer_widget.viewport():
                if self.answer_widget.anchorAt(event.position().toPoint()):
                    return False
            if self.sent:
                # Keep browser focus for text selection, but reopen question editing.
                self.editing = True
                self.question_widget.setReadOnly(False)
                if watched not in (self.answer_widget, self.answer_widget.viewport()):
                    self.question_widget.setFocus()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_selection(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            self.drag_selection(event.position().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.finish_selection(event.position().toPoint())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _spin(self):
        self.spinner_angle = (self.spinner_angle + 24) % 360
        self.header_spinner.angle = self.spinner_angle
        self.header_spinner.update()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(QRectF(self.rect()), self.snapshot, QRectF(self.snapshot.rect()))
        painter.fillRect(self.rect(), QColor(0, 0, 0, 166))
        if not self.bounds.isEmpty():
            source = QRectF(self.bounds.x() * self.snapshot.devicePixelRatio(), self.bounds.y() * self.snapshot.devicePixelRatio(), self.bounds.width() * self.snapshot.devicePixelRatio(), self.bounds.height() * self.snapshot.devicePixelRatio())
            painter.drawPixmap(QRectF(self.bounds), self.snapshot, source)
            painter.setPen(QPen(QColor(theme.ACCENT), theme.SELECTION_WIDTH))
            painter.drawRect(self.bounds)
        if self.pending:
            # Paint in parent only outside child bubbles; header spinner is painted via overlay above children.
            if self.waiting_visible:
                ring = QRectF(self.question_bounds.left() + 12, self.question_bounds.bottom() + 20, 18, 18)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(theme.ACCENT), 2))
                painter.drawArc(ring, self.spinner_angle * 16, 250 * 16)
                painter.setPen(QColor(theme.TEXT))
                painter.setFont(QFont(theme.FONT_FAMILY, 10))
                painter.drawText(int(ring.right() + 10), int(ring.bottom() - 2), "Thinking...")
        painter.end()

    def _cancel_request(self):
        if self.request is not None:
            self.request.cancel()
            self.request = None

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self.generation += 1
            self._cancel_request()
            self.spinner.stop()
            self.closed.emit()
        event.accept()
