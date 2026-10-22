#!/usr/bin/env sh
set -eu
python "$(dirname "$0")/generate_icon.py" "$@"
