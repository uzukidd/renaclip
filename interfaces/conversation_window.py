"""Persistent, always-on-top Responses conversation with a reusable Qt window."""
from copy import deepcopy
from pathlib import Path
import base64

from PySide6.QtCore import Qt, QEvent, QTimer, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QPixmap, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QComboBox,
                               QVBoxLayout, QHBoxLayout, QScrollArea, QFileDialog,
                               QSizeGrip, QLayout)

from config_loader import VALID_REASONING_EFFORTS
from interfaces import theme
from interfaces.screenshot_overlay import AnswerBrowser, QuestionEdit, safe_url
from openai_client.desktop_chat import DesktopChatStore, provider_identity, request_chat


class ConversationWindow(QWidget):
    def __init__(self, executor, history_path):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.executor = executor
        self.store = DesktopChatStore(Path(history_path))
        self.state = self.store.load()
        self.providers = []
        self.pending = False
        self.request = None
        self.generation = 0
        self.disposed = False
        self.attachments = deepcopy(self.state.get('draft_images', []))
        self._turn = None
        self._partial = ''
        self._drag_offset = None
        self._browsers = []
        self._draft_restored = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setObjectName('conversationWindow')
        self.setWindowTitle('RenaClip Chat')
        self.setStyleSheet(theme.qt_stylesheet() + f'''
            QWidget#conversationWindow {{background:{theme.SURFACE};border:1px solid {theme.OUTLINE};border-radius:8px;}}
            QWidget#chatContent {{background:{theme.SURFACE};}}
            QTextBrowser {{border:0;background:{theme.SURFACE_RAISED};}}
        ''')
        layout = QVBoxLayout(self)
        self.header = QWidget(self)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header.setToolTip('Drag to move; double-click to close / 拖动移动，双击关闭')
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel('RenaClip Chat', self.header)
        row.addWidget(self.title_label, 1)
        self.clear_button = QPushButton('Clear', self.header)
        self.clear_button.setToolTip('Clear history and start a new conversation / 清空历史并开始新对话')
        self.clear_button.clicked.connect(self.clear_history)
        row.addWidget(self.clear_button)
        self.close_button = QPushButton('×', self.header)
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        layout.addWidget(self.header)
        options = QHBoxLayout()
        self.provider_combo = QComboBox(self)
        self.provider_combo.setToolTip('Responses provider / Responses 服务商')
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip('Model / 模型')
        self.effort_combo = QComboBox(self)
        self.effort_combo.addItems(list(VALID_REASONING_EFFORTS))
        self.effort_combo.setToolTip('Reasoning effort / 思考程度')
        self.effort_combo.setCurrentText(self.state.get('reasoning_effort', 'default'))
        options.addWidget(self.provider_combo, 1)
        options.addWidget(self.model_combo, 2)
        options.addWidget(self.effort_combo, 1)
        layout.addLayout(options)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.model_combo.currentTextChanged.connect(self._preferences_changed)
        self.effort_combo.currentTextChanged.connect(self._preferences_changed)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.content = QWidget(self.scroll)
        self.content.setObjectName('chatContent')
        self.messages_layout = QVBoxLayout(self.content)
        self.messages_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.messages_layout.setContentsMargins(4, 4, 4, 4)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.status_label = QLabel('', self)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.attachment_label = QLabel('', self)
        self.attachment_label.setTextFormat(Qt.TextFormat.PlainText)
        self.attachment_label.setWordWrap(True)
        layout.addWidget(self.attachment_label)
        self.input = QuestionEdit(self)
        self.input.setPlaceholderText('Message… Enter to send; Shift+Enter for a new line')
        self.input.setMaximumHeight(100)
        self.input.submit.connect(self.send_message)
        self.input.setPlainText(self.state.get('draft_text', ''))
        self._update_attachments()
        layout.addWidget(self.input)
        actions = QHBoxLayout()
        self.image_button = QPushButton('Add images', self)
        self.image_button.clicked.connect(self.choose_images)
        actions.addWidget(self.image_button)
        self.remove_images_button = QPushButton('Remove images', self)
        self.remove_images_button.clicked.connect(self.remove_images)
        actions.addWidget(self.remove_images_button)
        actions.addStretch(1)
        self.stop_button = QPushButton('Stop', self)
        self.stop_button.clicked.connect(self.stop_request)
        actions.addWidget(self.stop_button)
        self.send_button = QPushButton('Send', self)
        self.send_button.clicked.connect(self.send_message)
        actions.addWidget(self.send_button)
        actions.addWidget(QSizeGrip(self))
        layout.addLayout(actions)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._flush_partial)
        self._rendering = False
        for widget in (self, self.header, self.title_label):
            widget.installEventFilter(self)
        self.setMinimumSize(360, 360)
        self.resize(520, 660)
        self._rebuild_history()
        self._set_pending(False)
        if self.state.get('load_warning'):
            self.status_label.setText('History could not be read. Clear to start fresh; the original file has been kept.')

    def configure(self, providers, default_effort='default'):
        if self.pending:
            return
        self.providers = [deepcopy(p) for p in providers if p.get('api_interface') == 'responses']
        preferred = self.state.get('selected_provider') or self.state.get('provider_identity')
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider in self.providers:
            self.provider_combo.addItem(provider['name'])
        index = next((i for i, p in enumerate(self.providers) if provider_identity(p) == preferred), 0)
        self.provider_combo.setCurrentIndex(index if self.providers else -1)
        self.provider_combo.blockSignals(False)
        self._provider_changed()
        if not self.providers:
            self.status_label.setText('Configure a Responses API provider in Settings to use continuous chat.')
        self.send_button.setEnabled(bool(self.providers))

    def _provider_changed(self, *_):
        index = self.provider_combo.currentIndex()
        if not 0 <= index < len(self.providers):
            return
        provider = self.providers[index]
        saved_identity = self.state.get('selected_provider') or self.state.get('provider_identity')
        saved_model = self.state.get('model') if saved_identity == provider_identity(provider) else ''
        self.state['selected_provider'] = provider_identity(provider)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(list(dict.fromkeys([provider.get('model', 'gpt-4o')] + provider.get('models', []))))
        self.model_combo.setCurrentText(saved_model or provider.get('model', 'gpt-4o'))
        self.model_combo.blockSignals(False)
        self._preferences_changed()

    def _preferences_changed(self, *_):
        if self.pending:
            return
        self.state['model'] = self.model_combo.currentText().strip()
        self.state['reasoning_effort'] = self.effort_combo.currentText()
        self._save()

    def _save(self):
        if self.state.get('load_warning'):
            return False
        try:
            if hasattr(self, 'input'):
                self.state['draft_text'] = self._turn['text'] if self.pending and self._turn else self.input.toPlainText()
                self.state['draft_images'] = deepcopy(self._turn['images'] if self.pending and self._turn else self.attachments)
            self.store.save(self.state)
            return True
        except OSError:
            self.status_label.setText('Could not save chat history. Check the data folder permissions.')
            return False

    def _set_pending(self, pending):
        self.pending = pending
        for control in (self.provider_combo, self.model_combo, self.effort_combo, self.image_button,
                        self.remove_images_button, self.send_button, self.input):
            control.setEnabled(not pending)
        self.send_button.setEnabled(not pending and bool(self.providers))
        self.stop_button.setEnabled(pending)

    def _rebuild_history(self):
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._browsers.clear()
        for message in self.state.get('messages', []):
            self._add_message(message)

    def _add_message(self, message):
        card = QWidget(self.content)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 4, 0, 4)
        label = QLabel('You' if message.get('role') == 'user' else 'Assistant', card)
        card_layout.addWidget(label)
        for image in message.get('images', []):
            try:
                data = base64.b64decode(image['data_url'].split(',', 1)[1], validate=True)
                pixmap = QPixmap()
                if not pixmap.loadFromData(data):
                    continue
                preview = QLabel(card)
                preview.setPixmap(pixmap.scaled(380, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                preview.setToolTip(str(image.get('name', 'Image')))
                card_layout.addWidget(preview)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        browser = AnswerBrowser(card)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.anchorClicked.connect(self._open_link)
        browser.document().setDefaultStyleSheet(f'a {{ color:{theme.ACCENT_LIGHT}; }}')
        browser.document().setTextWidth(max(100, self.width() - 72))
        if message.get('role') == 'user':
            browser.setPlainText(message.get('text', ''))
        else:
            browser.setMarkdown(message.get('text', ''))
        browser.setMinimumWidth(0)
        browser.document().documentLayout().documentSizeChanged.connect(lambda *_: self._fit_browser(browser))
        browser.selectionChanged.connect(self._selection_changed)
        browser.viewport().installEventFilter(self)
        card_layout.addWidget(browser)
        self._browsers.append(browser)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, card)
        self._fit_browser(browser)
        return browser

    def _fit_browser(self, browser):
        if self.disposed:
            return
        height = max(32, round(browser.document().size().height()) + 12)
        if browser.height() != height:
            browser.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for browser in self._browsers:
            browser.document().setTextWidth(max(100, self.width() - 72))
            self._fit_browser(browser)

    def show_chat(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def choose_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add images', '', 'Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)')
        self.add_images(paths)

    def add_images(self, paths):
        if self.pending:
            return
        try:
            additions = []
            for path in paths:
                source = Path(path)
                if source.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError('Each image must be smaller than 20 MB.')
                image = QImage(str(source))
                if image.isNull():
                    raise ValueError('Could not open the selected image.')
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, 'PNG') or len(data) > 20 * 1024 * 1024:
                    raise ValueError('The decoded image exceeds 20 MB.')
                additions.append({'name': source.name,
                                  'data_url': 'data:image/png;base64,' + base64.b64encode(bytes(data)).decode('ascii')})
            self.attachments.extend(additions)
            self._update_attachments()
        except (OSError, ValueError) as error:
            self.status_label.setText(str(error))

    def remove_images(self):
        self.attachments.clear()
        self._update_attachments()

    def _update_attachments(self):
        self.attachment_label.setText('Images: ' + ', '.join(item['name'] for item in self.attachments) if self.attachments else '')

    def send_message(self):
        if self.pending or self.disposed or not self.providers:
            return False
        if self.state.get('load_warning'):
            self.status_label.setText('Clear the unreadable history before starting a new conversation.')
            return False
        text = self.input.toPlainText().strip()
        if not text and not self.attachments:
            return False
        provider = deepcopy(self.providers[self.provider_combo.currentIndex()])
        provider['model'] = self.model_combo.currentText().strip()
        provider['reasoning_effort'] = self.effort_combo.currentText()
        if not provider['model']:
            self.status_label.setText('Choose a model first.')
            return False
        identity = provider_identity(provider)
        previous_id = self.state.get('previous_response_id')
        if previous_id and self.state.get('provider_identity') != identity:
            self.status_label.setText('This conversation belongs to another provider. Select it again, or Clear to start a new conversation.')
            return False
        self.generation += 1
        token = self.generation
        self._turn = {'role': 'user', 'text': text, 'images': deepcopy(self.attachments)}
        self._partial = ''
        self._turn_provider = identity
        self._add_message(self._turn)
        self._reply_browser = self._add_message({'role': 'assistant', 'text': ''})
        self._draft_restored = False
        self._set_pending(True)
        self.status_label.setText('Generating…')
        self.input.clear()
        self.remove_images()
        self._save()
        self._scroll_to_bottom()
        coro = request_chat(provider, text, self._turn['images'], previous_id,
                            on_delta=lambda delta: self.executor.post(self._delta, token, delta))
        try:
            self.request = self.executor.submit(coro,
                on_result=lambda result: self._complete(token, result),
                on_error=lambda error: self._failed(token, error))
        except Exception as error:
            coro.close()
            self._failed(token, error)
        return True

    def _delta(self, token, delta):
        if token != self.generation or not self.pending or self.disposed:
            return
        self._partial += delta
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _selection_changed(self):
        if not self._rendering and hasattr(self, '_reply_browser') and not self._reply_browser.textCursor().hasSelection():
            self._render_timer.start()

    def _flush_partial(self, force=False):
        if self.disposed or not hasattr(self, '_reply_browser'):
            return
        cursor = self._reply_browser.textCursor()
        if cursor.hasSelection() and not force:
            return
        selection = (cursor.anchor(), cursor.position()) if cursor.hasSelection() else None
        bar = self.scroll.verticalScrollBar()
        old, tail = bar.value(), bar.value() >= bar.maximum() - 4
        self._rendering = True
        try:
            self._reply_browser.setMarkdown(self._partial)
            if selection:
                length = self._reply_browser.document().characterCount() - 1
                restored = QTextCursor(self._reply_browser.document())
                restored.setPosition(min(selection[0], length))
                restored.setPosition(min(selection[1], length), QTextCursor.MoveMode.KeepAnchor)
                self._reply_browser.setTextCursor(restored)
            self._fit_browser(self._reply_browser)
        finally:
            self._rendering = False
        self.content.layout().activate()
        if tail:
            self._scroll_to_bottom()
        else:
            bar.setValue(old)

    def _scroll_to_bottom(self):
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()) if not self.disposed else None)

    def _complete(self, token, result):
        if token != self.generation or not self.pending or self.disposed:
            return
        self._partial = result['text']
        self._render_timer.stop()
        self._flush_partial(force=True)
        self.state['messages'].extend([deepcopy(self._turn), {'role': 'assistant', 'text': result['text'],
                                                            'model': self.model_combo.currentText()}])
        self.state['previous_response_id'] = result['response_id']
        self.state['provider_identity'] = self._turn_provider
        self.state['model'] = self.model_combo.currentText()
        self.state['reasoning_effort'] = self.effort_combo.currentText()
        self.request = None
        self._set_pending(False)
        self.status_label.setText('')
        self._save()
        self.input.setFocus() if self.isVisible() else None

    def _restore_draft(self):
        if self._turn:
            self.input.setPlainText(self._turn['text'])
            self.attachments = deepcopy(self._turn['images'])
            self._update_attachments()
        self._render_timer.stop()
        self._rebuild_history()
        if hasattr(self, '_reply_browser'):
            del self._reply_browser

    def _failed(self, token, error):
        if token != self.generation or not self.pending or self.disposed:
            return
        self.request = None
        self._set_pending(False)
        self._restore_draft()
        code = getattr(error, 'status_code', None)
        if code in (400, 404) and self.state.get('previous_response_id'):
            self.status_label.setText('Could not continue the saved response. Check the provider, or Clear to start a new conversation.')
        else:
            self.status_label.setText(f'Request failed (HTTP {code}). Draft restored.' if isinstance(code, int) else 'Request failed. Draft restored; check the Responses provider and connection.')

    def stop_request(self):
        if not self.pending:
            return
        self.generation += 1
        if self.request is not None:
            self.request.cancel()
        self.request = None
        self._set_pending(False)
        self._restore_draft()
        self.status_label.setText('Stopped. Draft restored.')

    def clear_history(self):
        self.stop_request()
        self.generation += 1
        self.state = {'messages': [], 'previous_response_id': None, 'provider_identity': None,
                      'model': self.model_combo.currentText(), 'reasoning_effort': self.effort_combo.currentText()}
        self._turn = None
        self._partial = ''
        self.input.clear()
        self.remove_images()
        if hasattr(self, '_reply_browser'):
            del self._reply_browser
        self._rebuild_history()
        self.status_label.setText('')
        self._save()

    def _open_link(self, url):
        if safe_url(url.toString()):
            QDesktopServices.openUrl(url)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and any(watched is browser.viewport() for browser in self._browsers):
            bar = self.scroll.verticalScrollBar()
            delta = event.pixelDelta().y() or round(event.angleDelta().y() / 120 * 60)
            bar.setValue(bar.value() - delta)
            return True
        if watched in (self, self.header, self.title_label):
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self.close()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        # Closing is hiding: no destruction or cancellation, so reopening resumes.
        self._save()
        super().closeEvent(event)

    def shutdown(self):
        self.stop_request()
        self._save()
        self.disposed = True
        self._render_timer.stop()
        self.hide()
