# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / 'assets' / name), 'assets')
         for name in ('renaclip_icon.ico', 'renaclip_icon.png', 'renaclip_logo.png')]
datas += [(str(root / 'interfaces' / 'qml'), 'interfaces/qml')]
datas += [(str(root / 'interfaces' / 'math_assets'), 'interfaces/math_assets')]
for package in ('certifi', 'gemini_webapi'):
    datas += collect_data_files(package)
for package in ('openai', 'gemini-webapi', 'browser-cookie3'):
    datas += copy_metadata(package)
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=datas, hiddenimports=['keyboard._winkeyboard'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['flet', 'flet_desktop', 'tkinter', '_tkinter', 'PyQt5', 'PyQt6',
              'PySide2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'numpy', 'pandas', 'matplotlib',
              'scipy', 'torch', 'IPython', 'pytest'],
    noarchive=False,
)
# QtQml hooks collect every installed QML plugin; this app never imports WebEngine.
a.binaries = [entry for entry in a.binaries if 'webengine' not in entry[0].lower()]
a.datas = [entry for entry in a.datas if 'webengine' not in entry[0].lower()]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RenaClip',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / 'assets' / 'renaclip_icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RenaClip')
