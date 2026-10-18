import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication
import config_loader
from interfaces import autostart
from interfaces.material_bridge import MaterialBridge


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_frozen_command_quotes_executable_and_hides(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/Program Files/RenaClip/RenaClip.exe'):
            command = autostart.startup_command()
            self.assertTrue(command.startswith('"'))
            self.assertTrue(command.endswith(' --hidden'))
            self.assertNotIn('main.py', command)

    def test_source_command_uses_absolute_script(self):
        with patch.object(sys, 'frozen', False, create=True):
            command = autostart.startup_command()
            self.assertIn(str(Path(autostart.__file__).resolve().parents[1] / 'main.py'), command)
            self.assertTrue(command.endswith(' --hidden'))

    def test_settings_save_cancel_disable_and_failure_rollback(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config_loader, 'CONFIG_PATH', Path(folder) / 'config.json'), \
                patch.object(autostart, 'read_startup_command', return_value='old command'), \
                patch.object(autostart, 'write_startup_command') as write:
            config_loader.save_config([], {})
            bridge = MaterialBridge(Mock())
            bridge.beginSettings()
            bridge.updateSettingsDraft({'START_WITH_WINDOWS':True})
            bridge.cancelSettings()
            write.assert_not_called()
            bridge.beginSettings()
            bridge.updateSettingsDraft({'START_WITH_WINDOWS':True})
            self.assertTrue(bridge.saveSettings())
            self.assertTrue(config_loader.load_config()[1]['START_WITH_WINDOWS'])
            self.assertTrue(write.call_args.args[0].endswith(' --hidden'))
            bridge.beginSettings()
            bridge.updateSettingsDraft({'START_WITH_WINDOWS':False})
            self.assertTrue(bridge.saveSettings())
            write.assert_called_with(None)
            self.assertFalse(config_loader.load_config()[1]['START_WITH_WINDOWS'])
            bridge.beginSettings()
            bridge.updateSettingsDraft({'START_WITH_WINDOWS':True})
            with patch('interfaces.material_bridge.save_config', side_effect=OSError('disk')):
                self.assertFalse(bridge.saveSettings())
            write.assert_called_with('old command')
            self.assertFalse(config_loader.load_config()[1]['START_WITH_WINDOWS'])
            write.side_effect = PermissionError('denied')
            self.assertFalse(bridge.saveSettings())
            self.assertIn('Windows startup', bridge.errorMessage)

    def test_registry_disable_only_removes_renaclip(self):
        import winreg
        handle = Mock()
        handle.__enter__ = Mock(return_value='key')
        handle.__exit__ = Mock(return_value=False)
        with patch.object(winreg,'CreateKeyEx',return_value=handle), \
                patch.object(winreg,'SetValueEx') as set_value, \
                patch.object(winreg,'DeleteValue') as delete:
            autostart.write_startup_command('test --hidden')
            set_value.assert_called_once_with('key','RenaClip',0,winreg.REG_SZ,'test --hidden')
            autostart.write_startup_command(None)
            delete.assert_called_once_with('key','RenaClip')
            delete.side_effect = FileNotFoundError()
            autostart.write_startup_command(None)


if __name__ == '__main__':
    unittest.main()
