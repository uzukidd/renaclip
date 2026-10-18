"""Interactive Material visual review. Uses demo data, no config or API access."""
import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from interfaces import theme


def run(argv=None):
    parser = argparse.ArgumentParser(description="RenaClip Material design preview")
    parser.add_argument('--capture-dir', type=Path, help='Save real rendered review images and exit')
    parser.add_argument('--verify-reorder', action='store_true', help='Exercise drag reorder during capture')
    args = parser.parse_args(argv)
    QQuickStyle.setStyle('Material')
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName('RenaClip Material Preview')
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('paletteTokens', {
        'background': theme.BACKGROUND, 'surface': theme.SURFACE,
        'raised': theme.SURFACE_RAISED, 'accentSurface': theme.SURFACE_ACCENT,
        'accent': theme.ACCENT, 'accentLight': theme.ACCENT_LIGHT,
        'text': theme.TEXT, 'muted': theme.TEXT_MUTED, 'divider': theme.DIVIDER,
    })
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(str(w.toString()) for w in warnings))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / 'MaterialPreview.qml')))
    if not engine.rootObjects():
        raise RuntimeError('Material QML failed to load: ' + '\n'.join(errors))
    window = engine.rootObjects()[0]
    if args.capture_dir:
        args.capture_dir.mkdir(parents=True, exist_ok=True)
        failure = []
        def save(name):
            try:
                image = window.grabWindow()
                if image.isNull() or not image.save(str(args.capture_dir / name)):
                    raise RuntimeError('Could not capture ' + name)
                print(args.capture_dir / name, flush=True)
            except Exception as exc:
                failure.append(exc)
                app.quit()
        def verify_reorder():
            if not args.verify_reorder:
                return
            from PySide6.QtCore import QPoint, QPointF
            from PySide6.QtTest import QTest
            from PySide6.QtQuick import QQuickItem
            original = window.property('skillOrder')
            def drag_first():
                # ListView delegates live in the visual tree, not necessarily QObject children.
                def find(item):
                    if item.objectName() == 'dragHandle0':
                        return item
                    for child in item.childItems():
                        found = find(child)
                        if found is not None:
                            return found
                    return None
                handle = find(window.contentItem())
                if handle is None:
                    raise RuntimeError('Drag handle missing')
                point = handle.mapToScene(QPointF(handle.width()/2, handle.height()/2)).toPoint()
                target = point + QPoint(0, 92)
                QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                QTest.mouseMove(window, target, 80)
                QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
                QTest.qWait(100)
            drag_first()
            if window.property('skillOrder') == original:
                raise RuntimeError('Drag reorder did not update the model')
            drag_first()
            if window.property('skillOrder') != original:
                raise RuntimeError('Reverse drag did not restore order')
            print('Real drag reorder passed in both directions.', flush=True)

        def settings():
            try:
                verify_reorder()
            except Exception as exc:
                failure.append(exc)
                app.quit()
                return
            save('material-home.png')
            QMetaObject.invokeMethod(window, 'openSettingsPreview', Qt.ConnectionType.DirectConnection)
            QTimer.singleShot(450, finish)
        def finish():
            save('material-settings.png')
            settings_obj = window.findChild(QObject, 'settingsDialog')
            if settings_obj:
                QMetaObject.invokeMethod(settings_obj, 'close', Qt.ConnectionType.DirectConnection)
            QMetaObject.invokeMethod(window, 'openSkillPreview', Qt.ConnectionType.DirectConnection)
            QTimer.singleShot(450, final)
        def final():
            save('material-skill.png')
            skill_obj = window.findChild(QObject, 'skillDialog')
            if skill_obj:
                QMetaObject.invokeMethod(skill_obj, 'close', Qt.ConnectionType.DirectConnection)
            QMetaObject.invokeMethod(window, 'openProviderPreview', Qt.ConnectionType.DirectConnection)
            QTimer.singleShot(450, provider)
        def provider():
            save('material-provider.png')
            app.quit()
        QTimer.singleShot(700, settings)
        app.exec()
        if failure:
            raise failure[0]
        if errors:
            raise RuntimeError('\n'.join(errors))
        return 0
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(run())
