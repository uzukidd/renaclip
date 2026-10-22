#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python scripts/generate_icon.py
python -m PyInstaller --noconfirm --distpath out/material --workpath build/pyinstaller-material RenaClip.spec
