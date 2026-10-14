#!/usr/bin/python3
"""Mutate account-owned Steam files only after permanently dropping privileges.

Load trusted snapshot assets/modules while root can still read the private helper
snapshot. Each operation then forks, changes identity, and has no root privileges.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from types import SimpleNamespace
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

def load_lock():
    spec = importlib.util.spec_from_file_location("operation_lock", HERE / "operation-lock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

LOCK = load_lock()



def load_storage():
    spec = importlib.util.spec_from_file_location("storage", HERE / "configure-steam-storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_vdf():
    spec = importlib.util.spec_from_file_location("trusted_vdf", HERE / "vdf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

VDF = load_vdf()


def settings_path(root):
    return root / ".steam-shared-library-manager/settings.json"


def read_settings(root):
    path = settings_path(root)
    if not path.exists():
        return {"schema": 1, "previous_mappings": {}}
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Unsafe settings recovery record")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("previous_mappings"), dict):
        raise ValueError("Unrecognized settings recovery record")
    if any(not isinstance(key, str) or not key.isdigit() or not isinstance(value, str) for key, value in data["previous_mappings"].items()):
        raise ValueError("Invalid previous compatibility selections")
    if "prefix_root" in data and not isinstance(data["prefix_root"], str):
        raise ValueError("Invalid saved prefix location")
    return data


def save_settings(root, data):
    parent = safe_directory(root, settings_path(root).parent)
    fd, filename = tempfile.mkstemp(prefix=".settings-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, settings_path(root))
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def apply_mappings(user, root, replacements, storage, dry_run=False):
    path = storage.safe_file(root, root / "config/config.vdf")
    original = storage.read_regular_text(path)
    existing = VDF.mappings(original)
    updated = original
    recovery = read_settings(root)
    for appid, name in replacements.items():
        previous = existing.get(appid, {})
        if previous.get("name") == name:
            continue
        recovery["previous_mappings"].setdefault(appid, previous.get("source", ""))
        updated = VDF.set_mapping(updated, appid, name)
    if not dry_run and updated != original:
        save_settings(root, recovery)  # Save recovery before touching Steam config.
        backup = storage.atomic_update(path, original, updated, user)
        print(f"{user.pw_name}: configured compatibility selections; backup: {backup}")


def load_migration():
    spec = importlib.util.spec_from_file_location("tool_migration", HERE / "tool-migration.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

MIGRATION = load_migration()


def describe_setup(user, args, storage):
    root = storage.safe_steam_root(Path(user.pw_dir))
    result = {"account": user.pw_name, "local_root": str(root), "shared_library": str(args.library),
              "prefix_root": str(choose_prefix(user, root, args)), "changes": ["Register shared game storage", "Select local remembered install destination", "Install personal wrapper", "Select wrapper by default; preserve per-game overrides"]}
    try:
        result["tool_directory_repair"] = inspect_tool_directory(root, user)
        proton = local_proton(root, args.base_proton)
        source = MANIFEST.generate(storage.read_regular_text(storage.safe_file(root, proton / "toolmanifest.vdf")))
        runtime = VDF.scalar(VDF.object_entry(source, "manifest"), "require_tool_appid")
        result.update(proton=str(proton), runtime_appid=runtime,
                      runtime_local=(not runtime or (root / f"steamapps/appmanifest_{runtime}.acf").is_file()))
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    print(json.dumps(result))


def migrate_tools(user, args, storage, dry_run):
    root = storage.safe_steam_root(Path(user.pw_dir))
    items = MIGRATION.plan(root, args.library, VDF)
    for item in items:
        print(f"{user.pw_name}: {item['operation']}: {item['name']} ({item['bytes'] / 2**30:.2f} GiB); {item['reason']}")
    if dry_run:
        if not args.dry_run and not args.replace_conflicts and any(x["operation"] == "conflict" for x in items):
            raise ValueError("Local destination conflicts require explicit backup-and-replace")
        return
    plan = storage.prepare_user(user.pw_name, args.library)
    state = safe_directory(root, root / ".steam-shared-library-manager/backups")
    for item in items:
        MIGRATION.copy_one(item, root, state, args.replace_conflicts)
    storage.apply_user(plan)
    recovery = read_settings(root)
    recovery["tool_copies"] = [{"appid": x["appid"], "directory": x["directory"], "digest": x["digest"]} for x in items]
    save_settings(root, recovery)


def verify_tools(user, storage, baseline):
    root = storage.safe_steam_root(Path(user.pw_dir))
    for item in baseline:
        target = root / "steamapps/common" / item["directory"]
        manifest = root / "steamapps" / item["manifest"].name
        if target.is_symlink() or manifest.is_symlink() or not manifest.is_file():
            raise ValueError("Copied tool is missing or symlinked")
        if MIGRATION.tree_digest(target)[0] != item["digest"]:
            raise ValueError(f"Local verification failed for {item['name']}; shared sources remain")
        entry = VDF.object_entry(manifest.read_text(), "AppState")
        if VDF.scalar(entry, "appid") != item["appid"]:
            raise ValueError("Local manifest no longer matches the verified copy")
    print(f"{user.pw_name}: all local tool copies verified")


def load_transactions():
    spec = importlib.util.spec_from_file_location("transactions", HERE / "transactions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

TRANSACTIONS = load_transactions()


def load_manifest():
    spec = importlib.util.spec_from_file_location("manifest", HERE / "manifest-helper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

MANIFEST = load_manifest()


def choose_prefix(user, root, args):
    saved = read_settings(root)
    prefix = Path(saved.get("prefix_root", str(Path(user.pw_dir) / ".local/share" / TOOL_ID)))
    if getattr(args, "data_home", None):
        prefix = Path(args.data_home) / TOOL_ID
    legacy = Path(user.pw_dir) / ".local/share/steam-per-user-proton"
    if not prefix.exists() and legacy.is_dir() and not legacy.is_symlink():
        prefix = legacy
    if not prefix.is_absolute() or prefix.is_symlink() or any(ord(c) < 32 for c in str(prefix)):
        raise ValueError("Prefix root must be an absolute private directory without a symlink")
    if prefix.exists() and (not prefix.is_dir() or prefix.stat().st_uid != user.pw_uid):
        raise ValueError("Prefix root must belong to this account")
    return prefix.resolve()


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
        if (not launcher.is_symlink() and launcher.is_file() and launcher.stat().st_uid == os.geteuid() and launcher.stat().st_mode & 0o100
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


def inspect_tool_directory(root, user, repair=False):
    """Repair only the root-owned container left by older privileged installs.

    Pin every directory with no-follow opens. Never recursively change ownership
    of account files or third-party compatibility tools.
    """
    if user.pw_uid < 1000:
        raise ValueError("Compatibility tools require a normal login account")
    flags = (os.O_RDONLY if repair else os.O_PATH) | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in root.relative_to("/").parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != user.pw_uid:
            raise ValueError(f"Steam directory must belong to {user.pw_name}: {root}")
        try:
            child = os.open("compatibilitytools.d", flags, dir_fd=fd)
        except FileNotFoundError:
            return False
        os.close(fd)
        fd = child
        info = os.fstat(fd)
        if info.st_uid not in (0, user.pw_uid):
            raise ValueError(f"Compatibility tool directory belongs to another account: {root / 'compatibilitytools.d'}")
        needed = info.st_uid == 0 or info.st_mode & 0o700 != 0o700
        if repair and needed:
            if info.st_uid == 0:
                os.fchown(fd, user.pw_uid, user.pw_gid)
            os.fchmod(fd, stat.S_IMODE(info.st_mode) | 0o700)
            print(f"{user.pw_name}: repaired compatibility tool container ownership/access (contents unchanged)")
        return needed
    finally:
        os.close(fd)


def install(user, args, storage, assets, dry_run):
    plan = storage.prepare_user(user.pw_name, None)
    root = storage.safe_steam_root(Path(user.pw_dir))
    proton = local_proton(root, args.base_proton)
    manifest = MANIFEST.generate(storage.read_regular_text(storage.safe_file(root, proton / "toolmanifest.vdf")))
    prefix = choose_prefix(user, root, args)
    parent = root / "compatibilitytools.d"
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError(f"Unsafe compatibility tool directory: {parent}")
    tool_id = getattr(args, "tool_id", TOOL_ID)
    tool = parent / tool_id
    if tool_id == TOOL_ID:
        apply_mappings(user, root, {"0": TOOL_ID}, storage, dry_run=True)
    if os.access(parent, os.X_OK) and tool.is_symlink():
        raise ValueError(f"Refusing symlinked tool: {tool}")
    if dry_run:
        if inspect_tool_directory(root, user):
            print(f"{user.pw_name}: compatibility tool container ownership/access will be repaired")
        print(f"{user.pw_name}: preflight passed; local Proton: {proton}")
        return
    safe_directory(root, parent)
    state = safe_directory(root, root / ".steam-shared-library-manager/backups")
    staged = Path(tempfile.mkdtemp(prefix=".manager-stage-", dir=parent))
    backup = state / f"wrapper-{uuid.uuid4().hex}"
    receipt = TRANSACTIONS.start(root, "wrapper", {"destination": str(tool), "staging": str(staged), "backup": str(backup)})
    swapped = False
    try:
        registration = assets["registration"].decode().replace(TOOL_ID, tool_id)
        if tool_id != TOOL_ID:
            registration = VDF.set_in_object(registration, tool_id, "display_name", "Personal settings – " + proton.name)
        contents = {"proton": assets["proton"], "compatibilitytool.vdf": registration.encode(),
                    "toolmanifest.vdf": manifest.encode(), "base-proton.conf": f"BASE_PROTON={proton}\nPREFIX_ROOT={prefix}\n".encode(),
                    "manifest-helper.py": assets["manifest-helper"], "vdf.py": assets["vdf"]}
        for name, content in contents.items():
            path = staged / name
            path.write_bytes(content)
            path.chmod(0o755 if name == "proton" else 0o644)
        recovery = read_settings(root)
        recovery["prefix_root"] = str(prefix)
        save_settings(root, recovery)
        if prefix.exists():
            prefix.chmod(0o700)
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
        if tool_id == TOOL_ID:
            apply_mappings(user, root, {"0": TOOL_ID}, storage)
        TRANSACTIONS.finish(receipt, "complete")
        print(f"{user.pw_name}: installed local Proton wrapper; previous wrapper: {backup if backup.exists() else 'none'}")
    except BaseException as error:
        TRANSACTIONS.finish(receipt, "failed", str(error))
        raise
    finally:
        if not swapped and staged.exists():
            shutil.rmtree(staged)


def remove_managed_mappings(text, storage, previous=None):
    for appid, mapping in VDF.mappings(text).items():
        if VDF.managed(mapping["name"]):
            text = VDF.set_mapping(text, appid, "", source=(previous or {}).get(appid, ""))
    return text


def proton_for_mapping(root, name, storage):
    for candidate in sorted((root / "steamapps/common").glob("Proton*")):
        if candidate.is_symlink():
            continue
        version = re.fullmatch(r"Proton (\d+)\.(\d+)(?:.*)", candidate.name)
        tool_name = {"Proton - Experimental": "proton_experimental", "Proton Hotfix": "proton_hotfix"}.get(candidate.name)
        if version:
            tool_name = "proton_" + version[1] + version[2]
        if tool_name == name:
            resolved = local_proton(root, str(candidate))
            if resolved == candidate:
                return candidate
    raise ValueError(f"Pinned {name} is not installed locally. Copy/install that version first; no selection was changed.")


def migrate(user, args, storage, assets, dry_run):
    root = storage.safe_steam_root(Path(user.pw_dir))
    storage.prepare_user(user.pw_name, None)  # Enforces idle Steam.
    text = storage.read_regular_text(storage.safe_file(root, root / "config/config.vdf"))
    selections = {}
    versions = {}
    for appid, mapping in VDF.mappings(text).items():
        name = mapping["name"]
        if appid == "0" or not re.fullmatch(r"proton_(?:experimental|hotfix|\d+)", name):
            continue
        proton = proton_for_mapping(root, name, storage)
        versions[name] = proton
        selections[appid] = TOOL_ID + "-" + name
    for name, proton in versions.items():
        variant = SimpleNamespace(base_proton=str(proton), tool_id=TOOL_ID + "-" + name)
        install(user, variant, storage, assets, dry_run)
    if not dry_run:
        apply_mappings(user, root, selections, storage)
    print(f"{user.pw_name}: {'would migrate' if dry_run else 'migrated'} {len(selections)} pinned game selections, preserving Proton versions")


def uninstall(user, args, storage, dry_run):
    root = storage.safe_steam_root(Path(user.pw_dir))
    if subprocess.run(["pgrep", "-u", str(user.pw_uid), "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0:
        raise ValueError("Close Steam before uninstalling")
    config = storage.safe_file(root, root / "config/config.vdf")
    original = storage.read_regular_text(config)
    updated = remove_managed_mappings(original, storage, read_settings(root)["previous_mappings"])
    paths = [path for path in (root / "compatibilitytools.d").glob("*") if VDF.managed(path.name)]
    if args.remove_prefixes:
        paths += [Path(read_settings(root).get("prefix_root", str(Path(user.pw_dir) / ".local/share" / TOOL_ID))), Path(user.pw_dir) / ".local/share/steam-per-user-proton"]
    for path in paths:
        if path.is_symlink() or (path.exists() and path.stat().st_uid != user.pw_uid):
            raise ValueError(f"Refusing unsafe removal: {path}")
    if dry_run:
        return
    backup = storage.atomic_update(config, original, updated, user)
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
    print(f"{user.pw_name}: removed wrapper and its Steam mappings; configuration backup: {backup}")


def run_as_user(user, operation):
    if user.pw_uid < 1000 or user.pw_shell.endswith(("nologin", "false")):
        raise ValueError("Only normal login accounts can be configured")
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
            # Modules are already loaded before dropping root; no reads of the
            # root-private helper checkout are needed under the account identity.
            root = STORAGE.safe_steam_root(Path(user.pw_dir))
            state = safe_directory(root, root / ".steam-shared-library-manager")
            with LOCK.account_lock(state / "operation.lock"):
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


STORAGE = load_storage()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("install", "storage", "uninstall", "migrate", "tools", "plan"), required=True)
    parser.add_argument("--base-proton", default="Proton - Experimental")
    parser.add_argument("--library", type=Path)
    parser.add_argument("--data-home", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove-prefixes", action="store_true")
    parser.add_argument("--replace-conflicts", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("users", nargs="+")
    args = parser.parse_args()
    if args.action == "plan":
        args.dry_run = True
    storage = STORAGE
    if args.library is not None:
        spec = importlib.util.spec_from_file_location("library_backend", HERE / "library-backend.py")
        library_backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(library_backend)
        args.library = library_backend.validate_library(args.library)
    assets = {"proton": read_asset(HERE / "proton"),
              "registration": read_asset(HERE.parent / "steam-shared-library-proton.vdf"),
              "manifest-helper": read_asset(HERE / "manifest-helper.py"), "vdf": read_asset(HERE / "vdf.py")}
    users = [pwd.getpwnam(name) for name in args.users]
    baseline = None
    if args.action == "tools":
        if args.library is None:
            raise ValueError("Tool migration needs --library")
        if not args.dry_run and subprocess.run(["pgrep", "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0:
            raise ValueError("Close Steam for all accounts before copying or quarantining tools")
        if args.cleanup:
            selected = {user.pw_name for user in users}
            for account in pwd.getpwall():
                if account.pw_uid < 1000 or account.pw_name in selected:
                    continue
                try:
                    root = storage.safe_steam_root(Path(account.pw_dir))
                    text = storage.read_regular_text(storage.safe_file(root, root / "config/libraryfolders.vdf"))
                except (OSError, ValueError):
                    continue
                if str(args.library) in storage.library_indexes(text):
                    raise ValueError(f"{account.pw_name} also uses this library; include them before removing shared tools")
        baseline = MIGRATION.sources(args.library, VDF)
    def execute(user, dry):
        root = storage.safe_steam_root(Path(user.pw_dir))
        if not dry:
            TRANSACTIONS.recover(root, MIGRATION.tree_digest)
        receipt = TRANSACTIONS.start(root, "account-" + args.action, {"account": user.pw_name}) if not dry else None
        try:
            execute_action(user, dry)
            if receipt:
                TRANSACTIONS.finish(receipt, "complete")
        except BaseException as error:
            if receipt:
                TRANSACTIONS.finish(receipt, "failed", str(error))
            raise
    def execute_action(user, dry):
        if args.action == "plan":
            describe_setup(user, args, storage)
        elif args.action == "tools":
            migrate_tools(user, args, storage, dry)
        elif args.action == "install":
            install(user, args, storage, assets, dry)
        elif args.action == "migrate":
            migrate(user, args, storage, assets, dry)
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
        if args.action in ("install", "migrate"):
            # Validate every container before repairing any. Package and config
            # writes still happen only after dropping to the target identity.
            for user in users:
                inspect_tool_directory(storage.safe_steam_root(Path(user.pw_dir)), user)
            for user in users:
                inspect_tool_directory(storage.safe_steam_root(Path(user.pw_dir)), user, repair=True)
        for user in users:
            run_as_user(user, lambda: execute(user, False))
        if args.action == "tools":
            for user in users:
                run_as_user(user, lambda: verify_tools(user, storage, baseline))
            if args.cleanup and baseline:
                run_as_user(users[0], lambda: print("Shared sources retained in quarantine:", MIGRATION.quarantine_sources(args.library, baseline)))


if __name__ == "__main__":
    try:
        if "--dry-run" in sys.argv or ("--action" in sys.argv and sys.argv[sys.argv.index("--action") + 1] == "plan"):
            main()
        else:
            with LOCK.admin_lock():
                main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
