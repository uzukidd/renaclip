"""Production Material bridge regression tests with isolated config and no network."""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

import config_loader
from interfaces import material_bridge as module


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, coro, **callbacks):
        # No coroutine execution means no API request, even for model refresh.
        coro.close()
        self.calls.append(callbacks)
        return Mock()


class MaterialBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "gem_config.json"
        cfg = patch.object(config_loader, "CONFIG_PATH", self.path)
        cfg.start()
        self.addCleanup(cfg.stop)
        self.initial_skills = [
            {"name": "One", "description": "first", "prompt": "p1", "web_search": False,
             "custom": {"retained": [1, 2]}},
            {"name": "Two", "description": "second", "prompt": "p2", "web_search": True},
        ]
        self.initial_settings = {
            "BACKEND": "openai", "HOTKEY_MODIFIER": "ctrl+shift",
            "OPENAI_REASONING_EFFORT": "default", "OPENAI_ACTIVE_PROVIDER": "A",
            "OPENAI_PROVIDERS": [
                {"name": "A", "api_key": "test-a", "base_url": "https://a.invalid/v1",
                 "api_interface": "responses", "model": "shared", "models": ["shared", "a-only"], "streaming": True},
                {"name": "B", "api_key": "test-b", "base_url": "https://b.invalid/v1",
                 "api_interface": "chat_completions", "model": "shared", "models": ["shared", "b-only"], "streaming": False},
            ],
        }
        config_loader.save_config(deepcopy(self.initial_skills), deepcopy(self.initial_settings))
        self.executor = FakeExecutor()
        self.bridge = module.MaterialBridge(self.executor)
        self.changed = Mock()
        self.bridge.configuration_changed.connect(self.changed)

    def disk(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def route(self, provider, model):
        return next(i for i, item in enumerate(self.bridge.modelRoutes)
                    if item["provider"] == provider and item["model"] == model and not item["heading"])

    def test_grouped_duplicate_routes_heading_and_invalid_selection_do_not_write(self):
        routes = self.bridge.modelRoutes
        self.assertEqual([item["title"] for item in routes], ["A", "shared", "a-only", "B", "shared", "b-only"])
        original = self.path.read_bytes()
        for index in (0, 3, -1, 999):
            self.assertFalse(self.bridge.setRoute(index))
        self.assertEqual(self.path.read_bytes(), original)
        self.changed.assert_not_called()
        self.assertTrue(self.bridge.setRoute(self.route("B", "shared")))
        self.assertEqual(self.disk()["settings"]["OPENAI_ACTIVE_PROVIDER"], "B")
        self.assertTrue(self.bridge.setRoute(self.route("B", "b-only")))
        self.assertEqual(self.disk()["settings"]["OPENAI_MODEL"], "b-only")
        self.assertEqual(self.bridge.modelRoutes[self.bridge.activeRoute]["provider"], "B")

    def test_reasoning_persists_and_normalizes_without_changing_provider(self):
        for value, expected in (("HIGH", "high"), ("medium", "medium"), ("invalid", "default")):
            self.assertTrue(self.bridge.setReasoning(value))
            self.assertEqual(self.bridge.reasoning, expected)
            self.assertEqual(self.disk()["settings"]["OPENAI_REASONING_EFFORT"], expected)
            self.assertEqual(self.disk()["settings"]["OPENAI_ACTIVE_PROVIDER"], "A")
        reopened = module.MaterialBridge(self.executor)
        self.assertEqual(reopened.reasoning, "default")

    def test_skill_add_edit_unknown_fields_reorder_delete_and_validation(self):
        self.assertTrue(self.bridge.saveSkill(0, {"name": " Edited ", "prompt": "new", "web_search": True}))
        entry = self.disk()["gems"][0]
        self.assertEqual(entry["name"], "Edited")
        self.assertEqual(entry["custom"], {"retained": [1, 2]})
        self.assertTrue(self.bridge.saveSkill(-1, {"name": "Three", "description": "d3", "prompt": "p3"}))
        self.assertTrue(self.bridge.moveSkill(2, 0))
        self.assertEqual([item["name"] for item in self.disk()["gems"]], ["Three", "Edited", "Two"])
        self.assertTrue(self.bridge.deleteSkill(1))
        self.assertEqual([item["name"] for item in self.bridge.skills], ["Three", "Two"])
        original = self.path.read_bytes()
        for index, value in ((0, {"name": "  "}), (99, {"name": "No"}), (-2, {"name": "No"})):
            self.assertFalse(self.bridge.saveSkill(index, value))
        self.assertFalse(self.bridge.deleteSkill(99))
        self.assertFalse(self.bridge.moveSkill(99, 0))
        self.assertEqual(self.path.read_bytes(), original)

    def test_delete_last_skill_keeps_intentionally_empty_list_on_reload(self):
        self.assertTrue(self.bridge.deleteSkill(1))
        self.assertTrue(self.bridge.deleteSkill(0))
        self.assertEqual(self.disk()["gems"], [])
        self.assertEqual(self.bridge.skills, [])
        self.assertEqual(module.MaterialBridge(self.executor).skills, [])

    def test_settings_cancel_isolates_deep_provider_draft_and_does_not_write(self):
        original = self.path.read_bytes()
        settings = deepcopy(self.bridge.settings)
        draft = self.bridge.beginSettings()
        draft["OPENAI_PROVIDERS"][0]["models"].append("external-mutation")
        self.assertNotIn("external-mutation", self.bridge.providersDraft[0]["models"])
        edited = self.bridge.providersDraft[0]
        edited.update(name="Renamed", models=["edited-model"], model="edited-model")
        self.assertTrue(self.bridge.saveProvider(0, edited))
        self.assertTrue(self.bridge.saveProvider(-1, {"name": "C", "models": ["c"]}))
        self.assertTrue(self.bridge.deleteProvider(1))
        self.bridge.updateSettingsDraft({"BACKEND": "gemini", "GEMINI_PSID": "draft-cookie"})
        self.assertEqual(self.bridge.settings, settings)
        self.bridge.cancelSettings()
        self.assertEqual(self.bridge.settingsDraft, {})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.bridge.settings, settings)
        self.changed.assert_not_called()

    def test_provider_active_rename_duplicate_suffix_minimum_and_settings_save(self):
        self.bridge.beginSettings()
        edited = self.bridge.providersDraft[0]
        edited["name"] = "B"
        self.assertTrue(self.bridge.saveProvider(0, edited))
        self.assertEqual(self.bridge.providersDraft[0]["name"], "B (2)")
        self.assertEqual(self.bridge.settingsDraft["OPENAI_ACTIVE_PROVIDER"], "B (2)")
        self.assertTrue(self.bridge.saveProvider(-1, {"name": "B", "models": [" x ", "x", ""], "model": "y", "api_interface": "bad"}))
        third = self.bridge.providersDraft[2]
        self.assertEqual(third["name"], "B (3)")
        self.assertEqual(third["models"], ["y", "x"])
        self.assertEqual(third["api_interface"], "chat_completions")
        before = deepcopy(self.bridge.providersDraft)
        self.assertFalse(self.bridge.saveProvider(-1, {"name": " "}))
        self.assertFalse(self.bridge.saveProvider(99, {"name": "Missing"}))
        self.assertEqual(self.bridge.providersDraft, before)
        self.assertTrue(self.bridge.deleteProvider(0))
        self.assertEqual(self.bridge.settingsDraft["OPENAI_ACTIVE_PROVIDER"], "B")
        self.assertTrue(self.bridge.deleteProvider(1))
        self.assertFalse(self.bridge.deleteProvider(0))
        self.assertIn("at least one", self.bridge.errorMessage)
        self.assertTrue(self.bridge.saveSettings())
        self.assertEqual(self.disk()["settings"]["OPENAI_ACTIVE_PROVIDER"], "B")
        self.assertEqual(len(self.disk()["settings"]["OPENAI_PROVIDERS"]), 1)

    def test_settings_save_all_fields_validation_and_unknown_key_guard(self):
        self.bridge.beginSettings()
        fields = {"BACKEND": "gemini", "HOTKEY_MODIFIER": "ctrl+alt",
                  "SCREENSHOT_QA_ENABLED": True, "SCREENSHOT_QA_WEB_SEARCH": True,
                  "GEMINI_PSID": "test-cookie", "GEMINI_PSIDTS": "test-timestamp",
                  "GEMINI_PROXY": "socks5://localhost:9999", "GEMINI_MODEL": "gemini-3.0-pro",
                  "GEMINI_COOKIE_BROWSER": "chrome", "GEMINI_USE_BROWSER_COOKIE": True}
        self.bridge.updateSettingsDraft({**fields, "UNSUPPORTED": "ignored"})
        self.assertNotIn("UNSUPPORTED", self.bridge.settingsDraft)
        self.assertTrue(self.bridge.saveSettings())
        for key, value in fields.items():
            self.assertEqual(self.disk()["settings"][key], value)
        self.assertEqual(self.bridge.hotkey, "Ctrl+Alt")
        self.assertEqual(self.bridge.settingsDraft, {})
        self.bridge.beginSettings()
        original = self.path.read_bytes()
        self.bridge.updateSettingsDraft({"HOTKEY_MODIFIER": "bad"})
        self.assertFalse(self.bridge.saveSettings())
        self.bridge.updateSettingsDraft({"HOTKEY_MODIFIER": "ctrl", "BACKEND": "bad"})
        self.assertFalse(self.bridge.saveSettings())
        self.assertEqual(self.path.read_bytes(), original)
        self.bridge.updateSettingsDraft({"BACKEND": "openai", "GEMINI_MODEL": "bad"})
        self.assertTrue(self.bridge.saveSettings())
        self.assertEqual(self.disk()["settings"]["GEMINI_MODEL"], "unspecified")

    def test_async_refresh_success_failure_and_stale_callbacks_are_ignored(self):
        self.bridge.beginSettings()
        before = self.path.read_bytes()
        self.bridge.refreshModels("fake-key", "https://mock.invalid")
        first = self.executor.calls[-1]
        self.assertTrue(self.bridge.refreshing)
        self.bridge.refreshModels("other-key", "https://other.invalid")
        latest = self.executor.calls[-1]
        first["on_result"](["stale"])
        first["on_error"](OSError("stale private response"))
        self.assertTrue(self.bridge.refreshing)
        self.assertEqual(self.bridge.refreshedModels, [])
        latest["on_result"](["a", "b"])
        self.assertEqual(self.bridge.refreshedModels, ["a", "b"])
        self.assertFalse(self.bridge.refreshing)
        self.bridge.refreshModels("key", "")
        self.executor.calls[-1]["on_result"]([])
        self.assertIn("No models", self.bridge.errorMessage)
        self.bridge.refreshModels("key", "")
        pending = self.executor.calls[-1]
        pending["on_error"](OSError("secret details"))
        self.assertNotIn("secret details", self.bridge.errorMessage)
        self.assertFalse(self.bridge.refreshing)
        self.bridge.refreshModels("key", "")
        late = self.executor.calls[-1]
        self.bridge.cancelSettings()
        late["on_result"](["late"])
        late["on_error"](RuntimeError("late"))
        self.assertEqual(self.bridge.refreshedModels, [])
        self.assertEqual(self.bridge.errorMessage, "")
        self.assertFalse(self.bridge.refreshing)
        self.assertEqual(self.path.read_bytes(), before)

    def test_save_failures_leave_live_state_disk_and_draft_retryable(self):
        initial_disk = self.path.read_bytes()
        initial_state = (self.bridge.skills, self.bridge.settings, self.bridge.activeRoute)
        self.bridge.beginSettings()
        self.bridge.updateSettingsDraft({"GEMINI_PSID": "unsaved", "SCREENSHOT_QA_ENABLED": True})
        edited = self.bridge.providersDraft[0]
        edited["name"] = "Unsaved renamed"
        self.bridge.saveProvider(0, edited)
        draft = deepcopy(self.bridge.settingsDraft)
        with patch.object(module, "save_config", side_effect=OSError("read-only test")):
            self.assertFalse(self.bridge.setRoute(self.route("B", "b-only")))
            self.assertFalse(self.bridge.setReasoning("high"))
            self.assertFalse(self.bridge.saveSkill(0, {"name": "Unsaved skill"}))
            self.assertFalse(self.bridge.deleteSkill(0))
            self.assertFalse(self.bridge.moveSkill(0, 1))
            self.assertFalse(self.bridge.saveSettings())
        self.assertEqual(self.path.read_bytes(), initial_disk)
        self.assertEqual((self.bridge.skills, self.bridge.settings, self.bridge.activeRoute), initial_state)
        self.assertEqual(self.bridge.settingsDraft, draft)
        self.assertIn("Could not save", self.bridge.errorMessage)
        self.changed.assert_not_called()
        self.assertTrue(self.bridge.saveSettings())
        self.assertEqual(self.disk()["settings"]["OPENAI_ACTIVE_PROVIDER"], "Unsaved renamed")
        self.assertEqual(self.bridge.errorMessage, "")
        self.changed.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
