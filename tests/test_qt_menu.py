"""Offscreen native Qt menu behavior, using an isolated config file."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer, QModelIndex
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

import config_loader
from interfaces.menu import MainWindow, SettingsDialog, ProviderDialog, SkillDialog


class QtMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"
        config_patch = patch.object(config_loader, "CONFIG_PATH", self.path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        config_loader.save_config(
            [{"name": "First", "description": "one", "prompt": "p1", "web_search": False},
             {"name": "Second", "description": "two", "prompt": "p2", "web_search": True},
             {"name": "Third", "description": "three", "prompt": "p3"}],
            {"BACKEND": "openai", "OPENAI_ACTIVE_PROVIDER": "A", "OPENAI_PROVIDERS": [
                {"name": "A", "model": "shared", "models": ["shared", "model-a"]},
                {"name": "B", "model": "shared", "models": ["shared", "model-b"]},
            ]},
        )
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self._close_widgets)

    def _close_widgets(self):
        for widget in list(self.app.topLevelWidgets()):
            if isinstance(widget, MainWindow):
                widget.hide_on_close = False
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def click_save(self, dialog):
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)

    def click_cancel(self, dialog):
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)

    def test_grouped_routes_reasoning_and_refresh(self):
        combo = self.window.model_combo
        self.assertEqual([combo.itemText(i).strip() for i in range(combo.count())],
                         ["A", "shared", "model-a", "B", "shared", "model-b"])
        for heading in (0, 3):
            self.assertFalse(combo.model().item(heading).flags() & Qt.ItemFlag.ItemIsEnabled)
            self.assertIsNone(combo.itemData(heading))
        spy = QSignalSpy(self.window.configuration_changed)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_End)
        self.assertEqual(combo.currentData(), ("B", "model-b"))
        settings = config_loader.load_config()[1]
        self.assertEqual(settings["OPENAI_ACTIVE_PROVIDER"], "B")
        self.assertEqual(settings["OPENAI_MODEL"], "model-b")
        self.window.reasoning_combo.setFocus()
        QTest.keyClick(self.window.reasoning_combo, Qt.Key.Key_End)
        self.assertEqual(config_loader.load_config()[1]["OPENAI_REASONING_EFFORT"], "high")
        self.assertEqual(spy.count(), 2)
        self.window.refresh()
        self.assertEqual(self.window.model_combo.currentData(), ("B", "model-b"))
        self.assertEqual(self.window.reasoning_combo.currentText(), "high")
        self.assertEqual(spy.count(), 2)

    def test_heading_does_not_persist(self):
        before = self.path.read_bytes()
        self.window.model_combo.setCurrentIndex(0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_provider_save_only_changes_settings_draft_and_cancel_discards(self):
        before = self.path.read_bytes()
        dialog = SettingsDialog(self.window.gems, self.window.settings, self.window)
        dialog.show()
        def edit():
            child = dialog.findChild(ProviderDialog)
            child.name_field.selectAll()
            QTest.keyClicks(child.name_field, "Renamed B")
            child.interface_combo.setCurrentIndex(1)
            child.streaming_checkbox.setChecked(True)
            self.click_save(child)
        QTimer.singleShot(0, edit)
        dialog.edit_provider(1)
        self.assertEqual(dialog.providers[1]["name"], "Renamed B")
        self.assertEqual(dialog.providers[1]["api_interface"], "responses")
        self.assertTrue(dialog.providers[1]["streaming"])
        self.assertEqual(self.window.settings["OPENAI_PROVIDERS"][1]["name"], "B")
        self.assertEqual(self.path.read_bytes(), before)
        self.click_cancel(dialog)
        self.assertEqual(self.path.read_bytes(), before)

    def test_settings_save_persists_all_fields_and_provider_edits(self):
        dialog = SettingsDialog(self.window.gems, self.window.settings, self.window)
        dialog.show()
        dialog._store_provider({**dialog.providers[1], "name": "Renamed B", "api_key": "secret", "base_url": "https://example.org/v1", "streaming": True}, 1)
        dialog.backend_combo.setCurrentText("gemini")
        dialog.hotkey_combo.setCurrentText("ctrl+shift")
        dialog.screenshot_checkbox.setChecked(True)
        dialog.screenshot_web_search_checkbox.setChecked(True)
        dialog.psid_field.setText("psid")
        dialog.psidts_field.setText("psidts")
        dialog.proxy_field.setText("socks5://localhost:8889")
        dialog.browser_cookie_checkbox.setChecked(True)
        self.assertFalse(dialog.psid_field.isEnabled())
        dialog.cookie_browser_combo.setCurrentText("chrome")
        dialog.gemini_model_combo.setCurrentText("gemini-3.0-pro")
        self.click_save(dialog)
        settings = config_loader.load_config()[1]
        for key, value in {"BACKEND": "gemini", "HOTKEY_MODIFIER": "ctrl+shift", "SCREENSHOT_QA_ENABLED": True,
                           "SCREENSHOT_QA_WEB_SEARCH": True, "GEMINI_PSID": "psid", "GEMINI_PSIDTS": "psidts",
                           "GEMINI_PROXY": "socks5://localhost:8889", "GEMINI_USE_BROWSER_COOKIE": True,
                           "GEMINI_COOKIE_BROWSER": "chrome", "GEMINI_MODEL": "gemini-3.0-pro"}.items():
            self.assertEqual(settings[key], value)
        self.assertEqual(settings["OPENAI_PROVIDERS"][1]["name"], "Renamed B")
        self.window.refresh()
        self.assertIn(("Renamed B", "shared"), [self.window.model_combo.itemData(i) for i in range(self.window.model_combo.count())])

    def test_provider_add_cancel_delete_minimum_and_unique_names(self):
        dialog = SettingsDialog(self.window.gems, self.window.settings, self.window)
        before = self.path.read_bytes()
        QTimer.singleShot(0, lambda: self.click_cancel(dialog.findChild(ProviderDialog)))
        dialog.add_provider()
        self.assertEqual(len(dialog.providers), 2)
        dialog._store_provider({**dialog.providers[0], "name": "A"})
        self.assertEqual(dialog.providers[-1]["name"], "A (2)")
        dialog.delete_provider(2)
        dialog.delete_provider(0)
        self.assertEqual(dialog.draft["OPENAI_ACTIVE_PROVIDER"], "B")
        dialog.delete_provider(0)
        self.assertEqual(len(dialog.providers), 1)
        self.assertFalse(dialog.delete_provider_button.isEnabled())
        self.assertEqual(self.path.read_bytes(), before)

    def test_skill_save_cancel_delete_and_reorder(self):
        before = self.path.read_bytes()
        def save_skill():
            dialog = self.window.findChild(SkillDialog)
            dialog.title_field.selectAll()
            QTest.keyClicks(dialog.title_field, "Edited")
            dialog.prompt_field.setPlainText("New prompt")
            dialog.web_search_checkbox.setChecked(True)
            self.click_save(dialog)
        QTimer.singleShot(0, save_skill)
        self.window.edit_skill(0)
        self.assertNotEqual(self.path.read_bytes(), before)
        self.assertEqual(config_loader.load_config()[0][0]["name"], "Edited")
        self.assertTrue(config_loader.load_config()[0][0]["web_search"])
        before_cancel = self.path.read_bytes()
        QTimer.singleShot(0, lambda: self.click_cancel(self.window.findChildren(SkillDialog)[-1]))
        self.window.edit_skill(None)
        self.assertEqual(self.path.read_bytes(), before_cancel)
        # Use Qt's own item model move, the same signal emitted by InternalMove.
        model = self.window.skill_list.model()
        self.assertTrue(model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 3))
        self.app.processEvents()
        self.assertEqual([g["name"] for g in config_loader.load_config()[0]], ["Second", "Third", "Edited"])
        self.window.delete_skill(1)
        self.assertEqual([g["name"] for g in config_loader.load_config()[0]], ["Second", "Edited"])

    def test_model_refresh_executor_and_error(self):
        submitted = []
        class Executor:
            def submit(self, coro, on_result=None, on_error=None):
                submitted.append((coro, on_result, on_error))
        dialog = ProviderDialog(self.window.settings["OPENAI_PROVIDERS"][0], executor=Executor())
        dialog.show()
        async def fake_fetch(**kwargs):
            return ["new-model"]
        with patch("interfaces.menu.fetch_available_models", fake_fetch):
            QTest.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
            self.assertFalse(dialog.refresh_button.isEnabled())
            coro, result, error = submitted.pop()
            coro.close()
            result(["new-model", "other"])
            self.assertEqual(dialog.value()["models"], ["new-model", "other"])
            self.assertEqual(dialog.value()["model"], "new-model")
            self.assertTrue(dialog.refresh_button.isEnabled())
            QTest.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
            coro, result, error = submitted.pop()
            coro.close()
            error(RuntimeError("offline"))
            self.assertIn("offline", dialog.status_label.text())
            self.assertTrue(dialog.refresh_button.isEnabled())

    def test_close_hides_when_runtime_enables_tray(self):
        self.window.hide_on_close = True
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.window.hide_on_close = False
        self.window.close()
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
