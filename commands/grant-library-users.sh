#!/bin/sh
# Grant selected Linux accounts access to an existing shared-library group.
set -eu
if [ -z "${SSLM_LOCK_FD:-}" ]; then
    lock_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    exec /usr/bin/python3 -I -B "$lock_dir/operation-lock.py" "$0" "$@"
fi

usage() { cat <<'EOF'
Usage: grant-library-users.sh --group GROUP [--library PATH] USER [USER ...]

Adds users to an existing shared-library Unix group. Users must log out and
back in before their desktop sessions receive the new membership.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this script with sudo or pkexec." >&2; exit 1; fi
if [ "${1:-}" != "--group" ] || [ "$#" -lt 3 ]; then usage >&2; exit 2; fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
group_name=$2
shift 2
if ! getent group "$group_name" >/dev/null; then echo "Shared-library group does not exist: $group_name" >&2; exit 1; fi
library=
if [ "${1:-}" = "--library" ]; then
    [ "$#" -ge 3 ] || { usage >&2; exit 2; }
    library=$2
    shift 2
    /usr/bin/python3 -I -B "$script_dir/library-backend.py" --library "$library" --validate-only >/dev/null
fi
for user_name in "$@"; do
    if ! getent passwd "$user_name" >/dev/null; then echo "Unknown user: $user_name" >&2; exit 1; fi
done
for user_name in "$@"; do usermod -a -G "$group_name" "$user_name"; done
echo "Users added to $group_name: $*"
if [ -n "$library" ]; then
    "$script_dir/repair-shared-library.sh" --library "$library" --group "$group_name"
    echo "Direct user ACL access is ready in existing sessions."
else
    echo "Group membership added. Run library repair for immediate ACL access."
fi
