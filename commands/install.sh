#!/bin/sh
# Install the shared-library personal-settings tool for existing Linux accounts.
set -eu
usage() { cat <<'EOF'
Usage: install.sh --base-proton PATH USER [USER ...]

PATH is the directory containing Proton's executable `proton` file. Each USER
must have run Steam at least once. Steam must be fully closed while this runs.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this installer with sudo." >&2; exit 1; fi
if [ "${1:-}" != "--base-proton" ] || [ "$#" -lt 3 ]; then usage >&2; exit 2; fi

# Locate the checked-out assets before touching any user configuration.
base_proton=$2
shift 2
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
if [ ! -f "$project_dir/steam-shared-library-proton.vdf" ] ||
   [ ! -f "$project_dir/toolmanifest.vdf" ] ||
   [ ! -f "$script_dir/proton" ]; then
    echo "Project files are incomplete; run from a complete checkout." >&2
    exit 1
fi
. "$script_dir/_common.sh"
base_proton=$(require_plain_absolute_path "$base_proton") || exit 1
if [ ! -x "$base_proton/proton" ]; then echo "Base Proton not found or not executable: $base_proton/proton" >&2; exit 1; fi

tmp_file=
active_user=
finish() {
    status=$?
    trap - 0 HUP INT TERM
    if [ -n "$tmp_file" ] && [ -e "$tmp_file" ]; then
        rm -f -- "$tmp_file"
    fi
    if [ "$status" -ne 0 ] && [ -n "$active_user" ]; then
        echo "Stopped while installing for $active_user. Earlier accounts may already be complete; correct the error and safely rerun this command." >&2
    fi
    exit "$status"
}
trap finish 0
trap 'exit 1' HUP INT TERM

# Preflight all users first, so a missing Steam setup cannot leave another
# account partially installed.
for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    if pgrep -u "$user_name" -x steam >/dev/null 2>&1; then echo "Steam is still running for $user_name. Close it first." >&2; exit 1; fi
    if [ -L "$steam_root/compatibilitytools.d" ]; then echo "Refusing symlinked compatibilitytools.d for $user_name." >&2; exit 1; fi
    config="$steam_root/config/config.vdf"
    if [ -e "$config" ] || [ -L "$config" ]; then
        require_existing_path_below "$steam_root" "$config" >/dev/null || exit 1
    fi
    for prefix_root in "$home_dir/.local/share/steam-shared-library-proton" "$home_dir/.local/share/steam-per-user-proton"; do
        if [ -e "$prefix_root" ] || [ -L "$prefix_root" ]; then
            require_existing_path_below_home "$home_dir" "$prefix_root" >/dev/null || exit 1
        fi
    done
done

# Install the small compatibility tool into each user's own Steam directory.
for user_name in "$@"; do
    active_user=$user_name
    echo "$user_name: installing personal-settings tool"
    home_dir=$(user_home "$user_name")
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    tool_dir="$steam_root/compatibilitytools.d/steam-shared-library-proton"
    legacy_tool_dir="$steam_root/compatibilitytools.d/steam-per-user-proton"
    if [ -L "$tool_dir" ]; then echo "Refusing symlinked tool directory for $user_name." >&2; exit 1; fi
    install -d -o "$user_name" -g "$(id -gn "$user_name")" -m 0755 "$tool_dir"
    rm -f "$steam_root/compatibilitytools.d/steam-per-user-proton.vdf"
    old_tool_file="$steam_root/compatibilitytools.d/compatibilitytool.vdf"
    if [ -e "$old_tool_file" ] && grep -q '"steam-per-user-proton"' "$old_tool_file"; then rm -f "$old_tool_file"; fi
    rm -f "$tool_dir/compatibilitytool.vdf" "$tool_dir/toolmanifest.vdf" "$tool_dir/proton" "$tool_dir/base-proton.conf"
    install -o "$user_name" -g "$(id -gn "$user_name")" -m 0644 "$project_dir/steam-shared-library-proton.vdf" "$tool_dir/compatibilitytool.vdf"
    install -o "$user_name" -g "$(id -gn "$user_name")" -m 0644 "$project_dir/toolmanifest.vdf" "$tool_dir/toolmanifest.vdf"
    install -o "$user_name" -g "$(id -gn "$user_name")" -m 0755 "$script_dir/proton" "$tool_dir/proton"
    printf 'BASE_PROTON=%s\n' "$base_proton" > "$tool_dir/base-proton.conf"
    chown "$user_name:$(id -gn "$user_name")" "$tool_dir/base-proton.conf"
    chmod 0644 "$tool_dir/base-proton.conf"

    # One-time rename migration: preserve Steam's choices and private game data
    # from the earlier steam-per-user-proton identifier.
    config="$steam_root/config/config.vdf"
    if [ -f "$config" ] && grep -q '"steam-per-user-proton"' "$config"; then
        backup=$(mktemp "$config.steam-shared-library-proton-backup.$(date +%Y%m%d-%H%M%S).XXXXXX")
        if ! cp -p -- "$config" "$backup"; then
            rm -f -- "$backup"
            exit 1
        fi
        echo "$user_name: configuration backup prepared: $backup"
        tmp_file=$(mktemp "$config.steam-shared-library-proton.XXXXXX")
        sed 's/"steam-per-user-proton"/"steam-shared-library-proton"/g' "$config" > "$tmp_file"
        chown "$user_name:$(id -gn "$user_name")" "$tmp_file" "$backup"
        chmod 0600 "$tmp_file" "$backup"
        mv -f -- "$tmp_file" "$config"
        tmp_file=
        echo "$user_name: migrated old Steam compatibility mappings; backup: $backup"
    fi
    legacy_prefix_root="$home_dir/.local/share/steam-per-user-proton"
    prefix_root="$home_dir/.local/share/steam-shared-library-proton"
    if [ -d "$legacy_prefix_root" ] && [ ! -e "$prefix_root" ]; then
        mv -- "$legacy_prefix_root" "$prefix_root"
        echo "$user_name: moved existing personal game settings to the new name."
    elif [ -d "$legacy_prefix_root" ]; then
        echo "$user_name: kept existing legacy personal game settings because the new location already exists."
    fi
    if [ -d "$prefix_root" ]; then
        make_prefix_tree_private "$home_dir" "$prefix_root" "$user_name"
        echo "$user_name: made existing personal game settings private."
    fi
    if [ -d "$legacy_tool_dir" ]; then rm -rf -- "$legacy_tool_dir"; fi
    echo "$user_name: installation complete"
done
active_user=
echo "Installed the shared-library personal-settings tool. Start Steam for each"
echo "listed user, then choose 'Shared library – personal settings (Proton)'"
echo "once in Settings -> Compatibility."
