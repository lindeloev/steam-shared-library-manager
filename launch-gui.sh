#!/bin/sh
# Start the local graphical guide without requiring Python packages from PyPI.

set -eu
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec env PYTHONDONTWRITEBYTECODE=1 python3 "$script_dir/steam_shared_library_gui.py" "$@"
