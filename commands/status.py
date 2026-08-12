#!/usr/bin/env python3
"""Read-only status report consumed by the graphical manager.

It uses only Python's standard library and intentionally makes no changes. Run
it through pkexec when inspecting other users' private Steam directories.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path


TOOL_APP_IDS = {
    "1070560",  # Steam Linux Runtime
    "1161040",  # Proton BattlEye Runtime
    "1391110",
    "1493710",
    "1628350",
    "1826330",  # Proton EasyAntiCheat Runtime
    "2180100",
    "228980",   # Steamworks Common Redistributables
    "4183110",
}
TOOL_ID = "steam-shared-library-proton"
PREFIX_ROOT = Path(".local/share") / TOOL_ID


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def vdf_value(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*"([^"]*)"', text)
    return match.group(1) if match else ""


def vdf_braced_value(text: str, opening_brace: int) -> tuple[str, int] | None:
    """Return a brace-delimited VDF value and its closing-brace position."""
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
                return text[opening_brace + 1:position], position
    return None


def vdf_object(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*\{{', text)
    if match is None:
        return ""
    opening_brace = text.find("{", match.start())
    result = vdf_braced_value(text, opening_brace)
    return result[0] if result is not None else ""


def compat_tool_mappings(config: Path) -> dict[str, str]:
    """Return account-wide and per-game compatibility-tool selections."""
    section = vdf_object(read_text(config / "config.vdf"), "CompatToolMapping")
    mappings: dict[str, str] = {}
    position = 0
    entry_pattern = re.compile(r'"([^"]+)"\s*\{')
    while match := entry_pattern.search(section, position):
        opening_brace = section.find("{", match.start())
        result = vdf_braced_value(section, opening_brace)
        if result is None:
            break
        body, closing_brace = result
        name = vdf_value(body, "name")
        if name:
            mappings[match.group(1)] = name
        position = closing_brace + 1
    return mappings


def normal_users() -> list[pwd.struct_passwd]:
    return [entry for entry in pwd.getpwall()
            if entry.pw_uid >= 1000 and not entry.pw_shell.endswith(("nologin", "false"))]


def user_groups(user: pwd.struct_passwd) -> set[str]:
    """Use NSS-aware membership lookup, not only local /etc/group entries."""
    try:
        return {grp.getgrgid(group_id).gr_name for group_id in os.getgrouplist(user.pw_name, user.pw_gid)}
    except (AttributeError, KeyError, OSError):
        groups = {group.gr_name for group in grp.getgrall() if user.pw_name in group.gr_mem}
        try:
            groups.add(grp.getgrgid(user.pw_gid).gr_name)
        except KeyError:
            pass
        return groups


def safe_steam_root(home: Path) -> Path | None:
    """Resolve native Steam only when it remains below the account's home."""
    try:
        resolved_home = home.resolve(strict=True)
        steam_root = (resolved_home / ".steam/root").resolve(strict=True)
        steam_root.relative_to(resolved_home)
    except (OSError, ValueError):
        return None
    if steam_root == resolved_home:
        return None
    return steam_root if steam_root.is_dir() else None


def safe_existing_descendant(root: Path, path: Path) -> Path | None:
    """Resolve an existing path only when it remains strictly below root."""
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if resolved_path == resolved_root:
        return None
    return resolved_path


