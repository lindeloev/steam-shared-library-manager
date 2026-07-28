#!/usr/bin/python3
"""Register a shared Steam library and make it the per-account default."""

from __future__ import annotations

import argparse
import os
import pwd
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


def braced_value(text: str, opening_brace: int) -> tuple[int, int] | None:
    """Return the body bounds of a VDF object, ignoring braces in strings."""
    depth = 0
    quoted = False
    escaped = False
    for position in range(opening_brace, len(text)):
        character = text[position]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return opening_brace + 1, position
    return None


def object_bounds(text: str, key: str) -> tuple[int, int] | None:
    match = re.search(rf'"{re.escape(key)}"\s*\{{', text)
    if match is None:
        return None
    opening = text.find("{", match.start())
    return braced_value(text, opening)


def top_level_items(text: str, object_key: str) -> list[tuple[str, str, int, int]]:
    """Return scalar/object items directly inside one VDF object."""
    bounds = object_bounds(text, object_key)
    if bounds is None:
        return []
    body_start, body_end = bounds
    position = body_start
    items: list[tuple[str, str, int, int]] = []
    key_pattern = re.compile(r'"((?:\\.|[^"])*)"\s*')
    while position < body_end:
        match = key_pattern.search(text, position, body_end)
        if match is None:
            break
        key = match.group(1)
        value_start = match.end()
        if value_start >= body_end:
            break
        if text[value_start] == "{":
            result = braced_value(text, value_start)
            if result is None:
                break
            _nested_start, nested_end = result
            items.append((key, "object", value_start, nested_end + 1))
            position = nested_end + 1
        elif text[value_start] == '"':
            value_end = value_start + 1
            escaped = False
            while value_end < body_end:
                character = text[value_end]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    break
                value_end += 1
            items.append((key, "scalar", value_start + 1, value_end))
            position = value_end + 1
        else:
            position = value_start + 1
    return items


def vdf_unescape(value: str) -> str:
    return value.replace("\\\\", "\\").replace('\\"', '"')


def vdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def library_indexes(text: str) -> dict[str, int]:
    """Map registered library paths to their folder indexes."""
    result: dict[str, int] = {}
    for key, kind, value_start, value_end in top_level_items(text, "libraryfolders"):
        if kind != "object" or not key.isdigit():
            continue
        body = text[value_start:value_end]
        match = re.search(r'"path"\s*"((?:\\.|[^"])*)"', body)
        if match is not None:
            result[vdf_unescape(match.group(1))] = int(key)
    return result


def ensure_library_registered(text: str, library: str, content_id: int) -> tuple[str, int]:
    indexes = library_indexes(text)
    if library in indexes:
        return text, indexes[library]
    bounds = object_bounds(text, "libraryfolders")
    if bounds is None:
        raise ValueError("libraryfolders.vdf has no libraryfolders object")
    existing_indexes = [
        int(key)
        for key, kind, _start, _end in top_level_items(text, "libraryfolders")
        if kind == "object" and key.isdigit()
    ]
    index = max(existing_indexes, default=-1) + 1
    entry = (
        f'\t"{index}"\n'
        "\t{\n"
        f'\t\t"path"\t\t"{vdf_escape(library)}"\n'
        f'\t\t"label"\t\t"{vdf_escape(library)}"\n'
        f'\t\t"contentid"\t\t"{content_id}"\n'
        '\t\t"totalsize"\t\t"0"\n'
        '\t\t"update_clean_bytes_tally"\t\t"0"\n'
        '\t\t"time_last_update_verified"\t\t"0"\n'
        '\t\t"apps"\n'
        "\t\t{\n"
        "\t\t}\n"
        "\t}\n"
    )
    _body_start, body_end = bounds
    return text[:body_end] + entry + text[body_end:], index


def set_default_folder_index(text: str, index: int) -> str:
    for key, kind, value_start, value_end in top_level_items(text, "UserLocalConfigStore"):
        if key == "LastInstallFolderIndex" and kind == "scalar":
            return text[:value_start] + str(index) + text[value_end:]
    bounds = object_bounds(text, "UserLocalConfigStore")
    if bounds is None:
        raise ValueError("localconfig.vdf has no UserLocalConfigStore object")
    _body_start, body_end = bounds
    entry = f'\t"LastInstallFolderIndex"\t\t"{index}"\n'
    return text[:body_end] + entry + text[body_end:]


def safe_steam_root(home: Path) -> Path:
    resolved_home = home.resolve(strict=True)
    steam_root = (resolved_home / ".steam/root").resolve(strict=True)
    steam_root.relative_to(resolved_home)
    if steam_root == resolved_home or not steam_root.is_dir():
        raise ValueError("Steam root is not a safe directory below the account home")
    return steam_root


