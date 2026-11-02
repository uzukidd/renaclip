"""Real QML interaction regression tests using a temporary local config."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QPoint, QPointF, QMetaObject, Qt, Q_ARG
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

import config_loader
from interfaces.material_window import MainWindow


class MaterialUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'config.json'
        context = patch.object(config_loader, 'CONFIG_PATH', self.path)
        context.start()
        self.addCleanup(context.stop)
        config_loader.save_config([
            {'name':'First', 'description':'one', 'prompt':'p1', 'web_search':False},
            {'name':'Second','description':'two','prompt':'p2','web_search':True},
            {'name':'Third','description':'three','prompt':'p3','web_search':False},
        ], {'HOTKEY_MODIFIER':'ctrl+shift', 'BACKEND':'openai'})
        self.errors=[]
        self.window = MainWindow(executor=Mock())
        self.window.engine.warnings.connect(lambda warnings: self.errors.extend(w.toString() for w in warnings))
        self.root = self.window.window
        self.window.show()
        QTest.qWait(150)

    def tearDown(self):
        self.window.dispose()
        self.app.processEvents()
        self.assertEqual(self.errors, [])

    def find_visual(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name:
            return parent
        for child in parent.childItems():
            value = self.find_visual(name, child)
            if value is not None:
                return value
        return None

    def test_real_drag_persists_and_updates_shortcut_order(self):
        handle = self.find_visual('dragHandle0')
        self.assertIsNotNone(handle)
        point = handle.mapToScene(QPointF(handle.width()/2,handle.height()/2)).toPoint()
        target = point + QPoint(0,92)
        QTest.mousePress(self.root,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(self.root,target,50)
        QTest.mouseRelease(self.root,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,target)
        QTest.qWait(100)
        self.assertEqual([x['name'] for x in config_loader.load_config()[0]], ['Second','First','Third'])
        self.assertEqual([x['name'] for x in self.window.gems], ['Second','First','Third'])

    def test_skill_save_button_persists_and_cancel_keeps_file(self):
        self.assertTrue(QMetaObject.invokeMethod(self.root,'openSkill',Qt.ConnectionType.DirectConnection,Q_ARG('QVariant',0)))
        QTest.qWait(200)
        field = self.root.findChild(QObject,'skillName')
        self.assertIsNotNone(field)
        field.setProperty('text','Edited through Material')
        checkbox = self.root.findChild(QObject,'skillPopupMode')
        self.assertIsNotNone(checkbox)
        checkbox.setProperty('checked',True)
        button = self.find_visual('skillDialogSave')
        self.assertIsNotNone(button)
        point = button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
        QTest.mouseClick(self.root,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.qWait(250)
        self.assertEqual(config_loader.load_config()[0][0]['name'],'Edited through Material')
        self.assertTrue(config_loader.load_config()[0][0]['popup_mode'])

    def test_settings_chat_toggle_save_cancel_and_reopen(self):
        before = self.path.read_bytes()
        self.assertTrue(QMetaObject.invokeMethod(self.root, 'openSettings', Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        toggle = self.root.findChild(QObject, 'continuousChatEnabled')
        dialog = self.root.findChild(QObject, 'settingsDialog')
        self.assertTrue(toggle.property('checked'))
        self.assertIn('+ 9', toggle.property('text'))
        toggle.setProperty('checked', False)
        self.assertTrue(QMetaObject.invokeMethod(dialog, 'reject', Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(QMetaObject.invokeMethod(self.root, 'openSettings', Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        self.assertTrue(toggle.property('checked'))
        toggle.setProperty('checked', False)
        button = self.find_visual('settingsDialogSave')
        point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
        QTest.mouseClick(self.root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(150)
        self.assertFalse(config_loader.load_config()[1]['CONTINUOUS_CHAT_ENABLED'])
        self.assertTrue(QMetaObject.invokeMethod(self.root, 'openSettings', Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        self.assertFalse(toggle.property('checked'))
        self.assertTrue(QMetaObject.invokeMethod(dialog, 'reject', Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)

    def test_add_skill_limit_and_legacy_ninth_has_no_shortcut_badge(self):
        skills = [{'name': f'Skill {i}'} for i in range(9)]
        config_loader.save_config(skills, {'HOTKEY_MODIFIER': 'ctrl'})
        self.window.refresh()
        self.root.resize(1000, 1100)
        QTest.qWait(150)
        self.assertFalse(self.find_visual('addSkillButton').property('enabled'))
        badge = self.find_visual('skillShortcut8')
        self.assertIsNotNone(badge)
        self.assertFalse(badge.property('visible'))
        self.assertTrue(self.find_visual('skillShortcut7').property('visible'))
        self.assertTrue(QMetaObject.invokeMethod(self.root, 'openSkill', Qt.ConnectionType.DirectConnection, Q_ARG('QVariant', -1)))
        self.assertFalse(self.root.findChild(QObject, 'skillDialog').property('visible'))
        self.assertEqual(config_loader.load_config()[0], skills)
        self.window.bridge.deleteSkill(8)
        self.assertFalse(self.find_visual('addSkillButton').property('enabled'))
        self.window.bridge.deleteSkill(7)
        self.assertTrue(self.find_visual('addSkillButton').property('enabled'))

    def test_window_close_hides_when_tray_enabled(self):
        self.window.hide_on_close = True
        self.root.close()
        QTest.qWait(50)
        self.assertFalse(self.root.isVisible())
        self.window.showNormal()
        self.assertTrue(self.root.isVisible())

    def test_settings_cancel_discards_provider_draft(self):
        before = self.path.read_bytes()
        self.assertTrue(QMetaObject.invokeMethod(self.root,'openSettings',Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        dialog = self.root.findChild(QObject,'settingsDialog')
        self.assertIsNotNone(dialog)
        self.window.bridge.saveProvider(-1, {'name':'Draft','model':'x','models':['x']})
        self.assertTrue(QMetaObject.invokeMethod(dialog,'reject',Qt.ConnectionType.DirectConnection))
        QTest.qWait(100)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.window.bridge.providersDraft,[])


if __name__ == '__main__':
    unittest.main()
