"""Copy Steam support tools as the destination account; retain recoverable sources."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import errno
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path


def load_transactions():
    spec = importlib.util.spec_from_file_location("transactions", Path(__file__).with_name("transactions.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

TRANSACTIONS = load_transactions()


def tree_digest(root: Path):
    """Hash regular contents and link text, never follow packaged symlinks.

    Runtime var/ contains generated, UID-specific hardlinks and must be recreated
    by each account, rather than copied from the shared installation.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Not a plain installed tool directory: {root}")
    digest = hashlib.sha256()
    total = 0
    entries = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if relative == Path('.'):
            directories[:] = [name for name in directories if name != "var"]
        for name in sorted(directories + files):
            path = Path(directory) / name
            info = path.lstat()
            entries += 1
            if entries > 500_000:
                raise ValueError("Tool tree exceeds entry safety limit")
            digest.update(str(path.relative_to(root)).encode() + b'\0')
            if stat.S_ISLNK(info.st_mode):
                digest.update(b'L' + os.readlink(path).encode())
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b'D')
            elif stat.S_ISREG(info.st_mode):
                digest.update(b'F' + str(bool(info.st_mode & 0o111)).encode())
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    opened = os.fstat(fd)
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("Source changed while reading tool")
                    while True:
                        chunk = os.read(fd, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    after = os.fstat(fd)
                    if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Tool changed during verification")
                finally:
                    os.close(fd)
                total += info.st_size
            else:
                raise ValueError(f"Refusing special file in tool package: {path}")
        directories.sort()
    return digest.hexdigest(), total


def support(name, directory, appid):
    return (name.lower().startswith("steam linux runtime")
            or directory.startswith("SteamLinuxRuntime")
            or any(re.match(r"^Proton(?:$|[ -]+(?:\d|Experimental\b|Hotfix\b|Next\b|BattlEye\b|EasyAntiCheat\b))", value, re.I) for value in (name, directory))
            or appid in ("228980", "1161040", "1826330"))


def sources(library, vdf):
    result = []
    common = library / "steamapps/common"
    if common.resolve() != common:
        raise ValueError("Shared common directory must not contain symlinks")
    for path in sorted((library / "steamapps").glob("appmanifest_*.acf")):
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError(f"Unsafe tool manifest: {path}")
        text = path.read_text()
        entry = vdf.object_entry(text, "AppState")
        if entry is None:
            raise ValueError(f"Malformed app manifest: {path.name}")
        appid = vdf.scalar(entry, "appid")
        name = vdf.scalar(entry, "name")
        directory = vdf.scalar(entry, "installdir")
        if not support(name, directory, appid):
            continue
        if not appid.isdigit() or path.name != f"appmanifest_{appid}.acf" or Path(directory).name != directory or directory in ("", ".", ".."):
            raise ValueError(f"Unsafe tool manifest path: {path.name}")
        flags = vdf.scalar(entry, "StateFlags")
        if not flags.isdigit() or not int(flags) & 4 or int(flags) & (256 | 512 | 1024):
            raise ValueError(f"Finish the Steam download/update before copying {name}")
        source = common / directory
        fingerprint, size = tree_digest(source)
        if any(item["directory"] == directory for item in result):
            raise ValueError("Multiple manifests refer to the same tool directory")
        result.append({"appid": appid, "name": name, "directory": directory,
                       "manifest": path, "manifest_text": text, "source": source,
                       "digest": fingerprint, "bytes": size})
    return result


def plan(root, library, vdf):
    result = sources(library, vdf)
    for item in result:
        destination = root / "steamapps/common" / item["directory"]
        manifest = root / "steamapps" / item["manifest"].name
        if destination.is_symlink() or manifest.is_symlink():
            item["operation"] = "conflict"
            item["reason"] = "Destination is symlinked"
        elif destination.exists() or manifest.exists():
            identical = destination.is_dir() and manifest.is_file() and tree_digest(destination)[0] == item["digest"]
            if identical:
                local = vdf.object_entry(manifest.read_text(), "AppState")
                source = vdf.object_entry(item["manifest_text"], "AppState")
                identical = (vdf.scalar(local, "appid") == item["appid"]
                             and vdf.scalar(local, "buildid") == vdf.scalar(source, "buildid")
                             and vdf.scalar(local, "installdir") == item["directory"])
            item["operation"] = "keep verified local copy" if identical else "conflict"
            item["reason"] = "Different or incomplete local installation" if not identical else "Package contents match"
        else:
            item["operation"] = "copy"
            item["reason"] = "Not installed locally"
    required = sum(item["bytes"] for item in result if item["operation"] != "keep verified local copy")
    if required > shutil.disk_usage(root).free:
        raise ValueError(f"Not enough local space: need {required / 2**30:.2f} GiB plus filesystem overhead")
    return result


def make_copy_private(root):
    for directory, directories, files in os.walk(root, followlinks=False):
        for path in [Path(directory), *[Path(directory) / name for name in files]]:
            if path.is_symlink():
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                    raise ValueError("Unexpected special file in staged tool copy")
                for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
                    try:
                        os.removexattr(fd, attribute)
                    except OSError as error:
                        if error.errno not in (errno.ENODATA, errno.ENOTSUP):
                            raise
                mode = 0o700 if stat.S_ISDIR(info.st_mode) else 0o400 | (0o200 if info.st_mode & 0o200 else 0) | (0o100 if info.st_mode & 0o111 else 0)
                os.fchmod(fd, mode)
            finally:
                os.close(fd)


def copy_one(item, root, state, replace_conflicts=False):
    common = root / "steamapps/common"
    common.mkdir(parents=True, exist_ok=True)
    if common.resolve() != common:
        raise ValueError("Local common directory is symlinked")
    destination = common / item["directory"]
    manifest = root / "steamapps" / item["manifest"].name
    if item["operation"] == "conflict" and not replace_conflicts:
        raise ValueError(f"Destination conflict for {item['name']}; choose backup-and-replace explicitly")
    if item["operation"] == "keep verified local copy":
        make_copy_private(destination)
        return
    staging = Path(tempfile.mkdtemp(prefix=".tool-copy-", dir=common))
    package = staging / "package"
    backup = state / ("tool-" + uuid.uuid4().hex)
    receipt = TRANSACTIONS.start(root, "tool-copy", {"destination": str(destination), "manifest": str(manifest),
                "staging": str(staging), "backup": str(backup), "digest": item["digest"],
                "manifest_digest": hashlib.sha256(item["manifest_text"].encode()).hexdigest()})
    moved = False
    published = False
    try:
        def ignore(directory, names):
            return ["var"] if Path(directory) == item["source"] and "var" in names else []
        shutil.copytree(item["source"], package, symlinks=True, ignore=ignore)
        if tree_digest(package)[0] != item["digest"] or tree_digest(item["source"])[0] != item["digest"]:
            raise ValueError("Tool changed during copying; staged copy was discarded")
        make_copy_private(package)
        if item["manifest"].read_text() != item["manifest_text"]:
            raise ValueError("Steam changed the source manifest during copying")
        staged_manifest = staging / "manifest.acf"
        staged_manifest.write_text(item["manifest_text"])
        if destination.exists() or manifest.exists():
            if not replace_conflicts:
                raise ValueError("Destination appeared during copying; refusing replacement")
            if destination.is_symlink() or manifest.is_symlink():
                raise ValueError("Refusing symlinked destination conflict")
            backup.mkdir(mode=0o700)
            if destination.exists():
                destination.rename(backup / "package")
            if manifest.exists():
                manifest.rename(backup / "manifest.acf")
            moved = True
        package.rename(destination)
        published = True
        staged_manifest.rename(manifest)
        TRANSACTIONS.finish(receipt, "complete")
    except BaseException as error:
        if published and destination.exists():
            # Retain the new package as a failed-stage backup rather than delete.
            failed = state / ("failed-tool-" + uuid.uuid4().hex)
            destination.rename(failed)
        if moved:
            if (backup / "package").exists():
                (backup / "package").rename(destination)
            if (backup / "manifest.acf").exists():
                (backup / "manifest.acf").rename(manifest)
        TRANSACTIONS.finish(receipt, "failed", str(error))
        raise
    finally:
        shutil.rmtree(staging)


def quarantine_sources(library, items):
    """Remove discovery entries by reversible rename; never delete source binaries."""
    quarantine = library / ".steam-shared-library-manager/quarantine" / uuid.uuid4().hex
    if quarantine.parent.parent.is_symlink() or quarantine.parent.is_symlink():
        raise ValueError("Unsafe shared quarantine directory")
    quarantine.mkdir(parents=True, mode=0o700)
    for item in items:
        if tree_digest(item["source"])[0] != item["digest"] or item["manifest"].read_text() != item["manifest_text"]:
            raise ValueError("Shared source changed; keeping it registered")
        destination = quarantine / item["directory"]
        item["source"].rename(destination)
        try:
            item["manifest"].rename(quarantine / item["manifest"].name)
        except BaseException:
            destination.rename(item["source"])
            raise
    (quarantine / "recovery.json").write_text(json.dumps({"library": str(library), "tools": [x["directory"] for x in items]}, indent=2))
    return quarantine
