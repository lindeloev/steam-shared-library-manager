#!/usr/bin/python3
"""Serialize admin changes and account mutations across GUI/CLI instances."""
from contextlib import contextmanager
import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path

LOCK_ROOT = Path("/run/lock/steam-shared-library-manager")


def inherited_fds():
    value = os.environ.get("SSLM_LOCK_FD", "")
    try:
        fd = int(value)
        info = os.fstat(fd)
        target = (LOCK_ROOT / "operation.lock").stat()
        if info.st_uid == 0 and stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            return (fd,)
    except (ValueError, OSError):
        pass
    return ()


@contextmanager
def admin_lock():
    inherited = inherited_fds()
    if os.geteuid() != 0 or inherited:
        yield inherited[0] if inherited else None
        return
    if LOCK_ROOT.is_symlink():
        raise ValueError("Unsafe admin lock directory")
    LOCK_ROOT.mkdir(mode=0o700, exist_ok=True)
    info = LOCK_ROOT.stat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("Admin lock directory must be private and root-owned")
    fd = os.open(LOCK_ROOT / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    previous = os.environ.get("SSLM_LOCK_FD")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise ValueError("Unsafe admin lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another manager operation or permission reconciliation is running; retry shortly") from error
        os.environ["SSLM_LOCK_FD"] = str(fd)
        yield fd
    finally:
        if previous is None:
            os.environ.pop("SSLM_LOCK_FD", None)
        else:
            os.environ["SSLM_LOCK_FD"] = previous
        os.close(fd)


@contextmanager
def account_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError("Unsafe account lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another setup operation is changing this Steam account") from error
        yield
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        with admin_lock():
            os.environ.setdefault("SSLM_LOCK_FD", "unprivileged")
            raise SystemExit(subprocess.call(sys.argv[1:], pass_fds=inherited_fds()))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
