#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python -m PyInstaller --noconfirm --distpath out/material --workpath build/pyinstaller-material RenaClip.spec
