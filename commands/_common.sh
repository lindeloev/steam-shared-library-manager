#!/bin/sh
# Shared validation helpers for administrative command scripts.

user_home() {
    getent passwd "$1" | awk -F: 'NR == 1 { print $6 }'
}

require_plain_absolute_path() {
    plain_path=$1
    case "$plain_path" in
        /*) ;;
        *) echo "Path must be absolute: $plain_path" >&2; return 1 ;;
    esac
    plain_newline='
'
    plain_carriage_return=$(printf '\r')
    plain_tab=$(printf '\t')
    case "$plain_path" in
        *"$plain_newline"*|*"$plain_carriage_return"*|*"$plain_tab"*)
            echo "Path cannot contain control characters." >&2
            return 1
            ;;
    esac
    printf '%s\n' "$plain_path"
}

require_safe_library_path() {
    requested_library=$(require_plain_absolute_path "$1") || return 1
    canonical_library=$(realpath -m -- "$requested_library" 2>/dev/null) || return 1
    lexical_library=$(realpath -ms -- "$requested_library" 2>/dev/null) || return 1
    if [ "$canonical_library" != "$lexical_library" ]; then
        echo "Refusing symbolic links in library path: $requested_library" >&2
        return 1
    fi
    case "$canonical_library" in
        /|/usr|/usr/*)
            echo "Choose a dedicated data location such as /srv/SteamLibrary or a mounted disk." >&2
            return 1
            ;;
    esac
    printf '%s\n' "$canonical_library"
}

require_not_personal_steam_root() {
    checked_library=$1
    if ! getent passwd | while IFS=: read -r _name _password user_id _group_id _description account_home account_shell; do
        [ "$user_id" -ge 1000 ] 2>/dev/null || continue
        case "$account_shell" in */nologin|*/false) continue ;; esac
        account_steam_root=$(steam_root_for_home "$account_home" 2>/dev/null) || continue
        [ "$account_steam_root" != "$checked_library" ] || exit 1
    done; then
        echo "Refusing to use a person's primary Steam folder as the shared library: $checked_library" >&2
        return 1
    fi
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
