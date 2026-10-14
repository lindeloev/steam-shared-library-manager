#!/bin/sh
# Copy tools into every requested local library and optionally quarantine sources.
set -eu
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo 'Usage: migrate-shared-tools.sh [--dry-run] [--replace-conflicts] [--cleanup] --library PATH USER [USER ...]'
    echo 'Steam must be closed. Conflicts can be backed up before replacement.'
    echo '--cleanup quarantines shared sources only after all account copies verify.'
    exit 0
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -I -B "$script_dir/account-worker.py" --action tools "$@"
