#!/bin/sh
# Grant selected Linux accounts access to an existing shared-library group.
set -eu
usage() { cat <<'EOF'
Usage: grant-library-users.sh --group GROUP USER [USER ...]

Adds users to an existing shared-library Unix group. Users must log out and
back in before their desktop sessions receive the new membership.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this script with sudo or pkexec." >&2; exit 1; fi
if [ "${1:-}" != "--group" ] || [ "$#" -lt 3 ]; then usage >&2; exit 2; fi
group_name=$2
shift 2
if ! getent group "$group_name" >/dev/null; then echo "Shared-library group does not exist: $group_name" >&2; exit 1; fi
for user_name in "$@"; do
    if ! getent passwd "$user_name" >/dev/null; then echo "Unknown user: $user_name" >&2; exit 1; fi
done
for user_name in "$@"; do usermod -a -G "$group_name" "$user_name"; done
echo "Users added to $group_name: $*"
echo "Each listed user must log out and back in before using the shared library."
