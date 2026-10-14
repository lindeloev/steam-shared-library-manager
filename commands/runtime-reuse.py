"""Transfer runtime packages through an anonymous file between account workers.

The privileged parent only passes an open descriptor. The supplying account
reads its package; the receiving account extracts, verifies and publishes it.
No account's home permissions are relaxed, and root never extracts an archive.
"""
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def beta_selected(root, appid, vdf):
    """Respect an app's branch choice, including choices without a package."""
    paths = [root / "config/config.vdf", *sorted((root / "userdata").glob("*/config/localconfig.vdf"))]
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if path.resolve(strict=True) != path:
                return True
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                    return True
                entries = vdf.parse(handle.read())

            def has_beta(entries):
                for entry in entries:
                    if not isinstance(entry.value, list):
                        continue
                    if entry.key.casefold() == "apps":
                        app = vdf.find(entry.value, appid)
                        for key in ("BetaKey", "BetaName", "SelectedBeta"):
                            value = vdf.scalar(app, key)
                            if value and value.casefold() != "public":
                                return True
                    if has_beta(entry.value):
                        return True
                return False

            if has_beta(entries):
                return True
        except (OSError, ValueError):
            return True
    return False


def public_runtime(root, appid, manifest, migration, vdf):
    if not manifest.runtime_installed(root, appid) or beta_selected(root, appid, vdf):
        return None
    path = root / f"steamapps/appmanifest_{appid}.acf"
    text = migration.read_manifest(path)
    entry = vdf.object_entry(text, "AppState")
    for section in ("UserConfig", "MountedConfig"):
        config = vdf.find(entry.value, section)
        branch = vdf.scalar(config, "BetaKey")
        if branch and branch.casefold() != "public":
            return None
    directory = vdf.scalar(entry, "installdir")
    if not directory.startswith("SteamLinuxRuntime"):
        return None
    package = root / "steamapps/common" / directory
    for name in ("VERSIONS.txt", "_v2-entry-point"):
        path = package / name
        if path.is_symlink() or not path.is_file():
            return None
    if not vdf.scalar(entry, "buildid").isdigit():
        return None
    return {"appid": appid, "directory": directory, "source_root": root,
            "manifest_text": text, "manifest": path, "source": root / "steamapps/common" / directory}


def destination_available(root, item, vdf=None):
    # Existing and incomplete installations are deliberately left for Steam.
    paths = [root / "steamapps" / item["manifest"].name,
             root / "steamapps/common" / item["directory"]]
    return (all(not path.exists() and not path.is_symlink() for path in paths)
            and (vdf is None or not beta_selected(root, item["appid"], vdf)))


def export_package(fd, item, migration, vdf, manifest):
    current = public_runtime(item["source_root"], item["appid"], manifest, migration, vdf)
    if current is None or current["manifest_text"] != item["manifest_text"]:
        raise ValueError("Runtime source changed; refresh the setup preview")
    digest, size = migration.tree_digest(item["source"])
    if size > 8 * 2**30 or shutil.disk_usage("/tmp").free < size + 16 * 1024 * 1024:
        raise ValueError("Not enough temporary space for runtime transfer, or package exceeds 8 GiB")
    text = vdf.set_in_object(item["manifest_text"], "AppState", "LastOwner", "0")
    text = vdf.set_in_object(text, "AppState", "LastPlayed", "0")
    metadata = json.dumps({"digest": digest, "bytes": size, "manifest_text": text}).encode()
    with os.fdopen(os.dup(fd), "wb") as output, tarfile.open(fileobj=output, mode="w") as archive:
        header = tarfile.TarInfo("metadata.json")
        header.size = len(metadata)
        archive.addfile(header, io.BytesIO(metadata))
        source = item["source"]
        for directory, directories, files in os.walk(source, followlinks=False):
            if Path(directory) == source:
                directories[:] = [name for name in directories if name != "var"]
            for name in sorted(directories + files):
                path = Path(directory) / name
                info = path.lstat()
                header = tarfile.TarInfo("package/" + path.relative_to(source).as_posix())
                header.mode = 0o700 if info.st_mode & 0o111 else 0o600
                if stat.S_ISLNK(info.st_mode):
                    header.type = tarfile.SYMTYPE
                    header.linkname = os.readlink(path)
                    archive.addfile(header)
                elif stat.S_ISDIR(info.st_mode):
                    header.type = tarfile.DIRTYPE
                    archive.addfile(header)
                elif stat.S_ISREG(info.st_mode):
                    opened = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(opened, "rb") as content:
                        actual = os.fstat(content.fileno())
                        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("Runtime source changed during export")
                        header.size = actual.st_size
                        archive.addfile(header, content)
                else:
                    raise ValueError("Special file in runtime package")
        if migration.tree_digest(source)[0] != digest or migration.read_manifest(item["manifest"]) != item["manifest_text"]:
            raise ValueError("Runtime changed during export; no destination was installed")


