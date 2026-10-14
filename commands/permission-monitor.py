#!/usr/bin/python3
"""Reconcile game permissions after completed Steam manifest updates.

Installed as root-owned code. Only validated library/game paths are processed;
no downloaded executable is ever launched. Runtime/Proton tools are excluded.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import pwd
import grp
import time
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

BACKEND = load("library-backend")
VDF = load("vdf")
CONFIG = Path("/etc/steam-shared-library-manager/libraries.json")


def normal_group_users(group):
    gid = grp.getgrnam(group).gr_gid
    users = []
    for user in pwd.getpwall():
        if user.pw_uid >= 1000 and not user.pw_shell.endswith(("nologin", "false")):
            if gid in os.getgrouplist(user.pw_name, user.pw_gid):
                users.append(user.pw_uid)
    return gid, tuple(sorted(users))


def reconcile(library, group, known, previous_users=None):
    library = BACKEND.validate_library(Path(library))
    gid, users = normal_group_users(group)
    root_fd = BACKEND.open_directory(library)
    changed = 0
    try:
        if previous_users is not None and set(previous_users) != set(users):
            BACKEND.repair_tree(root_fd, gid, users, remove_users=tuple(set(previous_users) - set(users)))
        apps_fd = os.open("steamapps", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            common_fd = os.open("common", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=apps_fd)
        except FileNotFoundError:
            os.close(apps_fd)
            return 0
        try:
            for name in os.listdir(apps_fd):
                if not name.startswith("appmanifest_") or not name.endswith(".acf"):
                    continue
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=apps_fd)
                try:
                    info = os.fstat(fd)
                    if info.st_size > 1024 * 1024:
                        continue
                    text = os.read(fd, 1024 * 1024 + 1).decode()
                finally:
                    os.close(fd)
                entry = VDF.object_entry(text, "AppState")
                appid = VDF.scalar(entry, "appid")
                directory = VDF.scalar(entry, "installdir")
                title = VDF.scalar(entry, "name")
                flags = VDF.scalar(entry, "StateFlags")
                if not appid.isdigit() or name != f"appmanifest_{appid}.acf" or Path(directory).name != directory or directory in ("", ".", ".."):
                    continue
                if title.lower().startswith("steam linux runtime") or BACKEND.support_directory(directory) or appid in ("228980", "1161040", "1826330"):
                    continue
                if not flags.isdigit() or not int(flags) & 4 or int(flags) & (256 | 512 | 1024):
                    continue
                fingerprint = (VDF.scalar(entry, "buildid"), VDF.scalar(entry, "LastUpdated"), directory, users)
                key = (str(library), appid)
                if known.get(key) == fingerprint:
                    continue
                game_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=common_fd)
                try:
                    BACKEND.repair_tree(game_fd, gid, users)
                finally:
                    os.close(game_fd)
                known[key] = fingerprint
                changed += 1
                print(f"Restored game permissions: {library} / {directory}", flush=True)
        finally:
            os.close(common_fd)
            os.close(apps_fd)
    finally:
        os.close(root_fd)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    known = {}
    state_path = Path("/var/lib/steam-shared-library-manager/memberships.json")
    if state_path.is_symlink():
        raise ValueError("Unsafe monitor state")
    memberships = json.loads(state_path.read_text()) if state_path.exists() else {}
    while True:
        try:
            if args.config.is_symlink() or args.config.stat().st_size > 1024 * 1024:
                raise ValueError("Unsafe monitor configuration")
            config = json.loads(args.config.read_text())
            for item in config.get("libraries", []):
                try:
                    previous = memberships.get(item["path"], item.get("managed_users"))
                    reconcile(item["path"], item["group"], known, previous)
                    memberships[item["path"]] = list(normal_group_users(item["group"])[1])
                except (OSError, ValueError, KeyError) as error:
                    print(f"Permission monitor: {item.get('path')}: {error}", flush=True)
        except (OSError, ValueError) as error:
            print(f"Permission monitor: {error}", flush=True)
        if os.geteuid() == 0 and state_path.parent.exists():
            fd, temporary = tempfile.mkstemp(prefix=".memberships-", dir=state_path.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(memberships, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, state_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
