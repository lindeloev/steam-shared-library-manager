#!/bin/sh
# Repair only dedicated game storage using pinned filesystem descriptors.
set -eu
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo 'Usage: repair-shared-library.sh --library PATH --group GROUP'
    echo 'Refuses unrelated/private storage, symlinks, hardlinked and special files.'
    exit 0
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -I -B "$script_dir/library-backend.py" "$@"
