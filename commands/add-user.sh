#!/bin/sh
# Add existing Linux accounts to an already-created shared Steam library.
set -eu
usage() { cat <<'EOF'
Usage: add-user.sh [--close-steam] [--default-library PATH] --group GROUP --base-proton PATH USER [USER ...]
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this script with sudo or pkexec." >&2; exit 1; fi
close_steam=false
if [ "${1:-}" = "--close-steam" ]; then close_steam=true; shift; fi
default_library=
if [ "${1:-}" = "--default-library" ]; then
    [ "$#" -ge 2 ] || { usage >&2; exit 2; }
    default_library=$2
    [ -d "$default_library/steamapps" ] || {
        echo "Not a Steam library: $default_library" >&2
        exit 1
    }
    shift 2
fi
if [ "${1:-}" != "--group" ] || [ "${3:-}" != "--base-proton" ] || [ "$#" -lt 5 ]; then usage >&2; exit 2; fi
group_name=$2
base_proton=$4
shift 4
if ! getent group "$group_name" >/dev/null; then echo "Shared-library group does not exist: $group_name" >&2; exit 1; fi
[ -x "$base_proton/proton" ] || { echo "Base Proton not found or not executable: $base_proton/proton" >&2; exit 1; }
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/_common.sh"

# Validate every account before changing any group membership.
for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    steam_root=$(require_safe_steam_root "$user_name") || exit 1
    if [ -L "$steam_root/compatibilitytools.d" ]; then
        echo "Refusing symlinked compatibilitytools.d for $user_name." >&2
        exit 1
    fi
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

# Use the dedicated shutdown command so GUI and command-line setup inherit the
# same desktop-session handling and timeout behavior.
if [ "$close_steam" = true ]; then
    "$script_dir/close-steam.sh" "$@"
else
    for user_name in "$@"; do
        if pgrep -u "$user_name" -x steam >/dev/null 2>&1; then
            echo "Steam is running for $user_name. Close it first, or use --close-steam." >&2
            exit 1
        fi
    done
fi

# Membership changes take effect for graphical sessions after the next login.
for user_name in "$@"; do
    usermod -a -G "$group_name" "$user_name"
done
"$script_dir/install.sh" --base-proton "$base_proton" "$@"
if [ -n "$default_library" ]; then
    "$script_dir/configure-steam-storage.py" --library "$default_library" "$@"
fi
echo "Each listed user must log out of Ubuntu and back in before using the shared library."