def safe_file(root: Path, path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked Steam configuration: {path}")
    resolved = path.resolve(strict=True)
    resolved.relative_to(root.resolve(strict=True))
    if not resolved.is_file():
        raise ValueError(f"not a regular Steam configuration file: {path}")
    return resolved


def read_regular_text(path: Path) -> str:
    """Read one regular file without following a final-component symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"not a regular Steam configuration file: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="strict") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def atomic_update(
    path: Path,
    expected: str,
    text: str,
    user: pwd.struct_passwd,
) -> Path | None:
    current = read_regular_text(path)
    if current != expected:
        raise ValueError(f"Steam configuration changed after preflight; refusing to overwrite: {path}")
    if expected == text:
        return None
    path_status = path.lstat()
    if not stat.S_ISREG(path_status.st_mode):
        raise ValueError(f"not a regular Steam configuration file: {path}")
    original_mode = stat.S_IMODE(path_status.st_mode)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_fd, backup_name = tempfile.mkstemp(
        prefix=path.name + f".steam-shared-library-manager-backup.{timestamp}.",
        dir=path.parent,
    )
    backup = Path(backup_name)
    with os.fdopen(backup_fd, "w", encoding="utf-8") as handle:
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(backup, user.pw_uid, user.pw_gid)
    backup.chmod(original_mode)

    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".steam-shared-library-manager.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, user.pw_uid, user.pw_gid)
        temporary.chmod(original_mode)
        if read_regular_text(path) != expected:
            raise ValueError(f"Steam configuration changed while updating; refusing to overwrite: {path}")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return backup


def prepare_user(
    user_name: str, library: Path
) -> tuple[
    pwd.struct_passwd,
    Path,
    str,
    str,
    int,
    list[tuple[Path, str, str]],
]:
    """Validate and prepare every change for one user without writing it."""
    user = pwd.getpwnam(user_name)
    home = Path(user.pw_dir)
    steam_root = safe_steam_root(home)
    if library == steam_root:
        raise ValueError("refusing to use this account's primary Steam folder as shared storage")
    if subprocess.run(
        ["pgrep", "-u", user_name, "-x", "steam"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0:
        raise ValueError(f"Steam is still running for {user_name}")

    folders_path = safe_file(steam_root, steam_root / "config/libraryfolders.vdf")
    original_folders = read_regular_text(folders_path)
    folders_text, index = ensure_library_registered(
        original_folders,
        str(library),
        secrets.randbelow(9_000_000_000_000_000_000) + 1,
    )
    localconfigs = sorted((steam_root / "userdata").glob("[0-9]*/config/localconfig.vdf"))
    if not localconfigs:
        raise ValueError(f"{user_name} has no initialized Steam account configuration")
    safe_localconfigs = [safe_file(steam_root, path) for path in localconfigs]
    localconfig_updates = []
    for localconfig in safe_localconfigs:
        original = read_regular_text(localconfig)
        localconfig_updates.append(
            (localconfig, original, set_default_folder_index(original, index))
        )
    return user, folders_path, original_folders, folders_text, index, localconfig_updates


def apply_user(
    plan: tuple[
        pwd.struct_passwd,
        Path,
        str,
        str,
        int,
        list[tuple[Path, str, str]],
    ]
) -> None:
    """Apply one fully validated user plan with per-file backups."""
    user, folders_path, original_folders, folders_text, _index, localconfig_updates = plan
    user_name = user.pw_name
    if folders_text != original_folders:
        backup = atomic_update(folders_path, original_folders, folders_text, user)
        print(f"{user_name}: registered shared storage; backup: {backup}")
    else:
        atomic_update(folders_path, original_folders, folders_text, user)
        print(f"{user_name}: shared storage already registered")
    for localconfig, original, updated in localconfig_updates:
        backup = atomic_update(localconfig, original, updated, user)
        account_id = localconfig.parents[1].name
        if backup is not None:
            print(f"{user_name}: made shared storage default for Steam account {account_id}; backup: {backup}")
        else:
            print(f"{user_name}: shared storage already default for Steam account {account_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a shared Steam library and make it the default for Linux users."
    )
    parser.add_argument("--library", required=True)
    parser.add_argument("users", nargs="+")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this script with sudo or pkexec.")
    requested_library = Path(args.library)
    if (
        not requested_library.is_absolute()
        or any(ord(character) < 32 or ord(character) == 127 for character in args.library)
    ):
        raise SystemExit(f"Invalid Steam library path: {requested_library}")
    lexical_library = Path(os.path.abspath(args.library))
    try:
        library = requested_library.resolve(strict=True)
    except OSError as error:
        raise SystemExit(f"Cannot resolve Steam library {requested_library}: {error}") from error
    if library != lexical_library:
        raise SystemExit(f"Refusing symbolic links in library path: {requested_library}")
    if library == Path("/") or library == Path("/usr") or Path("/usr") in library.parents:
        raise SystemExit(f"Unsafe Steam library path: {library}")
    if not (library / "steamapps").is_dir():
        raise SystemExit(f"Not a Steam library: {library}")
    plans = []
    for user_name in args.users:
        try:
            plans.append(prepare_user(user_name, library))
        except (KeyError, OSError, UnicodeError, ValueError) as error:
            raise SystemExit(f"{user_name}: {error}") from error
    for plan in plans:
        try:
            apply_user(plan)
        except (OSError, UnicodeError, ValueError) as error:
            raise SystemExit(f"{plan[0].pw_name}: {error}") from error


if __name__ == "__main__":
    main()
