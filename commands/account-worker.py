#!/usr/bin/python3
"""Mutate account-owned Steam files only after permanently dropping privileges.

Load trusted snapshot assets/modules while root can still read the private helper
snapshot. Each operation then forks, changes identity, and has no root privileges.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL_ID = "steam-shared-library-proton"


def load_storage():
    spec = importlib.util.spec_from_file_location("storage", HERE / "configure-steam-storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_asset(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError(f"Unsafe manager asset: {path}")
        return os.read(fd, 2 * 1024 * 1024 + 1)
    finally:
        os.close(fd)


def local_proton(root: Path, requested: str) -> Path:
    common = root / "steamapps/common"
    if common.resolve(strict=True) != common:
        raise ValueError("Local steamapps/common must not contain symbolic links")
    candidates = [common / Path(requested).name, common / "Proton - Experimental"]
    candidates += sorted(common.glob("Proton*"))
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        launcher = candidate / "proton"
        manifest = candidate / "toolmanifest.vdf"
        if (not launcher.is_symlink() and launcher.is_file() and os.access(launcher, os.X_OK)
                and manifest.is_file() and manifest.resolve().is_relative_to(root)):
            return candidate
    raise ValueError(f"Install an official Proton in this person's local library: {root}")


def safe_directory(root: Path, path: Path) -> Path:
    path.relative_to(root)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing symlinked manager directory: {current}")
        current.mkdir(mode=0o700, exist_ok=True)
        if not current.is_dir():
            raise ValueError(f"Not a directory: {current}")
    return path


def install(user, args, storage, assets, dry_run):
    plan = storage.prepare_user(user.pw_name, None)
    root = storage.safe_steam_root(Path(user.pw_dir))
    proton = local_proton(root, args.base_proton)
    manifest = storage.read_regular_text(storage.safe_file(root, proton / "toolmanifest.vdf"))
    parent = root / "compatibilitytools.d"
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError(f"Unsafe compatibility tool directory: {parent}")
    tool = parent / TOOL_ID
    if tool.is_symlink():
        raise ValueError(f"Refusing symlinked tool: {tool}")
    if dry_run:
        print(f"{user.pw_name}: preflight passed; local Proton: {proton}")
        return
    safe_directory(root, parent)
    state = safe_directory(root, root / ".steam-shared-library-manager/backups")
    staged = Path(tempfile.mkdtemp(prefix=".manager-stage-", dir=parent))
    backup = state / f"wrapper-{uuid.uuid4().hex}"
    swapped = False
    try:
        contents = {"proton": assets["proton"], "compatibilitytool.vdf": assets["registration"],
                    "toolmanifest.vdf": manifest.encode(), "base-proton.conf": f"BASE_PROTON={proton}\n".encode()}
        for name, content in contents.items():
            path = staged / name
            path.write_bytes(content)
            path.chmod(0o755 if name == "proton" else 0o644)
        storage.apply_user(plan)
        if tool.exists():
            tool.rename(backup)
        try:
            staged.rename(tool)
            swapped = True
        except BaseException:
            if backup.exists():
                backup.rename(tool)
            raise
        print(f"{user.pw_name}: installed local Proton wrapper; previous wrapper: {backup if backup.exists() else 'none'}")
    finally:
        if not swapped and staged.exists():
            shutil.rmtree(staged)


def remove_managed_mappings(text, storage):
    edits = []
    for key, kind, start, end in storage.top_level_items(text, "CompatToolMapping"):
        if kind != "object":
            continue
        body = text[start:end]
        if any(f'"{name}"' in body for name in (TOOL_ID, "steam-per-user-proton")):
            key_start = text.rfind('"' + key + '"', 0, start)
            edits.append((key_start, end))
    for start, end in reversed(edits):
        text = text[:start] + text[end:]
    return text


def uninstall(user, args, storage, dry_run):
    root = storage.safe_steam_root(Path(user.pw_dir))
    if subprocess.run(["pgrep", "-u", str(user.pw_uid), "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0:
        raise ValueError("Close Steam before uninstalling")
    config = storage.safe_file(root, root / "config/config.vdf")
    original = storage.read_regular_text(config)
    updated = remove_managed_mappings(original, storage)
    paths = [root / "compatibilitytools.d" / name for name in (TOOL_ID, "steam-per-user-proton")]
    if args.remove_prefixes:
        paths += [Path(user.pw_dir) / ".local/share" / name for name in (TOOL_ID, "steam-per-user-proton")]
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(Path(user.pw_dir).resolve()):
            raise ValueError(f"Refusing unsafe removal: {path}")
    if dry_run:
        return
    backup = storage.atomic_update(config, original, updated, user)
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
    print(f"{user.pw_name}: removed wrapper and its Steam mappings; configuration backup: {backup}")


def run_as_user(user, operation):
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            if os.geteuid() == 0:
                os.initgroups(user.pw_name, user.pw_gid)
                os.setgid(user.pw_gid)
                os.setuid(user.pw_uid)
            if os.geteuid() != user.pw_uid or user.pw_uid < 1000:
                raise ValueError("Account operation requires a normal account's own identity")
            os.environ.clear()
            os.environ.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name,
                              PATH="/usr/bin:/bin", LANG="C.UTF-8")
            os.chdir(user.pw_dir)
            os.umask(0o077)
            operation()
        except (OSError, ValueError, KeyError) as error:
            print(f"{user.pw_name}: {error}", file=sys.stderr)
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise ValueError(f"Stopped for {user.pw_name}; earlier accounts may be complete. Safely rerun after correcting the error.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("install", "storage", "uninstall"), required=True)
    parser.add_argument("--base-proton", default="Proton - Experimental")
    parser.add_argument("--library", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove-prefixes", action="store_true")
    parser.add_argument("users", nargs="+")
    args = parser.parse_args()
    storage = load_storage()
    if args.library is not None:
        spec = importlib.util.spec_from_file_location("library_backend", HERE / "library-backend.py")
        library_backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(library_backend)
        args.library = library_backend.validate_library(args.library)
    assets = {"proton": read_asset(HERE / "proton"),
              "registration": read_asset(HERE.parent / "steam-shared-library-proton.vdf")}
    users = [pwd.getpwnam(name) for name in args.users]
    def execute(user, dry):
        if args.action == "install":
            install(user, args, storage, assets, dry)
        elif args.action == "uninstall":
            uninstall(user, args, storage, dry)
        else:
            plan = storage.prepare_user(user.pw_name, args.library)
            if not dry:
                storage.apply_user(plan)
    # Validate every account under its actual identity before any account writes.
    for user in users:
        run_as_user(user, lambda: execute(user, True))
    if not args.dry_run:
        for user in users:
            run_as_user(user, lambda: execute(user, False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
