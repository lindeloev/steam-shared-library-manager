#!/bin/sh
# Ask a user's Steam client to shut down before changing its configuration.
set -eu
usage() { cat <<'EOF'
Usage: close-steam.sh [--force] USER [USER ...]

Normally this asks each running Steam client to exit through its desktop
session. --force permits SIGTERM when that request is unavailable or ignored.
EOF
}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run this script with sudo or pkexec." >&2; exit 1; fi
force=0
if [ "${1:-}" = "--force" ]; then force=1; shift; fi
if [ "$#" -eq 0 ]; then usage >&2; exit 2; fi
. "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/_common.sh"

# Validate every account and normal-shutdown route before asking any client to
# exit.  With --force, a missing desktop bus is allowed because SIGTERM is the
# explicit fallback the caller requested.
for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    if [ -z "$home_dir" ] || [ ! -d "$home_dir" ]; then echo "Unknown user or unavailable home directory: $user_name" >&2; exit 1; fi
    if pgrep -u "$user_name" -x steam >/dev/null 2>&1 && [ "$force" -eq 0 ]; then
        steam_root=$(require_safe_steam_root "$user_name") || exit 1
        if [ ! -x "$steam_root/steam.sh" ]; then echo "Native Steam launcher is missing for $user_name: $steam_root/steam.sh" >&2; exit 1; fi
        user_uid=$(id -u "$user_name")
        runtime_dir="/run/user/$user_uid"
        if [ ! -S "$runtime_dir/bus" ]; then
            echo "Cannot contact $user_name's desktop session: $runtime_dir/bus is unavailable." >&2
            echo "Ask $user_name to choose Steam -> Exit, wait for it to close, then retry. No configuration was changed." >&2
            exit 1
        fi
    fi
done

wait_for_steam() {
    wait_user=$1
    wait_limit=$2
    wait_context=$3
    waited=0
    while pgrep -u "$wait_user" -x steam >/dev/null 2>&1 && [ "$waited" -lt "$wait_limit" ]; do
        sleep 5
        waited=$((waited + 5))
        if pgrep -u "$wait_user" -x steam >/dev/null 2>&1; then
            echo "Steam is still $wait_context for $wait_user (${waited}s elapsed)."
        fi
    done
    ! pgrep -u "$wait_user" -x steam >/dev/null 2>&1
}

for user_name in "$@"; do
    home_dir=$(user_home "$user_name")
    if pgrep -u "$user_name" -x steam >/dev/null 2>&1; then
        user_uid=$(id -u "$user_name")
        runtime_dir="/run/user/$user_uid"
        steam_root=$(steam_root_for_user "$user_name" 2>/dev/null || true)
        normal_request_available=0
        if [ -n "$steam_root" ] && [ -x "$steam_root/steam.sh" ] && [ -S "$runtime_dir/bus" ]; then
            normal_request_available=1
        fi

        if [ "$normal_request_available" -eq 1 ]; then
            echo "$user_name: requesting a normal Steam exit"
            # Steam's launcher must inherit the account's session bus to send
            # IPC to the existing client instead of opening a new context.
            if ! runuser -u "$user_name" -- env HOME="$home_dir" XDG_RUNTIME_DIR="$runtime_dir" \
                DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" "$steam_root/steam.sh" -shutdown; then
                if [ "$force" -eq 0 ]; then
                    echo "Steam's normal exit request failed for $user_name. Ask them to choose Steam -> Exit, then retry." >&2
                    exit 1
                fi
                echo "$user_name: normal exit request failed; --force allows the SIGTERM fallback."
            fi
            if wait_for_steam "$user_name" 30 "closing"; then
                echo "Native Steam closed for $user_name."
                continue
            fi
        elif [ "$force" -eq 1 ]; then
            echo "$user_name: desktop shutdown route is unavailable; --force allows the SIGTERM fallback."
        fi

        if [ "$force" -eq 1 ]; then
            echo "$user_name: sending SIGTERM to Steam's main process"
            pkill -TERM -u "$user_name" -x steam || true
            if wait_for_steam "$user_name" 15 "stopping after SIGTERM"; then
                echo "Native Steam stopped for $user_name."
                continue
            fi
            echo "Native Steam is still running for $user_name after SIGTERM. Ask them to close Steam or end their session, then retry." >&2
            exit 1
        fi

        echo "Native Steam is still running for $user_name after 30 seconds." >&2
        echo "Ask $user_name to choose Steam -> Exit and retry, or deliberately use --force after checking that no game or download is active." >&2
        exit 1
    else
        echo "Steam is already closed for $user_name."
    fi
done
