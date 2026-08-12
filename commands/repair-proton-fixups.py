#!/usr/bin/env python3
"""Restore pending SteamPipe modes in one shared official Proton install."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import NoReturn


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def regular_file(path: Path, description: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        fail(f"Could not inspect {description}: {error}")
    if not stat.S_ISREG(result.st_mode):
        fail(f"Refusing {description} that is not a regular file: {path}")
    return result


def validated_proton(library: Path, proton: Path) -> tuple[Path, Path]:
    requested_library = Path(os.path.abspath(library))
    requested_proton = Path(os.path.abspath(proton))
    try:
        library = library.resolve(strict=True)
        proton = proton.resolve(strict=True)
        common = (library / "steamapps/common").resolve(strict=True)
    except OSError as error:
        fail(f"Could not resolve the Steam library or Proton installation: {error}")
    if library in (Path("/"), Path("/usr")) or str(library).startswith("/usr/"):
        fail(f"Refusing unsafe library path: {library}")
    if requested_library != library or requested_proton != proton:
        fail("Refusing symbolic links in the library or Proton path.")
    if not (library / "steamapps").is_dir() or proton.parent != common:
        fail("The selected Proton installation is not directly inside this library's steamapps/common directory.")
    if not proton.name.startswith("Proton"):
        fail(f"Refusing a non-Proton directory: {proton}")
    launcher = regular_file(proton / "proton", "Proton launcher")
    if not launcher.st_mode & 0o111:
        fail(f"Proton launcher is not executable: {proton / 'proton'}")
    return library, proton


def relevant_processes(proton: Path) -> list[str]:
    """Return Steam or selected-Proton processes, excluding this repair."""
    found: list[str] = []
    own_pid = os.getpid()
    for process in Path("/proc").glob("[0-9]*"):
        if int(process.name) == own_pid:
            continue
        try:
            comm = (process / "comm").read_text(errors="replace").strip()
            command = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if comm == "steam" or str(proton) in command:
            found.append(f"{process.name} ({comm or 'unknown'})")
    return found


def safe_manifest_path(proton: Path, value: object, kind: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        fail(f"Invalid {kind} path in steampipe_fixups.json")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"Unsafe {kind} path in steampipe_fixups.json: {value}")
    target = proton / relative
    try:
        target.resolve(strict=False).relative_to(proton)
    except (OSError, ValueError):
        fail(f"{kind.capitalize()} path escapes the Proton installation: {value}")
    if target.is_symlink():
        fail(f"Refusing symlinked {kind} path in steampipe_fixups.json: {value}")
    return target


def restore(proton: Path) -> int:
    manifest = proton / "steampipe_fixups.json"
    marker = proton / "files/steampipe_fixups_mtime"
    regular_file(manifest, "SteamPipe fixups manifest")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Could not read SteamPipe fixups manifest: {error}")
    if not isinstance(data, dict):
        fail("SteamPipe fixups manifest is not an object.")
    empty_dirs = data.get("empty_dirs")
    no_write_paths = data.get("no_write_paths")
    if not isinstance(empty_dirs, list) or not isinstance(no_write_paths, list):
        fail("SteamPipe fixups manifest has invalid path lists.")

    directories = [safe_manifest_path(proton, value, "directory") for value in empty_dirs]
    files = [safe_manifest_path(proton, value, "file") for value in no_write_paths]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.resolve(strict=True).relative_to(proton)
        except (OSError, ValueError):
            fail(f"Created directory escaped the Proton installation: {directory}")
    for path in files:
        result = regular_file(path, "fixup target")
        path.chmod(stat.S_IMODE(result.st_mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))

    expected = str(os.path.getmtime(manifest))
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists() or marker.is_symlink():
        regular_file(marker, "SteamPipe fixups marker")
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o664)
        try:
            marker_status = os.fstat(descriptor)
            if not stat.S_ISREG(marker_status.st_mode):
                fail(f"SteamPipe fixups marker is not a regular file: {marker}")
            os.fchown(descriptor, -1, marker.parent.stat().st_gid)
            os.fchmod(descriptor, stat.S_IMODE(marker_status.st_mode) | stat.S_IRGRP | stat.S_IWGRP)
            os.write(descriptor, (expected + "\n").encode())
        finally:
            os.close(descriptor)
    except OSError as error:
        fail(f"Could not write SteamPipe fixups marker: {error}")
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--proton", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("Run this repair command as root.")
    _library, proton = validated_proton(Path(args.library), Path(args.proton))
    active = relevant_processes(proton)
    if active:
        fail("Close Steam and Proton for all users before repairing pending fixups: " + ", ".join(active))
    changed = restore(proton)
    print(f"Restored pending SteamPipe permissions for {proton} ({changed} file modes).")


if __name__ == "__main__":
    main()
