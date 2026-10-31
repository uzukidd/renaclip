"""Persistent, always-on-top Responses conversation with a reusable Qt window."""
from copy import deepcopy
from pathlib import Path
import base64

from PySide6.QtCore import Qt, QEvent, QTimer, QBuffer, QByteArray, QIODevice, QSize, QRectF, QPointF
from PySide6.QtGui import QImage, QPixmap, QDesktopServices, QTextCursor, QTextBlockFormat, QFont, QIcon, QPainter, QPen, QColor
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QComboBox,
                               QVBoxLayout, QHBoxLayout, QScrollArea, QFileDialog,
                               QSizeGrip, QLayout, QCheckBox)

from config_loader import VALID_REASONING_EFFORTS
from interfaces import theme
from interfaces.screenshot_overlay import AnswerBrowser, QuestionEdit, safe_url
from openai_client.desktop_chat import DesktopChatStore, provider_identity, request_chat
from openai_client import fetch_available_models


class ChatMessageBrowser(AnswerBrowser):
    def setMarkdown(self, source):
        super().setMarkdown(source)
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            fmt.setTopMargin(14 if fmt.headingLevel() else 4)
            fmt.setBottomMargin(12 if fmt.headingLevel() else 8)
            fmt.setLineHeight(130, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.setBlockFormat(fmt)
            block = block.next()


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
        self._resize_origin = None
        self._resize_edges_active = Qt.Edge(0)
        self._browsers = []
        self._draft_restored = False
        self._model_refresh_token = 0
        self._model_request = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setObjectName('conversationWindow')
        self.setWindowTitle('RenaChat')
        self.setStyleSheet('''
            QWidget {color:#e3e3e3;font-family:'Segoe UI','Microsoft YaHei UI';font-size:14px;}
            QWidget#conversationWindow {background:#101010;border:1px solid #292929;border-radius:20px;}
            QWidget#chatContent,QScrollArea,QScrollArea>QWidget>QWidget {background:#101010;border:0;}
            QWidget#composer {background:#202020;border-radius:28px;}
            QWidget#userBubble {background:#1c1c1c;border-radius:24px;}
            QTextBrowser {background:transparent;border:0;padding:0;selection-background-color:#304b85;}
            QPlainTextEdit {background:transparent;border:0;padding:0;selection-background-color:#304b85;font-size:15px;}
            QPushButton {background:transparent;border:0;border-radius:16px;padding:6px 10px;color:#c4c7c5;}
            QPushButton:hover {background:#303030;color:#ffffff;}
            QPushButton:disabled {color:#626262;}
            QPushButton#sendButton {background:#2948ad;color:#e0e7ff;border-radius:18px;font-size:24px;font-weight:600;padding:0;}
            QPushButton#sendButton:hover {background:#395dce;}
            QPushButton#sendButton:disabled {background:#30384c;color:#747c90;}
            QComboBox {background:transparent;border:0;border-radius:12px;padding:5px 17px 5px 7px;color:#c4c7c5;}
            QComboBox:hover {background:#2b2b2b;}
            QComboBox:disabled {color:#626262;}
            QComboBox::drop-down {border:0;width:15px;}
            QComboBox QAbstractItemView {background:#242424;color:#e3e3e3;selection-background-color:#39445b;border:1px solid #393939;outline:0;}
            QLineEdit {background:transparent;border:0;color:#e3e3e3;selection-background-color:#304b85;}
            QLabel#statusLabel {color:#a6a6a6;font-size:12px;}
            QScrollBar:vertical {background:transparent;width:6px;margin:0;}
            QScrollBar::handle:vertical {background:#424242;min-height:28px;border-radius:3px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
        '''.replace('#2948ad', theme.ACCENT).replace('#395dce', theme.ACCENT_LIGHT)
           .replace('#e0e7ff', theme.TEXT).replace('#304b85', theme.ACCENT)
           .replace('#39445b', theme.SURFACE_ACCENT).replace('#c4c7c5', theme.ACCENT_LIGHT))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 12)
        layout.setSpacing(8)
        self.header = QWidget(self)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header.setToolTip('Drag to move; double-click to close')
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel('RenaChat', self.header)
        row.addWidget(self.title_label)
        self.provider_combo = QComboBox(self.header)
        self.provider_combo.setMaximumWidth(160)
        self.provider_combo.setToolTip('Provider')
        row.addWidget(self.provider_combo)
        row.addStretch(1)
        self.clear_button = QPushButton(self.header)
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.setStyleSheet('padding:0;border-radius:16px;')
        icon = QPixmap(48, 48)
        icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.ACCENT_LIGHT), 3, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawArc(QRectF(9, 11, 28, 28), 95 * 16, 285 * 16)
        painter.drawLine(QPointF(23, 25), QPointF(35, 13))
        painter.drawLine(QPointF(35, 13), QPointF(39, 17))
        painter.drawLine(QPointF(39, 17), QPointF(27, 29))
        painter.drawLine(QPointF(27, 29), QPointF(21, 31))
        painter.drawLine(QPointF(21, 31), QPointF(23, 25))
        painter.end()
        self.clear_button.setIcon(QIcon(icon))
        self.clear_button.setIconSize(QSize(24, 24))
        self.clear_button.setAccessibleName('New conversation')
        self.clear_button.setToolTip('New conversation')
        self.clear_button.clicked.connect(self.clear_history)
        row.addWidget(self.clear_button)
        layout.addWidget(self.header)
        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(False)
        self.model_combo.setToolTip('Select model')
        self.model_combo.setMaxVisibleItems(12)
        self.model_combo.setStyleSheet('QComboBox::drop-down {width:20px;}')
        self.model_combo.setMinimumWidth(100)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.refresh_models_button = QPushButton('↻', self)
        self.refresh_models_button.setToolTip('Refresh models')
        self.refresh_models_button.setFixedWidth(36)
        self.refresh_models_button.clicked.connect(self.refresh_models)
        self.effort_combo = QComboBox(self)
        self.effort_combo.addItems(list(VALID_REASONING_EFFORTS))
        self.effort_combo.setToolTip('Reasoning effort')
        self.effort_combo.setCurrentText(self.state.get('reasoning_effort', 'default'))
        self.model_combo.setMaximumWidth(220)
        self.effort_combo.setMaximumWidth(95)
        self.effort_combo.setMinimumWidth(75)
        options.addWidget(self.model_combo, 1)
        options.addWidget(self.refresh_models_button)
        options.addWidget(self.effort_combo)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.model_combo.currentTextChanged.connect(self._preferences_changed)
        self.effort_combo.currentTextChanged.connect(self._preferences_changed)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.content = QWidget(self.scroll)
        self.content.setObjectName('chatContent')
        self.messages_layout = QVBoxLayout(self.content)
        self.messages_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.messages_layout.setContentsMargins(2, 16, 8, 16)
        self.messages_layout.setSpacing(24)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.status_label = QLabel('', self)
        self.status_label.setObjectName('statusLabel')
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.attachment_label = QLabel('', self)
        self.attachment_label.setTextFormat(Qt.TextFormat.PlainText)
        self.attachment_label.setWordWrap(True)
        layout.addWidget(self.attachment_label)
        self.composer = QWidget(self)
        self.composer.setObjectName('composer')
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(14, 12, 14, 10)
        composer_layout.setSpacing(6)
        self.input = QuestionEdit(self.composer)
        self.input.setPlaceholderText('Ask anything')
        self.input.setFixedHeight(44)
        self.input.submit.connect(self.send_message)
        self.input.setPlainText(self.state.get('draft_text', ''))
        self._update_attachments()
        composer_layout.addWidget(self.input)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.image_button = QPushButton('+', self)
        self.image_button.setToolTip('Add images')
        self.image_button.setFixedSize(34, 34)
        self.image_button.setStyleSheet('font-size:26px;padding:0;')
        self.image_button.clicked.connect(self.choose_images)
        actions.addWidget(self.image_button)
        self.web_search_checkbox = QCheckBox('Web search', self)
        self.web_search_checkbox.setToolTip('Web search')
        self.web_search_checkbox.setChecked(bool(self.state.get('web_search', False)))
        self.web_search_checkbox.setStyleSheet(
            f'QCheckBox {{color:{theme.ACCENT_LIGHT};spacing:5px;}} '
            f'QCheckBox::indicator {{width:14px;height:14px;border:1px solid {theme.OUTLINE};border-radius:3px;background:{theme.SURFACE};}} '
            f'QCheckBox::indicator:checked {{background:{theme.ACCENT};border-color:{theme.ACCENT_LIGHT};}}')
        self.web_search_checkbox.toggled.connect(self._preferences_changed)
        actions.addWidget(self.web_search_checkbox)
        self.remove_images_button = QPushButton('× images', self)
        self.remove_images_button.setToolTip('Remove attached images')
        self.remove_images_button.hide()
        self.remove_images_button.clicked.connect(self.remove_images)
        actions.addWidget(self.remove_images_button)
        actions.addStretch(1)
        actions.addLayout(options)
        self.stop_button = QPushButton('■', self)
        self.stop_button.setToolTip('Stop generating')
        self.stop_button.setFixedSize(36, 36)
        self.stop_button.clicked.connect(self.stop_request)
        actions.addWidget(self.stop_button)
        self.send_button = QPushButton('↑', self)
        self.send_button.setObjectName('sendButton')
        self.send_button.setToolTip('Send')
        self.send_button.setFixedSize(36, 36)
        self.send_button.clicked.connect(self.send_message)
        actions.addWidget(self.send_button)
        composer_layout.addLayout(actions)
        layout.addWidget(self.composer)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip = QSizeGrip(self)
        grip.setFixedSize(12, 8)
        grip_row.addWidget(grip)
        layout.addLayout(grip_row)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._flush_partial)
        self._rendering = False
        self.setMouseTracking(True)
        for widget in self.findChildren(QWidget):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        self.installEventFilter(self)
        self.setMinimumSize(360, 360)
        self.resize(520, 660)
        self._rebuild_history()
        self._update_attachments()
        self._set_pending(False)
        if self.state.get('load_warning'):
            self.status_label.setText('History could not be read. Clear to start fresh; the original file has been kept.')

    def configure(self, providers, default_effort='default'):
        if self.pending:
            return
        self.providers = [deepcopy(p) for p in providers]
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
            self.model_combo.clear()
            self.status_label.setText('Configure an OpenAI-compatible provider in Settings.')
        else:
            self.status_label.setText('')
        self.refresh_models_button.setEnabled(bool(self.providers) and self._model_request is None)
        self.send_button.setEnabled(bool(self.providers))

    def _provider_changed(self, *_):
        self._model_refresh_token += 1
        if self._model_request is not None:
            self._model_request.cancel()
            self._model_request = None
        self.refresh_models_button.setEnabled(not self.pending and bool(self.providers))
        index = self.provider_combo.currentIndex()
        if not 0 <= index < len(self.providers):
            return
        provider = self.providers[index]
        saved_identity = self.state.get('selected_provider') or self.state.get('provider_identity')
        saved_model = self.state.get('model') if saved_identity == provider_identity(provider) else ''
        self.state['selected_provider'] = provider_identity(provider)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        selected = saved_model or provider.get('model', 'gpt-4o')
        self.model_combo.addItems(list(dict.fromkeys([selected, provider.get('model', 'gpt-4o')] + provider.get('models', []))))
        self.model_combo.setCurrentText(selected)
        self.model_combo.blockSignals(False)
        self._preferences_changed()

    def refresh_models(self):
        index = self.provider_combo.currentIndex()
        if self.pending or self.disposed or self._model_request is not None or not 0 <= index < len(self.providers):
            return
        provider = self.providers[index]
        self._model_refresh_token += 1
        token = self._model_refresh_token
        self.refresh_models_button.setEnabled(False)
        self.status_label.setText('Loading models…')
        coro = fetch_available_models(api_key=provider.get('api_key'), base_url=provider.get('base_url') or None)
        try:
            self._model_request = self.executor.submit(coro,
                on_result=lambda models: self._models_loaded(token, models),
                on_error=lambda error: self._models_loaded(token, []))
        except Exception:
            coro.close()
            self._models_loaded(token, [])

    def _models_loaded(self, token, models):
        if self.disposed or token != self._model_refresh_token:
            return
        self._model_request = None
        self.refresh_models_button.setEnabled(not self.pending and bool(self.providers))
        if not models:
            self.status_label.setText('No models returned. Check the endpoint/key and refresh again.')
            return
        selected = self.model_combo.currentText().strip()
        models = list(dict.fromkeys(str(model).strip() for model in models if str(model).strip()))
        self.providers[self.provider_combo.currentIndex()]['models'] = models
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(list(dict.fromkeys(([selected] if selected else []) + models)))
        self.model_combo.setCurrentText(selected or models[0])
        self.model_combo.blockSignals(False)
        self._preferences_changed()
        self.status_label.setText(f'{len(models)} models loaded.')

    def _preferences_changed(self, *_):
        if self.pending:
            return
        self.state['model'] = self.model_combo.currentText().strip()
        self.state['reasoning_effort'] = self.effort_combo.currentText()
        self.state['web_search'] = self.web_search_checkbox.isChecked()
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
                        self.remove_images_button, self.send_button, self.input, self.web_search_checkbox):
            control.setEnabled(not pending)
        self.send_button.setEnabled(not pending and bool(self.providers))
        self.stop_button.setEnabled(pending)
        self.stop_button.setVisible(pending)
        self.send_button.setVisible(not pending)
        self.refresh_models_button.setEnabled(not pending and bool(self.providers) and self._model_request is None)

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
        row = QHBoxLayout(card)
        row.setContentsMargins(0, 0, 0, 0)
        user = message.get('role') == 'user'
        body = QWidget(card)
        body.setObjectName('userBubble' if user else 'assistantMessage')
        if user:
            body.setMaximumWidth(max(240, round(self.width() * 0.78)))
            row.addStretch(1)
        row.addWidget(body, 0 if user else 1)
        card_layout = QVBoxLayout(body)
        card_layout.setContentsMargins(18, 12, 18, 12) if user else card_layout.setContentsMargins(0, 6, 0, 6)
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
        browser = ChatMessageBrowser(body)
        font = QFont('Segoe UI')
        font.setPixelSize(16)
        browser.setFont(font)
        browser.document().setDefaultFont(font)
        browser.setObjectName('userMessage' if user else 'assistantText')
        browser.document().setDocumentMargin(0)
        browser.setStyleSheet('font-size:16px;color:#e3e3e3;')
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.anchorClicked.connect(self._open_link)
        browser.document().setDefaultStyleSheet('a {color:#a8c7fa;} p {margin-top:8px;margin-bottom:12px;} h1,h2,h3 {margin-top:18px;margin-bottom:10px;} li {margin-bottom:6px;}')
        browser.document().setTextWidth(max(100, self.width() * (0.78 if user else 1) - 80))
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
            user = browser.objectName() == 'userMessage'
            if user:
                browser.parentWidget().setMaximumWidth(max(240, round(self.width() * 0.78)))
            browser.document().setTextWidth(max(100, self.width() * (0.78 if user else 1) - 80))
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
        if hasattr(self, 'remove_images_button'):
            self.remove_images_button.setVisible(bool(self.attachments))
        self.attachment_label.setVisible(bool(self.attachments))
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
        provider['web_search'] = self.web_search_checkbox.isChecked()
        if not provider['model']:
            self.status_label.setText('Choose a model first.')
            return False
        identity = provider_identity(provider)
        previous_id = self.state.get('previous_response_id')
        if previous_id and self.state.get('provider_identity') != identity:
            self.status_label.setText('This conversation belongs to another provider. Select it again, or Clear to start a new conversation.')
            return False
        if self._model_request is not None:
            self._model_refresh_token += 1
            self._model_request.cancel()
            self._model_request = None
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
                            on_delta=lambda delta: self.executor.post(self._delta, token, delta),
                            history=deepcopy(self.state['messages']),
                            replay_input=deepcopy(self.state.get('replay_input', [])),
                            use_history=bool(self.state.get('use_history', False)))
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
        if 'replay_input' in result:
            self.state['replay_input'] = result['replay_input']
            self.state['use_history'] = result.get('use_history', False)
        self.state['provider_identity'] = self._turn_provider
        self.state['model'] = self.model_combo.currentText()
        self.state['reasoning_effort'] = self.effort_combo.currentText()
        self.state['web_search'] = self.web_search_checkbox.isChecked()
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
        body = getattr(error, 'body', None)
        detail = body.get('error', body) if isinstance(body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        param = str(detail.get('param') or getattr(error, 'param', '') or '')
        error_code = str(detail.get('code') or getattr(error, 'code', '') or '')
        message = str(detail.get('message') or '')
        # Display only structured API error details, never a raw exception that
        # may include HTTP headers or the complete request with attached images.
        for provider in self.providers:
            for key in ('api_key', 'base_url'):
                secret = provider.get(key)
                if secret:
                    message = message.replace(secret, '[redacted]')
        message = ' '.join(message.split())[:400]
        id_error = ('previous_response_id' in param.lower() or
                    'previous_response' in error_code.lower() or
                    ('response' in message.lower() and any(word in message.lower() for word in ('not found', 'expired', 'does not exist'))))
        if id_error and self.state.get('previous_response_id'):
            summary = 'Previous response is unavailable or expired. History is kept; start a new conversation to reset the response chain.'
        elif 'web_search' in (param + error_code + message).lower() or param == 'tools':
            summary = 'Web search was rejected. Disable 联网 or select a model/provider that supports web search. Draft restored.'
        elif 'reasoning' in (param + error_code + message).lower():
            summary = 'Reasoning effort was rejected. Try default or a supported model. Draft restored.'
        else:
            summary = f'Request failed (HTTP {code}). Draft restored.' if isinstance(code, int) else 'Request failed. Draft restored; check the provider and connection.'
        self.status_label.setText(summary + ('\n' + message if message else ''))

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
                      'model': self.model_combo.currentText(), 'reasoning_effort': self.effort_combo.currentText(),
                      'web_search': self.web_search_checkbox.isChecked()}
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

    def _resize_edges(self, point):
        margin = 8
        edges = Qt.Edge(0)
        if point.x() < margin:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if point.y() < margin:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _resize_to(self, global_point):
        start, original = self._resize_origin
        delta = global_point - start
        rect = type(original)(original)
        edges = self._resize_edges_active
        if edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(original.left() + delta.x(), original.right() - self.minimumWidth() + 1))
        if edges & Qt.Edge.RightEdge:
            rect.setRight(max(original.right() + delta.x(), original.left() + self.minimumWidth() - 1))
        if edges & Qt.Edge.TopEdge:
            rect.setTop(min(original.top() + delta.y(), original.bottom() - self.minimumHeight() + 1))
        if edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(original.bottom() + delta.y(), original.top() + self.minimumHeight() - 1))
        self.setGeometry(rect)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._resize_edges(point)
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and edges:
                self._resize_edges_active = edges
                self._resize_origin = (event.globalPosition().toPoint(), self.geometry())
                return True
            if event.type() == QEvent.Type.MouseMove:
                if self._resize_origin is not None:
                    self._resize_to(event.globalPosition().toPoint())
                    return True
                if edges:
                    diagonal_a = Qt.Edge.LeftEdge | Qt.Edge.TopEdge
                    diagonal_b = Qt.Edge.RightEdge | Qt.Edge.BottomEdge
                    horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
                    vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
                    cursor = (Qt.CursorShape.SizeFDiagCursor if edges in (diagonal_a, diagonal_b) else Qt.CursorShape.SizeBDiagCursor) if horizontal and vertical else (Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor)
                    watched.setCursor(cursor)
                elif watched not in (self.header, self.title_label):
                    watched.unsetCursor()
            if event.type() == QEvent.Type.MouseButtonRelease and self._resize_origin is not None:
                self._resize_origin = None
                self._resize_edges_active = Qt.Edge(0)
                return True
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
        self._model_refresh_token += 1
        if self._model_request is not None:
            self._model_request.cancel()
            self._model_request = None
        self.stop_request()
        self._save()
        self.disposed = True
        self._render_timer.stop()
        self.hide()
