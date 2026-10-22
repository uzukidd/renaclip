@echo off
setlocal
pushd "%~dp0.."
python scripts\generate_icon.py
if errorlevel 1 (
    popd
    exit /b 1
)
python -m PyInstaller --noconfirm --distpath out/material --workpath build/pyinstaller-material RenaClip.spec
set "BUILD_RESULT=%ERRORLEVEL%"
popd
exit /b %BUILD_RESULT%
