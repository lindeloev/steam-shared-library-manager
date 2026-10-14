#!/bin/sh
# Remove account-owned files as that account, and remove dangling mappings.
set -eu
if [ -z "${SSLM_LOCK_FD:-}" ]; then
    lock_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    exec /usr/bin/python3 -I -B "$lock_dir/operation-lock.py" "$0" "$@"
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo 'Usage: uninstall.sh [--remove-prefixes] USER [USER ...]'
    echo 'Personal saves are retained unless --remove-prefixes is explicitly given.'
    exit 0
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -I -B "$script_dir/account-worker.py" --action uninstall "$@"