def steam_is_running(user_name: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-u", user_name, "-x", "steam"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def comparable_path(path: Path) -> Path:
    """Normalize a path for status comparisons without requiring it to exist."""
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def library_registered(config: Path, library: Path) -> bool:
    target = comparable_path(library)
    return any(comparable_path(Path(path)) == target for path in registered_libraries(config))


def registered_library_indexes(config: Path) -> dict[str, int]:
    """Return external library paths and their Steam folder indexes."""
    section = vdf_object(read_text(config / "libraryfolders.vdf"), "libraryfolders")
    indexes: dict[str, int] = {}
    position = 0
    entry_pattern = re.compile(r'"([0-9]+)"\s*\{')
    while match := entry_pattern.search(section, position):
        opening_brace = section.find("{", match.start())
        result = vdf_braced_value(section, opening_brace)
        if result is None:
            break
        body, closing_brace = result
        path = vdf_value(body, "path").replace("\\\\", "/")
        if path and match.group(1) != "0":
            indexes[path] = int(match.group(1))
        position = closing_brace + 1
    return indexes


def library_is_default(steam_root: Path, config: Path, library: Path) -> bool:
    """Check the per-Steam-account default install-folder selection."""
    target = comparable_path(library)
    index = next(
        (
            folder_index
            for path, folder_index in registered_library_indexes(config).items()
            if comparable_path(Path(path)) == target
        ),
        None,
    )
    if index is None:
        return False
    safe_configs = steam_localconfigs(steam_root)
    return bool(safe_configs) and all(
        vdf_value(read_text(path), "LastInstallFolderIndex") == str(index)
        for path in safe_configs
    )


def steam_localconfigs(steam_root: Path) -> list[Path]:
    """Return initialized account configs without following escapes."""
    localconfigs = sorted((steam_root / "userdata").glob("[0-9]*/config/localconfig.vdf"))
    return [
        resolved
        for path in localconfigs
        if (resolved := safe_existing_descendant(steam_root, path)) is not None
    ]


def personal_tool_selected(config: Path) -> bool:
    """Report the account-wide tool choice without changing Steam state."""
    return compat_tool_mappings(config).get("0") == TOOL_ID


def registered_libraries(config: Path) -> set[str]:
    """Return external Steam library paths recorded by one account."""
    return set(registered_library_indexes(config))


def library_kind(library: Path) -> str:
    """Classify a selected path without changing it."""
    if not library.exists():
        return "missing"
    if not library.is_dir():
        return "not_directory"
    if (library / "steamapps").is_dir():
        return "steam_library"
    try:
        next(library.iterdir())
    except StopIteration:
        return "empty_directory"
    except OSError:
        return "unreadable_directory"
    return "nonempty_other"


def shared_access_report(library: Path, users: list[pwd.struct_passwd], group_name: str) -> tuple[bool, list[str]]:
    """Check the sharing contract and return concise actionable failures."""
    problems: list[str] = []
    # Steam's container runtime creates writable state below its ``var``
    # directory.  Checking only the library root and steamapps makes a library
    # look healthy even when a download/update has left that directory private
    # to the account which installed it.  In that case Proton games start and
    # immediately stop with a fairly opaque pressure-vessel error.
    try:
        group_id = grp.getgrnam(group_name).gr_gid
    except KeyError:
        return False, [f"Shared-library group does not exist: {group_name}"]

    directories = [library, library / "steamapps"]
    common = library / "steamapps" / "common"
    try:
        runtime_vars = sorted(path / "var" for path in common.glob("SteamLinuxRuntime*")
                              if path.is_dir() and (path / "var").is_dir())
    except OSError:
        runtime_vars = []
    directories.extend(runtime_vars)
    try:
        for directory in directories:
            status = directory.stat()
            mode = status.st_mode
            if status.st_gid != group_id:
                problems.append(f"{directory}: group ownership is not {group_name}")
            if not mode & 0o020:
                problems.append(f"{directory}: group write permission is missing")
            if not mode & 0o2000:
                problems.append(f"{directory}: setgid permission is missing")
    except OSError:
        return False, [f"Could not inspect shared Steam directories below {library}"]
    if problems:
        return False, problems
    if os.geteuid() != 0:
        return True, []
    members = [user for user in users if group_name in user_groups(user)]
    for user in members:
        for directory in directories:
            for access in ("-r", "-w", "-x"):
                try:
                    # Use the POSIX shell builtin, not external coreutils
                    # /usr/bin/test. In an elevated runuser session the latter
                    # can incorrectly reject ACL-backed group write access.
                    result = subprocess.run(["runuser", "-u", user.pw_name, "--", "/bin/sh", "-c",
                                             f'test {access} "$1"', "sh", str(directory)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    problems.append(f"Could not test {user.pw_name}'s access to {directory}")
                    continue
                if result.returncode != 0:
                    label = {"-r": "read", "-w": "write", "-x": "enter"}[access]
                    problems.append(f"{user.pw_name} cannot {label} {directory}")
    return not problems, problems


def user_status(user: pwd.struct_passwd, library: Path, group_name: str) -> dict[str, object]:
    home = Path(user.pw_dir)
    steam_root = safe_steam_root(home)
    snap_root = home / "snap/steam/common/.local/share/Steam"
    flatpak_root = home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    steam_clients = [
        name
        for name, path in (("native", steam_root), ("snap", snap_root), ("flatpak", flatpak_root))
        if path is not None and path.is_dir()
    ]
    steam_client = steam_clients[0] if len(steam_clients) == 1 else "multiple" if steam_clients else "not_started"
    config = (
        safe_existing_descendant(steam_root, steam_root / "config")
        if steam_root is not None else None
    )
    tool_dir = (
        safe_existing_descendant(steam_root, steam_root / "compatibilitytools.d" / TOOL_ID)
        if steam_root is not None else None
    )
    resolved_home = home.resolve(strict=False)
    prefix_root = safe_existing_descendant(resolved_home, resolved_home / PREFIX_ROOT)
    mappings = compat_tool_mappings(config) if config is not None else {}
    prefixes: list[str] = []
    if prefix_root is not None:
        try:
            prefixes = sorted(item.name for item in prefix_root.iterdir() if item.is_dir() and item.name.isdigit())
        except OSError:
            pass
    return {
        "name": user.pw_name,
        "home": str(home),
        "steam_client": steam_client,
        "steam_clients": steam_clients,
        "in_group": group_name in user_groups(user),
        "steam_initialized": (
            bool(steam_localconfigs(steam_root))
            if steam_root is not None and "native" in steam_clients else False
        ),
        "library_registered": library_registered(config, library) if config is not None else False,
        "library_default": (
            library_is_default(steam_root, config, library)
            if steam_root is not None and config is not None else False
        ),
        "tool_installed": (
            (tool_dir / "proton").is_file() and (tool_dir / "base-proton.conf").is_file()
            if tool_dir is not None else False
        ),
        "personal_tool_selected": mappings.get("0") == TOOL_ID,
        "compat_tool_mappings": mappings,
        "steam_running": steam_is_running(user.pw_name),
        "prefixes": prefixes,
    }


def has_linux_launcher_or_windows_exe(game_dir: Path) -> tuple[bool, bool]:
    """Return launchable-Linux/Windows indicators using a bounded scan.

    Proton games commonly bundle ELF shared libraries.  Counting every ELF as
    a native build would make those games look safe even when Steam launches
    their Windows executable, so Linux evidence must be executable and not a
    conventional ``.so`` file.
    """
    linux_launcher = windows = False
    try:
        for root, directories, files in os.walk(game_dir):
            depth = len(Path(root).relative_to(game_dir).parts)
            if depth > 3:
                directories[:] = []
                continue
            for name in files:
                path = Path(root, name)
                lower_name = name.lower()
                if path.is_symlink():
                    continue
                if lower_name.endswith(".exe"):
                    windows = True
                try:
                    is_executable = bool(path.stat().st_mode & 0o111)
                    is_shared_object = lower_name.endswith(".so") or ".so." in lower_name
                    if is_executable and not is_shared_object:
                        with path.open("rb") as handle:
                            if handle.read(4) == b"\x7fELF":
                                linux_launcher = True
                except OSError:
                    continue
                if linux_launcher and windows:
                    return linux_launcher, windows
    except OSError:
        pass
    return linux_launcher, windows


def platform_kind(game_dir: Path) -> str:
    linux_launcher, windows = has_linux_launcher_or_windows_exe(game_dir)
    if linux_launcher and windows:
        return "mixed"
    if windows:
        return "windows"
    if linux_launcher:
        return "native"
    return "unknown"


def platform_label(kind: str) -> str:
    return {
        "mixed": "Native Linux + Windows files",
        "windows": "Windows / Proton",
        "native": "Native Linux",
        "unknown": "Unknown",
    }[kind]


def game_user_state(user: dict[str, object], appid: str, kind: str) -> str:
    """Classify current launch safety separately from whether a prefix exists."""
    if not user.get("library_registered"):
        return "library_not_added"

    mappings = user.get("compat_tool_mappings", {})
    if not isinstance(mappings, dict):
        return "unknown"
    override = mappings.get(appid)
    if override:
        if override != TOOL_ID:
            return "non_personal_override"
        if not user.get("tool_installed"):
            return "tool_missing"
        return "personal_created" if appid in user.get("prefixes", []) else "personal_ready"

    if kind in ("native", "mixed"):
        return "native"
    if kind == "windows":
        default_tool = mappings.get("0")
        if default_tool != TOOL_ID:
            return "non_personal_default" if default_tool else "personal_tool_not_selected"
        if not user.get("tool_installed"):
            return "tool_missing"
        return "personal_created" if appid in user.get("prefixes", []) else "personal_ready"
    return "unknown"


def available_protons(library: Path) -> list[str]:
    """Return official Proton directories already installed in this library."""
    common = library / "steamapps/common"
    try:
        choices = [
            item
            for item in common.iterdir()
            if not item.is_symlink()
            and item.is_dir()
            and item.name.startswith("Proton")
            and not (item / "proton").is_symlink()
            and (item / "proton").is_file()
            and (item / "proton").stat().st_mode & 0o111
        ]
    except OSError:
        return []
    choices.sort(key=lambda item: (item.name != "Proton - Experimental", item.name))
    return [str(item) for item in choices]


def proton_fixup_status(proton: Path) -> dict[str, object]:
    """Report whether Proton will attempt its owner-only mode restoration."""
    manifest = proton / "steampipe_fixups.json"
    marker = proton / "files/steampipe_fixups_mtime"
    if not manifest.is_file() or manifest.is_symlink():
        return {"path": str(proton), "supported": False, "pending": False}
    try:
        expected = str(os.path.getmtime(manifest))
        marker_status = marker.lstat()
        if not stat.S_ISREG(marker_status.st_mode):
            raise OSError("fixup marker is not a regular file")
        current = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        current = ""
    return {
        "path": str(proton),
        "supported": True,
        "pending": current != expected,
        "expected_mtime": expected,
        "marker_mtime": current,
    }


def games(library: Path, users: list[dict[str, object]]) -> list[dict[str, object]]:
    steamapps = library / "steamapps"
    result: list[dict[str, object]] = []
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        text = read_text(manifest)
        appid = vdf_value(text, "appid")
        if not appid.isdigit() or appid in TOOL_APP_IDS:
            continue
        name = vdf_value(text, "name") or appid
        install_dir = vdf_value(text, "installdir")
        common = steamapps / "common"
        game_dir = common / install_dir
        try:
            game_dir.resolve(strict=False).relative_to(common.resolve(strict=True))
        except (OSError, ValueError):
            game_dir = Path("/nonexistent")
        kind = platform_kind(game_dir)
        result.append({
            "appid": appid,
            "name": name,
            "platform": platform_label(kind),
            "platform_kind": kind,
            "prefix_users": [str(user["name"]) for user in users if appid in user["prefixes"]],
            "user_states": {
                str(user["name"]): game_user_state(user, appid, kind)
                for user in users
            },
            "shared_prefix_exists": (steamapps / "compatdata" / appid).is_dir(),
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read shared Steam library status as JSON.")
    parser.add_argument("--library", required=True, help="Shared Steam library root")
    parser.add_argument("--group", default="steamgames", help="Unix group allowed to use the library")
    args = parser.parse_args()
    library = Path(args.library)
    kind = library_kind(library)
    protons = available_protons(library)
    proton_fixups = [proton_fixup_status(Path(path)) for path in protons]
    accounts = normal_users()
    users = [user_status(user, library, args.group) for user in accounts]
    access_ready, access_problems = shared_access_report(library, accounts, args.group) if kind == "steam_library" else (False, [])
    libraries = sorted({
        path
        for user in accounts
        for steam_root in [safe_steam_root(Path(user.pw_dir))]
        if steam_root is not None
        for config in [safe_existing_descendant(steam_root, steam_root / "config")]
        if config is not None
        for path in registered_libraries(config)
        if (Path(path) / "steamapps").is_dir()
    })
    output = {
        "library": str(library),
        "library_exists": library.is_dir(),
        "library_kind": kind,
        "shared_access_ready": access_ready,
        "shared_access_problems": access_problems,
        "base_proton": protons[0] if protons else "",
        "base_proton_ready": bool(protons),
        "available_protons": protons,
        "proton_fixups": proton_fixups,
        "proton_fixups_pending": any(item["pending"] for item in proton_fixups),
        "users": users,
        "registered_libraries": libraries,
        "games": games(library, users) if library.is_dir() else [],
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
