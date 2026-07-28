#!/bin/sh
# Convert explicit built-in Proton selections to this tool.
set -eu
usage() { cat <<'EOF'
Usage: migrate-existing-games.sh --dry-run USER [USER ...]
       migrate-existing-games.sh --apply USER [USER ...]
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this migration script with sudo." >&2; exit 1; fi
mode=${1:-}
if [ "$mode" != "--dry-run" ] && [ "$mode" != "--apply" ]; then usage >&2; exit 2; fi
shift
if [ "$#" -eq 0 ]; then usage >&2; exit 2; fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/_common.sh"

tmp_file=
active_user=
finish() {
    status=$?
    trap - 0 HUP INT TERM
    if [ -n "$tmp_file" ] && [ -e "$tmp_file" ]; then
        rm -f -- "$tmp_file"
    fi
    if [ "$status" -ne 0 ] && [ -n "$active_user" ]; then
        echo "Stopped while migrating $active_user. Earlier accounts may already be complete; inspect the reported backup and safely rerun this command." >&2
    fi
    exit "$status"
}
trap finish 0
trap 'exit 1' HUP INT TERM

# Confirm every requested account is safe to edit before writing a config file.
for user_name in "$@"; do
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    config=$(realpath -e "$steam_root/config/config.vdf" 2>/dev/null) || { echo "$user_name has no Steam config." >&2; exit 1; }
    case "$config" in "$steam_root"/*) ;; *) echo "Refusing Steam config outside its Steam root for $user_name." >&2; exit 1;; esac
    if [ ! -d "$steam_root/compatibilitytools.d/steam-shared-library-proton" ]; then echo "$user_name does not have the personal-settings tool installed." >&2; exit 1; fi
    if pgrep -u "$user_name" -x steam >/dev/null 2>&1; then echo "Steam is still running for $user_name. Close it first." >&2; exit 1; fi
done

# Count first for a useful dry run; --apply writes a backup before replacement.
for user_name in "$@"; do
    active_user=$user_name
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    config=$(realpath -e "$steam_root/config/config.vdf")
    group_name=$(id -gn "$user_name")
    changes=$(awk '/"CompatToolMapping"/ { waiting = 1 } waiting && /{/ { in_mapping = 1; depth = 1; waiting = 0; next } in_mapping { opens = gsub(/{/, "{"); closes = gsub(/}/, "}"); if ($0 ~ /"name"[[:space:]]*"proton[^\"]*"/) count++; depth += opens - closes; if (depth == 0) in_mapping = 0 } END { print count + 0 }' "$config")
    if [ "$changes" -eq 0 ]; then echo "$user_name: no explicit built-in Proton mappings to migrate"; continue; fi
    if [ "$mode" = "--dry-run" ]; then echo "$user_name: would migrate $changes explicit built-in Proton mapping(s)"; continue; fi
    backup=$(mktemp "$config.per-user-proton-backup.$(date +%Y%m%d-%H%M%S).XXXXXX")
    if ! cp -p -- "$config" "$backup"; then
        rm -f -- "$backup"
        exit 1
    fi
    echo "$user_name: configuration backup prepared: $backup"
    tmp_file=$(mktemp "$config.per-user-proton.XXXXXX")
    chmod 0600 "$backup"
    chown "$user_name:$group_name" "$backup"
    awk '/"CompatToolMapping"/ { waiting = 1 } waiting && /{/ { in_mapping = 1; depth = 1; waiting = 0; print; next } in_mapping && /"name"[[:space:]]*"proton[^\"]*"/ { sub(/"proton[^\"]*"/, "\"steam-shared-library-proton\"") } { print; if (in_mapping) { opens = gsub(/{/, "{"); closes = gsub(/}/, "}"); depth += opens - closes; if (depth == 0) in_mapping = 0 } }' "$config" > "$tmp_file"
    chown "$user_name:$group_name" "$tmp_file"
    chmod 0600 "$tmp_file"
    mv -f -- "$tmp_file" "$config"
    tmp_file=
    echo "$user_name: migrated $changes mapping(s); backup: $backup"
done
active_user=
