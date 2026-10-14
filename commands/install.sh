#!/bin/sh
# Install account-owned Steam files under each account's own identity.
set -eu
if [ -z "${SSLM_LOCK_FD:-}" ]; then
    lock_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    exec /usr/bin/python3 -I -B "$lock_dir/operation-lock.py" "$0" "$@"
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo 'Usage: install.sh --base-proton PATH USER [USER ...]'
    echo 'Each USER needs local Proton. Steam must be closed. Changes have backups.'
    exit 0
fi
if [ "${1:-}" != "--base-proton" ] || [ "$#" -lt 3 ]; then
    echo 'Usage: install.sh --base-proton PATH USER [USER ...]' >&2
    exit 2
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -I -B "$script_dir/account-worker.py" --action install "$@"