def import_package(fd, root, item, migration, vdf, manifest):
    if manifest.runtime_installed(root, item["appid"]):
        return
    if not destination_available(root, item, vdf):
        raise ValueError("Runtime destination now exists; preserving it. Refresh status and finish its download through Steam.")
    common = root / "steamapps/common"
    if common.resolve() != common:
        raise ValueError("Local common directory must not contain symbolic links")
    common.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".runtime-import-", dir=common))
    receipt = migration.TRANSACTIONS.start(root, "runtime-import", {"staging": str(staging)})
    try:
        package = staging / "package"
        package.mkdir(mode=0o700)
        with os.fdopen(os.dup(fd), "rb") as source, tarfile.open(fileobj=source, mode="r|") as archive:
            first = archive.next()
            if first is None or first.name != "metadata.json" or not first.isfile() or first.size > 1024 * 1024:
                raise ValueError("Invalid runtime transfer metadata")
            metadata = json.loads(archive.extractfile(first).read())
            if (not isinstance(metadata, dict) or not isinstance(metadata.get("digest"), str)
                    or not isinstance(metadata.get("manifest_text"), str)):
                raise ValueError("Invalid runtime transfer metadata fields")
            size = metadata["bytes"]
            if not isinstance(size, int) or size < 0 or size > 8 * 2**30 or shutil.disk_usage(root).free < 2 * size + 16 * 1024 * 1024:
                raise ValueError("Not enough local space for verified runtime staging")
            copied = 0
            entries = 0
            for member in archive:
                # The streaming iterator may yield the already-read first member.
                if member is first:
                    continue
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != "package" or len(parts) < 2 or ".." in parts or parts[1] == "var":
                    raise ValueError("Unsafe runtime transfer path")
                entries += 1
                if entries > 500_000:
                    raise ValueError("Runtime transfer exceeds entry safety limit")
                path = staging.joinpath(*parts)
                parent = package
                for part in parts[1:-1]:
                    parent = parent / part
                    if parent.is_symlink():
                        raise ValueError("Runtime transfer would write through a symlink")
                    parent.mkdir(mode=0o700, exist_ok=True)
                if member.isdir():
                    if path.is_symlink():
                        raise ValueError("Symlinked runtime transfer directory")
                    path.mkdir(mode=0o700, exist_ok=True)
                elif member.issym():
                    path.symlink_to(member.linkname)
                elif member.isfile():
                    copied += member.size
                    if copied > size:
                        raise ValueError("Runtime transfer exceeds declared package size")
                    opened = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o700 if member.mode & 0o111 else 0o600)
                    with os.fdopen(opened, "wb") as output:
                        shutil.copyfileobj(archive.extractfile(member), output)
                else:
                    raise ValueError("Unsupported runtime transfer entry")
        text = metadata["manifest_text"]
        entry = vdf.object_entry(text, "AppState")
        if vdf.scalar(entry, "appid") != item["appid"] or vdf.scalar(entry, "installdir") != item["directory"]:
            raise ValueError("Transferred manifest does not match the required runtime")
        if migration.tree_digest(package) != (metadata["digest"], size):
            raise ValueError("Runtime copy verification failed")
        path = staging / item["manifest"].name
        path.write_text(text)
        # A branch choice or new destination appearing during extraction must
        # not be replaced by this automatically provisioned public package.
        if not destination_available(root, item, vdf):
            raise ValueError("Runtime destination or branch choice changed during copying; preserving it")
        staged_item = {**item, "source": package, "manifest": path, "manifest_text": text,
                       "digest": metadata["digest"], "bytes": size, "operation": "copy"}
        state = root / ".steam-shared-library-manager/backups"
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        migration.copy_one(staged_item, root, state)
        if not manifest.runtime_installed(root, item["appid"]):
            raise ValueError("Published runtime failed the local installation check")
        migration.TRANSACTIONS.finish(receipt, "complete")
    except BaseException as error:
        migration.TRANSACTIONS.finish(receipt, "failed", str(error))
        raise
    finally:
        shutil.rmtree(staging)
