#!/bin/sh
# Preserve explicit Proton versions using corresponding personal wrappers.
set -eu
case "${1:-}" in
    --help|-h) echo 'Usage: migrate-existing-games.sh --dry-run|--apply USER [USER ...]'; exit 0 ;;
    --dry-run) mode=--dry-run ;;
    --apply) mode= ;;
    *) echo 'Usage: migrate-existing-games.sh --dry-run|--apply USER [USER ...]' >&2; exit 2 ;;
esac
shift
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -n "$mode" ]; then
    exec /usr/bin/python3 -I -B "$script_dir/account-worker.py" --action migrate --dry-run "$@"
fi
exec /usr/bin/python3 -I -B "$script_dir/account-worker.py" --action migrate "$@"
