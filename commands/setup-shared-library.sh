#!/bin/sh
# Create the filesystem part of a shared Steam library.
set -eu
usage() { cat <<'EOF'
Usage: setup-shared-library.sh --library PATH --group GROUP [USER ...]

Creates PATH/steamapps with group access and default ACLs, then optionally adds
each USER to GROUP. PATH must be missing or an empty directory, absolute, and
outside /usr. Existing Steam libraries are handled by repair-shared-library.sh.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this setup script with sudo." >&2; exit 1; fi
if [ "${1:-}" != "--library" ] || [ "${3:-}" != "--group" ] || [ "$#" -lt 4 ]; then usage >&2; exit 2; fi
library=$2
group_name=$4
shift 4

# Only a dedicated, missing/empty path is safe to prepare automatically.
case "$library" in /*) ;; *) echo "Library path must be absolute: $library" >&2; exit 2;; esac
case "$library" in /|/usr|/usr/*) echo "Choose a dedicated data location such as /srv/SteamLibrary or a mounted disk." >&2; exit 2;; esac
if [ -L "$library" ]; then echo "Refusing symbolic-link library path: $library" >&2; exit 1; fi
if [ -e "$library" ] && [ ! -d "$library" ]; then echo "Library path exists but is not a directory: $library" >&2; exit 1; fi
if [ -d "$library" ] && [ -n "$(find -P "$library" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "Refusing to alter non-empty path: $library" >&2
    echo "Choose an empty directory, or use repair-shared-library.sh for an existing Steam library." >&2
    exit 1
fi
for user_name in "$@"; do if ! getent passwd "$user_name" >/dev/null; then echo "Unknown user: $user_name" >&2; exit 1; fi; done
if ! command -v setfacl >/dev/null 2>&1; then echo "setfacl is required. Install Ubuntu's 'acl' package first." >&2; exit 1; fi
if ! getent group "$group_name" >/dev/null; then groupadd -- "$group_name"; fi

# setgid and default ACLs make both existing and future Steam downloads shared.
install -d -o root -g "$group_name" -m 2775 "$library"
install -d -o root -g "$group_name" -m 2775 "$library/steamapps"
setfacl -m "g:$group_name:rwx,m::rwx,d:g:$group_name:rwx,d:m::rwx" "$library"
setfacl -m "g:$group_name:rwx,m::rwx,d:g:$group_name:rwx,d:m::rwx" "$library/steamapps"
for user_name in "$@"; do usermod -a -G "$group_name" "$user_name"; done
echo "Created shared library: $library"
if [ "$#" -gt 0 ]; then echo "Users added to $group_name: $*"; echo "Each user must sign out and back in before Steam sees the new group."; else echo "No users were added. Add them later with commands/add-user.sh or the graphical guide."; fi
echo "Then, in Steam -> Settings -> Storage, add: $library"
