"""Native Qt configuration windows for RenaClip."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QTextEdit, QCheckBox, QComboBox,
    QPushButton, QDialogButtonBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QGroupBox, QScrollArea,
)
from config_loader import (
    load_config, save_config, VALID_MODIFIERS, AVAILABLE_MODELS,
    VALID_BACKENDS, VALID_REASONING_EFFORTS, normalize_reasoning_effort,
    normalize_openai_providers,
)
from constants import APP_ROOT
from openai_client import fetch_available_models

APP_NAME = "RenaClip"
UI_LOCK_PATH = Path(__file__).resolve().parent / ".renaclip_ui.lock"


def _combo(values, current):
    widget = QComboBox()
    widget.addItems(list(values))
    widget.setCurrentText(str(current))
    return widget


def _buttons(dialog, layout):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.buttons = buttons


class SkillDialog(QDialog):
    def __init__(self, skill=None, parent=None):
        super().__init__(parent)
        self._original = deepcopy(skill or {})
        self.setWindowTitle("Edit Skill" if skill is not None else "Add Skill")
        self.resize(520, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_field = QLineEdit(self._original.get("name", ""))
        self.description_field = QTextEdit(self._original.get("description", ""))
        self.description_field.setMaximumHeight(100)
        self.prompt_field = QTextEdit(self._original.get("prompt", ""))
        self.web_search_checkbox = QCheckBox("Enable web search")
        self.web_search_checkbox.setChecked(bool(self._original.get("web_search", False)))
        form.addRow("Title", self.title_field)
        form.addRow("Description", self.description_field)
        form.addRow("Prompt", self.prompt_field)
        form.addRow(self.web_search_checkbox)
        layout.addLayout(form)
        _buttons(self, layout)

    def value(self):
        return {**self._original, "name": self.title_field.text().strip() or "Unnamed",
                "description": self.description_field.toPlainText(),
                "prompt": self.prompt_field.toPlainText(),
                "web_search": self.web_search_checkbox.isChecked()}


class _ModelWorker(QThread):
    result = Signal(object)
    error = Signal(object)

    def __init__(self, coroutine, parent=None):
        super().__init__(parent)
        self.coroutine = coroutine

    def run(self):
        try:
            self.result.emit(asyncio.run(self.coroutine))
        except Exception as exc:
            self.error.emit(exc)


class ProviderDialog(QDialog):
    def __init__(self, provider, parent=None, *, executor=None):
        super().__init__(parent)
        self.executor = executor
        self._original = deepcopy(provider)
        self._workers = []
        self.setWindowTitle("Provider")
        self.resize(540, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_field = QLineEdit(provider.get("name", ""))
        self.api_key_field = QLineEdit(provider.get("api_key", ""))
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.base_url_field = QLineEdit(provider.get("base_url", ""))
        self.base_url_field.setPlaceholderText("https://api.openai.com/v1")
        self.interface_combo = QComboBox()
        self.interface_combo.addItem("Chat Completions", "chat_completions")
        self.interface_combo.addItem("Responses API", "responses")
        self.interface_combo.setCurrentIndex(max(0, self.interface_combo.findData(provider.get("api_interface"))))
        self.models_field = QTextEdit("\n".join(provider.get("models") or []))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(list(dict.fromkeys([provider.get("model") or "gpt-4o", *(provider.get("models") or [])])))
        self.model_combo.setCurrentText(provider.get("model") or "gpt-4o")
        self.models_field.textChanged.connect(self._update_model_options)
        self.streaming_checkbox = QCheckBox("Streaming mode")
        self.streaming_checkbox.setChecked(bool(provider.get("streaming", False)))
        self.refresh_button = QPushButton("Refresh models")
        self.refresh_button.clicked.connect(self.refresh_models)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        for label, widget in [("Provider name", self.name_field), ("API key", self.api_key_field),
                              ("Base URL", self.base_url_field), ("API interface", self.interface_combo),
                              ("Model IDs (one per line)", self.models_field), ("Default model", self.model_combo)]:
            form.addRow(label, widget)
        form.addRow(self.streaming_checkbox)
        form.addRow(self.refresh_button)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        _buttons(self, layout)

    def _model_ids(self):
        return list(dict.fromkeys(line.strip() for line in self.models_field.toPlainText().replace(",", "\n").splitlines() if line.strip()))

    def _update_model_options(self):
        current = self.model_combo.currentText()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(self._model_ids())
        self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def value(self):
        model = self.model_combo.currentText().strip() or "gpt-4o"
        models = self._model_ids()
        if model not in models:
            models.insert(0, model)
        return {"name": self.name_field.text().strip() or "Unnamed provider",
                "api_key": self.api_key_field.text().strip(), "base_url": self.base_url_field.text().strip(),
                "api_interface": self.interface_combo.currentData(), "models": models,
                "model": model, "streaming": self.streaming_checkbox.isChecked()}

    def refresh_models(self):
        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading models…")
        coroutine = fetch_available_models(api_key=self.api_key_field.text().strip(), base_url=self.base_url_field.text().strip() or None)
        if self.executor is not None:
            try:
                self.executor.submit(coroutine, on_result=self._models_loaded, on_error=self._models_failed)
            except Exception as exc:
                coroutine.close()
                self._models_failed(exc)
        else:
            # Workers belong to the application so closing this dialog cannot destroy
            # a live QThread. Qt disconnects callbacks when the dialog is destroyed.
            worker = _ModelWorker(coroutine, QApplication.instance())
            self._workers.append(worker)
            worker.result.connect(self._models_loaded)
            worker.error.connect(self._models_failed)
            worker.finished.connect(worker.deleteLater)
            worker.start()

    def _models_loaded(self, model_ids):
        if model_ids:
            current = self.model_combo.currentText()
            self.models_field.setPlainText("\n".join(model_ids))
            self.model_combo.setCurrentText(current if current in model_ids else model_ids[0])
        self.status_label.setText("Models refreshed" if model_ids else "No models returned")
        self.refresh_button.setEnabled(True)

    def _models_failed(self, error):
        self.status_label.setText(f"Unable to refresh models: {error}")
        self.refresh_button.setEnabled(True)


class SettingsDialog(QDialog):
    def __init__(self, gems, settings, parent=None, *, executor=None):
        super().__init__(parent)
        self.gems = deepcopy(gems)
        self.draft = deepcopy(settings)
        self.executor = executor
        self.providers = normalize_openai_providers(self.draft)
        self.setWindowTitle("Settings")
        self.resize(620, 720)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        form = QFormLayout()
        self.backend_combo = _combo(VALID_BACKENDS, self.draft.get("BACKEND", "openai"))
        self.hotkey_combo = _combo(VALID_MODIFIERS, self.draft.get("HOTKEY_MODIFIER", "ctrl"))
        self.continuous_chat_checkbox = QCheckBox("RenaChat (Hotkey + 9)")
        self.continuous_chat_checkbox.setChecked(self.draft.get("CONTINUOUS_CHAT_ENABLED", True) is True)
        self.screenshot_checkbox = QCheckBox("Screenshot Q&A (Hotkey + 0)")
        self.screenshot_checkbox.setChecked(bool(self.draft.get("SCREENSHOT_QA_ENABLED")))
        self.screenshot_web_search_checkbox = QCheckBox("Screenshot web search")
        self.screenshot_web_search_checkbox.setChecked(bool(self.draft.get("SCREENSHOT_QA_WEB_SEARCH")))
        self.screenshot_web_search_checkbox.setEnabled(self.screenshot_checkbox.isChecked())
        self.screenshot_checkbox.toggled.connect(self.screenshot_web_search_checkbox.setEnabled)
        form.addRow("Backend", self.backend_combo)
        form.addRow("Hotkey modifier", self.hotkey_combo)
        form.addRow(self.continuous_chat_checkbox)
        form.addRow(self.screenshot_checkbox)
        form.addRow(self.screenshot_web_search_checkbox)
        body.addLayout(form)
        self.providers_group = QGroupBox("OpenAI-compatible providers")
        provider_layout = QVBoxLayout(self.providers_group)
        self.provider_list = QListWidget()
        self.provider_list.itemDoubleClicked.connect(lambda item: self.edit_provider(self.provider_list.row(item)))
        provider_layout.addWidget(self.provider_list)
        actions = QHBoxLayout()
        self.add_provider_button = QPushButton("Add provider")
        self.edit_provider_button = QPushButton("Edit provider")
        self.delete_provider_button = QPushButton("Delete provider")
        self.add_provider_button.clicked.connect(self.add_provider)
        self.edit_provider_button.clicked.connect(lambda: self.edit_provider(self.provider_list.currentRow()))
        self.delete_provider_button.clicked.connect(lambda: self.delete_provider(self.provider_list.currentRow()))
        for button in (self.add_provider_button, self.edit_provider_button, self.delete_provider_button):
            actions.addWidget(button)
        provider_layout.addLayout(actions)
        body.addWidget(self.providers_group)
        self.gemini_group = QGroupBox("Gemini Web")
        gemini_form = QFormLayout(self.gemini_group)
        self.psid_field = QLineEdit(self.draft.get("GEMINI_PSID", ""))
        self.psidts_field = QLineEdit(self.draft.get("GEMINI_PSIDTS", ""))
        for field in (self.psid_field, self.psidts_field):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.browser_cookie_checkbox = QCheckBox("Log in via browser-cookie3")
        self.browser_cookie_checkbox.setChecked(bool(self.draft.get("GEMINI_USE_BROWSER_COOKIE")))
        self.cookie_browser_combo = _combo(("edge", "chrome"), self.draft.get("GEMINI_COOKIE_BROWSER", "edge"))
        self.proxy_field = QLineEdit(self.draft.get("GEMINI_PROXY", ""))
        self.gemini_model_combo = _combo(AVAILABLE_MODELS, self.draft.get("GEMINI_MODEL", "unspecified"))
        for label, widget in [("GEMINI_PSID", self.psid_field), ("GEMINI_PSIDTS", self.psidts_field),
                              ("Browser cookies", self.browser_cookie_checkbox), ("Cookie browser", self.cookie_browser_combo),
                              ("Proxy", self.proxy_field), ("Model", self.gemini_model_combo)]:
            gemini_form.addRow(label, widget)
        body.addWidget(self.gemini_group)
        self.browser_cookie_checkbox.toggled.connect(self._toggle_cookies)
        self.backend_combo.currentTextChanged.connect(self._toggle_backend)
        self._toggle_cookies(self.browser_cookie_checkbox.isChecked())
        self._toggle_backend(self.backend_combo.currentText())
        self._rebuild_providers()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        _buttons(self, layout)

    def _toggle_cookies(self, checked):
        self.psid_field.setEnabled(not checked)
        self.psidts_field.setEnabled(not checked)

    def _toggle_backend(self, backend):
        self.providers_group.setEnabled(backend == "openai")
        self.gemini_group.setEnabled(backend == "gemini")

    def _rebuild_providers(self):
        row = self.provider_list.currentRow()
        self.provider_list.clear()
        for provider in self.providers:
            self.provider_list.addItem(f"{provider['name']}\n{provider['api_interface']} · {provider['model']} · {provider['base_url'] or 'default endpoint'}")
        self.provider_list.setCurrentRow(min(max(row, 0), len(self.providers) - 1))
        self.delete_provider_button.setEnabled(len(self.providers) > 1)

    def _store_provider(self, value, index=None):
        value = deepcopy(value)
        existing = {p["name"] for i, p in enumerate(self.providers) if i != index}
        base = value["name"]
        suffix = 2
        while value["name"] in existing:
            value["name"] = f"{base} ({suffix})"
            suffix += 1
        old_name = self.providers[index]["name"] if index is not None else None
        if index is None:
            self.providers.append(value)
        else:
            self.providers[index] = value
        if index is None or self.draft.get("OPENAI_ACTIVE_PROVIDER") == old_name:
            self.draft["OPENAI_ACTIVE_PROVIDER"] = value["name"]
        self.draft["OPENAI_PROVIDERS"] = self.providers
        self._rebuild_providers()

    def edit_provider(self, index):
        if not 0 <= index < len(self.providers):
            return
        dialog = ProviderDialog(self.providers[index], self, executor=self.executor)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._store_provider(dialog.value(), index)

    def add_provider(self):
        dialog = ProviderDialog({"name": "New provider", "models": ["gpt-4o"], "model": "gpt-4o"}, self, executor=self.executor)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._store_provider(dialog.value())

    def delete_provider(self, index):
        if len(self.providers) <= 1 or not 0 <= index < len(self.providers):
            return
        removed = self.providers.pop(index)
        if self.draft.get("OPENAI_ACTIVE_PROVIDER") == removed["name"]:
            self.draft["OPENAI_ACTIVE_PROVIDER"] = self.providers[0]["name"]
        self.draft["OPENAI_PROVIDERS"] = self.providers
        self._rebuild_providers()

    def value(self):
        self.draft.update({
            "BACKEND": self.backend_combo.currentText(), "HOTKEY_MODIFIER": self.hotkey_combo.currentText(),
            "CONTINUOUS_CHAT_ENABLED": self.continuous_chat_checkbox.isChecked(),
            "SCREENSHOT_QA_ENABLED": self.screenshot_checkbox.isChecked(),
            "SCREENSHOT_QA_WEB_SEARCH": self.screenshot_web_search_checkbox.isChecked(),
            "GEMINI_PSID": self.psid_field.text().strip(), "GEMINI_PSIDTS": self.psidts_field.text().strip(),
            "GEMINI_USE_BROWSER_COOKIE": self.browser_cookie_checkbox.isChecked(),
            "GEMINI_COOKIE_BROWSER": self.cookie_browser_combo.currentText(),
            "GEMINI_PROXY": self.proxy_field.text().strip(), "GEMINI_MODEL": self.gemini_model_combo.currentText(),
            "OPENAI_PROVIDERS": self.providers,
        })
        normalize_openai_providers(self.draft)
        return deepcopy(self.draft)

    def accept(self):
        save_config(self.gems, self.value())
        super().accept()


class MainWindow(QMainWindow):
    configuration_changed = Signal()

    def __init__(self, parent=None, *, executor=None):
        super().__init__(parent)
        self.executor = executor
        self.hide_on_close = False
        self.setWindowTitle(APP_NAME)
        self.resize(920, 620)
        icon_path = APP_ROOT / "assets" / "renaclip_icon.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(APP_NAME))
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Reasoning"))
        self.reasoning_combo = _combo(VALID_REASONING_EFFORTS, "default")
        toolbar.addWidget(self.reasoning_combo)
        toolbar.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(240)
        toolbar.addWidget(self.model_combo)
        self.settings_button = QPushButton("Settings")
        self.add_skill_button = QPushButton("Add Skill")
        toolbar.addWidget(self.settings_button)
        toolbar.addWidget(self.add_skill_button)
        layout.addLayout(toolbar)
        self.skill_list = QListWidget()
        self.skill_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.skill_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.skill_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.skill_list.setSpacing(6)
        layout.addWidget(self.skill_list)
        self.skill_list.model().rowsMoved.connect(self._skills_reordered)
        self.skill_list.itemDoubleClicked.connect(lambda item: self.edit_skill(self.skill_list.row(item)))
        self.reasoning_combo.currentTextChanged.connect(self._reasoning_changed)
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.settings_button.clicked.connect(self.open_settings)
        self.add_skill_button.clicked.connect(lambda: self.edit_skill(None))
        self.refresh()

    def refresh(self):
        self.gems, self.settings = load_config()
        self.reasoning_combo.blockSignals(True)
        self.reasoning_combo.setCurrentText(normalize_reasoning_effort(self.settings.get("OPENAI_REASONING_EFFORT")))
        self.reasoning_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        selected = 0
        for provider in normalize_openai_providers(self.settings):
            self.model_combo.addItem(provider["name"])
            heading = self.model_combo.model().item(self.model_combo.count() - 1)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            models = list(dict.fromkeys([provider["model"], *provider.get("models", [])]))
            for model in models:
                self.model_combo.addItem("    " + model, (provider["name"], model))
                index = self.model_combo.count() - 1
                self.model_combo.setItemData(index, f"{provider['name']}: {model}", Qt.ItemDataRole.ToolTipRole)
                if provider["name"] == self.settings["OPENAI_ACTIVE_PROVIDER"] and model == provider["model"]:
                    selected = index
        self.model_combo.setCurrentIndex(selected)
        self.model_combo.blockSignals(False)
        self._rebuild_skills()

    def _persist(self):
        save_config(self.gems, self.settings)
        self.configuration_changed.emit()

    def _reasoning_changed(self, value):
        self.settings["OPENAI_REASONING_EFFORT"] = normalize_reasoning_effort(value)
        self._persist()

    def _model_changed(self, index):
        route = self.model_combo.itemData(index)
        if not route:
            return
        name, model = route
        self.settings["OPENAI_ACTIVE_PROVIDER"] = name
        for provider in self.settings["OPENAI_PROVIDERS"]:
            if provider["name"] == name:
                provider["model"] = model
                break
        self._persist()

    def _rebuild_skills(self):
        self.add_skill_button.setEnabled(len(self.gems) < 8)
        self.skill_list.clear()
        modifier = self.settings.get("HOTKEY_MODIFIER", "ctrl").title()
        for index, skill in enumerate(self.gems):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(skill))
            self.skill_list.addItem(item)
            card = QWidget()
            row = QHBoxLayout(card)
            text = QVBoxLayout()
            shortcut = f"  ·  {modifier}+{index + 1}" if index < 8 else ""
            title = QLabel(f"{skill.get('name', 'Unnamed')}{shortcut}" + ("  ·  Web search" if skill.get("web_search") else ""))
            font = title.font()
            font.setBold(True)
            title.setFont(font)
            text.addWidget(title)
            description = QLabel(skill.get("description", ""))
            description.setWordWrap(True)
            text.addWidget(description)
            row.addLayout(text, 1)
            edit = QPushButton("Edit")
            delete = QPushButton("Delete")
            edit.clicked.connect(lambda checked=False, entry=item: self.edit_skill(self.skill_list.row(entry)))
            delete.clicked.connect(lambda checked=False, entry=item: self.delete_skill(self.skill_list.row(entry)))
            row.addWidget(edit)
            row.addWidget(delete)
            item.setSizeHint(card.sizeHint())
            self.skill_list.setItemWidget(item, card)

    def _skills_reordered(self, *args):
        self.gems = [deepcopy(self.skill_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.skill_list.count())]
        self._persist()
        # Rebuild after the model finishes its move, so shortcut numbers update.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._rebuild_skills)

    def move_skill(self, source, destination):
        if 0 <= source < len(self.gems) and 0 <= destination < len(self.gems) and source != destination:
            self.gems.insert(destination, self.gems.pop(source))
            self._persist()
            self._rebuild_skills()

    def edit_skill(self, index=None):
        if index is None and len(self.gems) >= 8:
            return
        dialog = SkillDialog(self.gems[index] if index is not None else None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if index is None:
                self.gems.append(dialog.value())
            else:
                self.gems[index] = dialog.value()
            self._persist()
            self._rebuild_skills()

    def delete_skill(self, index):
        if 0 <= index < len(self.gems):
            self.gems.pop(index)
            self._persist()
            self._rebuild_skills()

    def open_settings(self):
        dialog = SettingsDialog(self.gems, self.settings, self, executor=self.executor)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.configuration_changed.emit()

    def closeEvent(self, event):
        if self.hide_on_close:
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)


def launch_ui(lock_path=None):
    """Run the UI alone; the application runtime owns tray/hotkeys in normal use."""
    import sys
    from interfaces import theme
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme = getattr(theme, "apply_qt_theme", None)
    if apply_theme is not None:
        apply_theme(app)
    window = MainWindow()
    window.show()
    app._renaclip_window = window
    return app.exec()


__all__ = ["MainWindow", "SettingsDialog", "ProviderDialog", "SkillDialog", "launch_ui"]
