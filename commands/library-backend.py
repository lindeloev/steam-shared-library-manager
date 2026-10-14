#!/usr/bin/python3
"""Narrow shared-library validation and descriptor-based permission repair."""
from __future__ import annotations
import argparse
import ctypes
import ctypes.util
import grp
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path

PROTECTED = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var")
ROOTS = ("/", "/home", "/srv", "/mnt", "/media", "/tmp", "/opt")


def validate_library(path: Path, *, existing=True):
    if not path.is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise ValueError("Library path must be absolute without control characters")
    lexical = Path(os.path.abspath(path))
    if path.resolve(strict=False) != lexical:
        raise ValueError("Refusing symbolic links in shared library path")
    if str(lexical) in ROOTS or any(lexical == Path(p) or Path(p) in lexical.parents for p in PROTECTED):
        raise ValueError(f"Choose dedicated game storage; protected location: {lexical}")
    for user in pwd.getpwall():
        if user.pw_uid < 1000 or user.pw_shell.endswith(("false", "nologin")):
            continue
        home = Path(user.pw_dir).resolve()
        if lexical == home or lexical in home.parents:
            raise ValueError("Shared storage cannot be an account home or its ancestor")
        for private in (".ssh", ".config", ".cache", ".local", ".steam"):
            sensitive = home / private
            if lexical == sensitive or sensitive in lexical.parents:
                raise ValueError("Shared storage cannot be inside private account data")
        steam = (home / ".steam/root").resolve()
        if lexical == steam or steam in lexical.parents or lexical in steam.parents:
            raise ValueError("Shared storage cannot overlap a person's primary Steam library")
    if existing:
        fd = open_directory(lexical)
        try:
            names = set(os.listdir(fd))
            unexpected = names - {"steamapps", "libraryfolder.vdf", "lost+found", ".steam-shared-library-manager"}
            if unexpected:
                raise ValueError("Folder contains unrelated data: " + ", ".join(sorted(unexpected)[:5]))
            apps = os.open("steamapps", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(apps)
        finally:
            os.close(fd)
    return lexical


def open_directory(path):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class ACL:
    """Modify ACLs on pinned objects, retaining unrelated named entries."""
    def __init__(self):
        self.lib = ctypes.CDLL(ctypes.util.find_library("acl") or "libacl.so.1", use_errno=True)
        signatures = {
            "acl_get_fd": (ctypes.c_void_p, [ctypes.c_int]),
            "acl_get_file": (ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_int]),
            "acl_from_text": (ctypes.c_void_p, [ctypes.c_char_p]),
            "acl_entries": (ctypes.c_int, [ctypes.c_void_p]),
            "acl_get_entry": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]),
            "acl_get_tag_type": (ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]),
            "acl_get_qualifier": (ctypes.c_void_p, [ctypes.c_void_p]),
            "acl_delete_entry": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
            "acl_create_entry": (ctypes.c_int, [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]),
            "acl_set_tag_type": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "acl_set_qualifier": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
            "acl_get_permset": (ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
            "acl_clear_perms": (ctypes.c_int, [ctypes.c_void_p]),
            "acl_add_perm": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "acl_calc_mask": (ctypes.c_int, [ctypes.POINTER(ctypes.c_void_p)]),
            "acl_set_fd": (ctypes.c_int, [ctypes.c_int, ctypes.c_void_p]),
            "acl_set_file": (ctypes.c_int, [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]),
            "acl_free": (ctypes.c_int, [ctypes.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            fn = getattr(self.lib, name)
            fn.restype, fn.argtypes = result, arguments

    def check(self, result):
        if result < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        return result

    def grant(self, fd, group_id, users=(), *, default=False, remove_users=()):
        path = f"/proc/self/fd/{fd}".encode()
        raw = self.lib.acl_get_file(path, 0x4000) if default else self.lib.acl_get_fd(fd)
        if not raw:
            raise OSError(ctypes.get_errno(), "Cannot read filesystem ACL")
        acl = ctypes.c_void_p(raw)
        try:
            if self.lib.acl_entries(acl) == 0:
                self.lib.acl_free(acl)
                other_mode = os.fstat(fd).st_mode & 7
                other = "".join(c if other_mode & bit else "-" for c, bit in (("r", 4), ("w", 2), ("x", 1)))
                acl = ctypes.c_void_p(self.lib.acl_from_text(f"user::rwx,group::rwx,other::{other}".encode()))
                if not acl:
                    raise ValueError("Cannot build default ACL")
            for identifier in remove_users:
                entry = ctypes.c_void_p()
                position = 0
                while self.lib.acl_get_entry(acl, position, ctypes.byref(entry)) == 1:
                    position = 1
                    tag = ctypes.c_int()
                    self.check(self.lib.acl_get_tag_type(entry, ctypes.byref(tag)))
                    if tag.value != 2:
                        continue
                    qualifier = self.lib.acl_get_qualifier(entry)
                    try:
                        matches = qualifier and ctypes.cast(qualifier, ctypes.POINTER(ctypes.c_uint)).contents.value == identifier
                    finally:
                        if qualifier:
                            self.lib.acl_free(qualifier)
                    if matches:
                        self.check(self.lib.acl_delete_entry(acl, entry))
                        break
            targets = [(8, group_id), *[(2, uid) for uid in users]]
            for tag, identifier in targets:
                entry = ctypes.c_void_p()
                found = False
                position = 0
                while self.lib.acl_get_entry(acl, position, ctypes.byref(entry)) == 1:
                    position = 1
                    actual = ctypes.c_int()
                    self.check(self.lib.acl_get_tag_type(entry, ctypes.byref(actual)))
                    if actual.value != tag:
                        continue
                    qualifier = self.lib.acl_get_qualifier(entry)
                    try:
                        if qualifier and ctypes.cast(qualifier, ctypes.POINTER(ctypes.c_uint)).contents.value == identifier:
                            found = True
                            break
                    finally:
                        if qualifier:
                            self.lib.acl_free(qualifier)
                if not found:
                    self.check(self.lib.acl_create_entry(ctypes.byref(acl), ctypes.byref(entry)))
                    self.check(self.lib.acl_set_tag_type(entry, tag))
                    value = ctypes.c_uint(identifier)
                    self.check(self.lib.acl_set_qualifier(entry, ctypes.byref(value)))
                perms = ctypes.c_void_p()
                self.check(self.lib.acl_get_permset(entry, ctypes.byref(perms)))
                self.check(self.lib.acl_clear_perms(perms))
                self.check(self.lib.acl_add_perm(perms, 4 | 2))
                mode = os.fstat(fd).st_mode
                if default or stat.S_ISDIR(mode) or mode & 0o111:
                    self.check(self.lib.acl_add_perm(perms, 1))
            self.check(self.lib.acl_calc_mask(ctypes.byref(acl)))
            self.check(self.lib.acl_set_file(path, 0x4000, acl) if default else self.lib.acl_set_fd(fd, acl))
        finally:
            self.lib.acl_free(acl)


def support_directory(name):
    return name.startswith(("SteamLinuxRuntime", "Steamworks Shared")) or re.match(r"^Proton(?:$|[ -]+(?:\d|Experimental\b|Hotfix\b|Next\b|BattlEye\b|EasyAntiCheat\b))", name, re.I) is not None


def group_users(group_id):
    return tuple(user.pw_uid for user in pwd.getpwall()
                 if user.pw_uid >= 1000 and not user.pw_shell.endswith(("nologin", "false"))
                 and group_id in os.getgrouplist(user.pw_name, user.pw_gid))


def prepare_new(path, group_id, users):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.listdir(fd):
            raise ValueError("New library must be empty; use repair for an existing library")
        os.mkdir("steamapps", 0o2775, dir_fd=fd)
        return repair_tree(fd, group_id, users)
    finally:
        os.close(fd)


def repair_tree(fd, group_id, users=(), *, device=None, acl=None, relative="", remove_users=()):

    acl = acl or ACL()
    info = os.fstat(fd)
    device = info.st_dev if device is None else device
    if info.st_dev != device:
        raise ValueError("Refusing to traverse a nested filesystem")
    if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError("Refusing special files or multiply linked game files")
    if os.geteuid() == 0:
        if stat.S_ISREG(info.st_mode) and info.st_uid < 1000:
            raise ValueError("Refusing to make a root/service-owned file shared")
        os.fchown(fd, -1, group_id)
    mode = stat.S_IMODE(info.st_mode) & ~(stat.S_ISUID | stat.S_ISGID)
    os.fchmod(fd, mode | (0o2070 if stat.S_ISDIR(info.st_mode) else 0o060))
    acl.grant(fd, group_id, users, remove_users=remove_users)
    count = 1
    if stat.S_ISDIR(info.st_mode):
        acl.grant(fd, group_id, users, default=True, remove_users=remove_users)
        for name in os.listdir(fd):
            if name in ("lost+found", ".steam-shared-library-manager") or (relative == "steamapps/common" and support_directory(name)):
                continue
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(item.st_mode):
                continue
            if not stat.S_ISDIR(item.st_mode) and (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1):
                raise ValueError(f"Refusing special or multiply linked file: {name}")
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                count += repair_tree(child, group_id, users, device=device, acl=acl, relative=(relative + "/" + name).lstrip("/"), remove_users=remove_users)
            finally:
                os.close(child)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--group", default="steamgames")
    parser.add_argument("--validate-new", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--users", nargs="*", default=[])
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    library = validate_library(args.library, existing=not (args.validate_new or args.setup))
    if args.validate_only or args.validate_new:
        print(library)
        return
    if os.geteuid() != 0:
        raise ValueError("Run shared permissions repair through sudo or pkexec")
    group_id = grp.getgrnam(args.group).gr_gid
    users = set(group_users(group_id))
    for name in args.users:
        user = pwd.getpwnam(name)
        if user.pw_uid < 1000 or user.pw_shell.endswith(("nologin", "false")):
            raise ValueError("Only normal accounts can receive shared access")
        users.add(user.pw_uid)
    if args.setup:
        print(f"Prepared shared game storage ({prepare_new(library, group_id, tuple(users))} objects); current sessions have direct ACL access")
        return
    fd = open_directory(library)
    try:
        print(f"Repaired {repair_tree(fd, group_id, tuple(users))} filesystem objects in dedicated game storage: {library}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
