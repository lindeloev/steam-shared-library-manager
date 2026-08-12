#!/bin/sh
# Repair group access in an existing, recognisable shared Steam library.
set -eu

usage() {
    cat <<'EOF'
Usage: repair-shared-library.sh --library PATH --group GROUP

Repairs group ownership, group read/write access, setgid directories, and
default ACLs within an existing PATH/steamapps library. It refuses arbitrary
non-Steam folders and never follows symlinks. Pending official Proton
SteamPipe file modes are restored after the shared permissions are repaired.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this repair script with sudo." >&2; exit 1; fi
if [ "${1:-}" != "--library" ] || [ "${3:-}" != "--group" ] || [ "$#" -ne 4 ]; then usage >&2; exit 2; fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/_common.sh"
library=$(require_safe_library_path "$2") || exit 1
require_not_personal_steam_root "$library" || exit 1
group_name=$4
if [ ! -d "$library/steamapps" ]; then
    echo "Refusing to repair $library: it does not contain a steamapps directory." >&2
    exit 1
fi
if ! command -v setfacl >/dev/null 2>&1; then echo "setfacl is required. Install Ubuntu's 'acl' package first." >&2; exit 1; fi
if pgrep -x steam >/dev/null 2>&1; then echo "Close Steam for all users before repairing a shared library." >&2; exit 1; fi
if ! getent group "$group_name" >/dev/null; then groupadd -- "$group_name"; fi

# Limit changes to this recognised Steam library. -P avoids following a game
# symlink outside it; default ACLs ensure future Steam downloads stay shared.
find -P "$library" -exec chgrp --no-dereference -- "$group_name" {} +
find -P "$library" -type f -exec chmod g+rw {} +
find -P "$library" -type d -exec chmod g+rwx,g+s {} +
find -P "$library" -type d -exec setfacl -m "g:$group_name:rwx,m::rwx,d:g:$group_name:rwx,d:m::rwx" {} +

# The broad group-write repair above can undo the deliberately read-only modes
# recorded by official Proton. Reapply those modes and refresh Proton's exact
# mtime marker while Steam is already known to be closed.
for proton_dir in "$library"/steamapps/common/Proton*; do
    [ -d "$proton_dir" ] || continue
    [ ! -L "$proton_dir" ] || continue
    [ -f "$proton_dir/steampipe_fixups.json" ] || continue
    "$script_dir/repair-proton-fixups.py" --library "$library" --proton "$proton_dir"
done

echo "Repaired shared access for Steam library: $library"
