#!/usr/bin/python3
"""Register shared game storage while keeping automatic tool installs local."""

from __future__ import annotations

import argparse
import os
import pwd
import re
import secrets
import stat
import subprocess
import uuid
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
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_size > 16 * 1024 * 1024:
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
    # Pin the parent directory and use descriptors for metadata. A replaced
    # parent/symlink cannot redirect metadata operations to unrelated files.
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    backup_name = path.name + ".steam-shared-library-manager-backup." + uuid.uuid4().hex
    temporary_name = path.name + ".steam-shared-library-manager." + uuid.uuid4().hex
    def current_text():
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                raise ValueError(f"Unsafe Steam configuration: {path}")
            return handle.read(), info
    def write_new(name, value, mode):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchown(handle.fileno(), user.pw_uid, user.pw_gid)
            os.fchmod(handle.fileno(), mode)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    try:
        current, info = current_text()
        if current != expected:
            raise ValueError(f"Steam configuration changed after preflight; refusing to overwrite: {path}")
        if expected == text:
            return None
        mode = stat.S_IMODE(info.st_mode)
        write_new(backup_name, expected, 0o600)
        write_new(temporary_name, text, mode)
        if current_text()[0] != expected:
            raise ValueError(f"Steam configuration changed while updating; refusing to overwrite: {path}")
        os.replace(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        return path.parent / backup_name
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def prepare_user(
    user_name: str, library: Path | None
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
    if library is None:
        folders_text, index = original_folders, 0
    else:
        folders_text, index = ensure_library_registered(
            original_folders,
            str(library),
            secrets.randbelow(9_000_000_000_000_000_000) + 1,
        )
    # Steam has one default for games and tools. Never select the shared folder:
    # automatic Proton/runtime downloads must use this account's primary library.
    primary_paths = [
        path for path, folder_index in library_indexes(folders_text).items()
        if folder_index == 0 and Path(path).resolve(strict=True) == steam_root
    ]
    if not primary_paths:
        raise ValueError("Steam's primary library (index 0) does not match its local root")
    localconfigs = sorted((steam_root / "userdata").glob("[0-9]*/config/localconfig.vdf"))
    if not localconfigs:
        raise ValueError(f"{user_name} has no initialized Steam account configuration")
    safe_localconfigs = [safe_file(steam_root, path) for path in localconfigs]
    localconfig_updates = []
    for localconfig in safe_localconfigs:
        original = read_regular_text(localconfig)
        localconfig_updates.append(
            (localconfig, original, set_default_folder_index(original, 0))
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
        print(f"{user_name}: library registration unchanged")
    for localconfig, original, updated in localconfig_updates:
        backup = atomic_update(localconfig, original, updated, user)
        account_id = localconfig.parents[1].name
        if backup is not None:
            print(f"{user_name}: made local storage default for Steam account {account_id}; backup: {backup}")
        else:
            print(f"{user_name}: local storage already default for Steam account {account_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register shared game storage and keep local storage default for Proton and runtimes."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--library", help="Shared game library to register")
    selection.add_argument("--local-only", action="store_true", help="Keep local storage default without registering a library")
    parser.add_argument("users", nargs="+")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this script with sudo or pkexec.")
    worker_args = ["/usr/bin/python3", "-I", "-B", str(Path(__file__).with_name("account-worker.py")), "--action", "storage"]
    if args.library:
        requested = Path(args.library)
        if not requested.is_absolute() or requested.resolve() != Path(os.path.abspath(requested)):
            raise SystemExit("Shared library must be an absolute path without symlinks")
        worker_args += ["--library", str(requested)]
    raise SystemExit(subprocess.call([*worker_args, *args.users]))


if __name__ == "__main__":
    main()
