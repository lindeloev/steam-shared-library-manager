#!/bin/sh
# Remove this project's Steam registration without touching shared game files.
set -eu
usage() { cat <<'EOF'
Usage: uninstall.sh [--remove-prefixes] USER [USER ...]

Removes the personal-settings tool. It never deletes shared games. The optional
flag also discards personal Windows-game settings and non-cloud saves.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this uninstall script with sudo." >&2; exit 1; fi
remove_prefixes=false
if [ "${1:-}" = "--remove-prefixes" ]; then remove_prefixes=true; shift; fi
if [ "$#" -eq 0 ]; then usage >&2; exit 2; fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/_common.sh"

# Validate all users and ensure Steam cannot rewrite files during removal.
for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    if [ -z "$home_dir" ] || [ ! -d "$home_dir" ]; then echo "Unknown user or unavailable home directory: $user_name" >&2; exit 1; fi
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    if [ -L "$steam_root/compatibilitytools.d" ]; then echo "Refusing symlinked compatibilitytools.d for $user_name." >&2; exit 1; fi
    if [ "$remove_prefixes" = true ]; then
        for prefix_root in "$home_dir/.local/share/steam-shared-library-proton" "$home_dir/.local/share/steam-per-user-proton"; do
            if [ -e "$prefix_root" ] || [ -L "$prefix_root" ]; then
                require_existing_path_below_home "$home_dir" "$prefix_root" >/dev/null || exit 1
            fi
        done
    fi
    if pgrep -u "$user_name" -x steam >/dev/null 2>&1; then echo "Steam is still running for $user_name. Close it first." >&2; exit 1; fi
done

# Remove only this tool. Shared games are deliberately outside these paths.
for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    tool_dir="$steam_root/compatibilitytools.d/steam-shared-library-proton"
    legacy_tool_dir="$steam_root/compatibilitytools.d/steam-per-user-proton"
    if [ -d "$tool_dir" ]; then rm -rf -- "$tool_dir"; echo "Removed tool registration for $user_name"; fi
    if [ -d "$legacy_tool_dir" ]; then rm -rf -- "$legacy_tool_dir"; echo "Removed legacy tool registration for $user_name"; fi
    if [ "$remove_prefixes" = true ]; then
        for prefix_root in "$home_dir/.local/share/steam-shared-library-proton" "$home_dir/.local/share/steam-per-user-proton"; do
            if [ -d "$prefix_root" ]; then rm -rf -- "$prefix_root"; echo "Removed personal game setups for $user_name"; fi
        done
    fi
done
echo "Shared games and the shared library were left unchanged."
