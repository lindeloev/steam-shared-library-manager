#!/bin/sh
# Shared validation helpers for administrative command scripts.

user_home() {
    getent passwd "$1" | awk -F: 'NR == 1 { print $6 }'
}

# Steam's normal Debian/Ubuntu root is a symlink; accept it only when its final
# destination remains inside the account's home directory.
steam_root_for_home() {
    home_dir=$1
    home_dir=$(realpath -e "$home_dir" 2>/dev/null) || return 1
    steam_root=$(realpath -e "$home_dir/.steam/root" 2>/dev/null) || return 1
    [ -d "$steam_root" ] || return 1
    case "$steam_root" in
        "$home_dir"/*) printf '%s\n' "$steam_root" ;;
        *) return 1 ;;
    esac
}

steam_root_for_user() {
    home_dir=$(user_home "$1")
    [ -n "$home_dir" ] && [ -d "$home_dir" ] || return 1
    steam_root_for_home "$home_dir"
}

require_safe_steam_root() {
    steam_root=$(steam_root_for_user "$1") || {
        echo "$1 has no safe Steam root below its home directory." >&2
        return 1
    }
    printf '%s\n' "$steam_root"
}

require_existing_path_below() {
    validation_root=$(realpath -e "$1" 2>/dev/null) || return 1
    validation_path=$(realpath -e "$2" 2>/dev/null) || return 1
    case "$validation_path" in
        "$validation_root"/*) printf '%s\n' "$validation_path" ;;
        *)
            echo "Refusing path outside $validation_root: $2" >&2
            return 1
            ;;
    esac
}

require_existing_path_below_home() {
    require_existing_path_below "$1" "$2"
}

make_prefix_tree_private() {
    private_prefix_root=$(require_existing_path_below_home "$1" "$2") || return 1
    [ -d "$private_prefix_root" ] || return 0
    private_prefix_user=$3
    if [ "$(id -u)" -eq "$(id -u "$private_prefix_user")" ]; then
        chmod 0700 -- "$private_prefix_root"
        find -P "$private_prefix_root" -mindepth 1 -maxdepth 1 -type d \
            -exec chmod 0700 -- {} +
    else
        runuser -u "$private_prefix_user" -- chmod 0700 -- "$private_prefix_root"
        find -P "$private_prefix_root" -mindepth 1 -maxdepth 1 -type d \
            -exec runuser -u "$private_prefix_user" -- chmod 0700 -- {} +
    fi
}
