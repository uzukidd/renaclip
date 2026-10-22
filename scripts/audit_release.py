"""Fail a Windows release if private config or obsolete UI runtimes are bundled."""
from pathlib import Path
import sys


def audit(folder):
    folder = Path(folder).resolve()
    if not (folder / 'RenaClip.exe').is_file():
        raise RuntimeError('RenaClip.exe is missing')
    forbidden = []
    for path in folder.rglob('*'):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ('gem_config' in name or 'renaclip.log' == name or name in ('tcl86t.dll', 'tk86t.dll')
                or 'webengine' in name or any(part.lower() in ('flet', '.chrome_user_data', '.edge_user_data', '.temp')
                                             for part in path.relative_to(folder).parts)):
            forbidden.append(str(path.relative_to(folder)))
    if forbidden:
        raise RuntimeError('Forbidden release files: ' + ', '.join(forbidden))
    if not (folder / '_internal' / 'PySide6' / 'plugins' / 'platforms' / 'qwindows.dll').is_file():
        raise RuntimeError('Windows Qt platform plugin is missing')
    qml = folder / '_internal' / 'interfaces' / 'qml'
    for resource in ('Main.qml', 'icons/edit.svg', 'icons/delete_outline.svg', 'icons/LICENSE'):
        if not (qml / resource).is_file():
            raise RuntimeError('Material resource is missing: ' + resource)
    math_assets = folder / '_internal' / 'interfaces' / 'math_assets'
    for resource in ('renderer.bundle.js', 'LICENSE.mathjax'):
        if not (math_assets / resource).is_file():
            raise RuntimeError('Offline formula resource is missing: ' + resource)
    print('Release audit passed: Qt, Material and offline formula resources present; private config and old UI runtimes absent.')


if __name__ == '__main__':
    audit(sys.argv[1] if len(sys.argv) > 1 else 'out/material/RenaClip')
