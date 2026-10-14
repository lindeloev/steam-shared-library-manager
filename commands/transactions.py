"""Private recovery journals for staged wrapper and tool publications."""
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path


def directory(root):
    parent = root / ".steam-shared-library-manager/operations"
    if parent.parent.is_symlink() or parent.is_symlink():
        raise ValueError("Unsafe recovery directory")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return parent


def write(path, data):
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def start(root, kind, fields):
    path = directory(root) / (uuid.uuid4().hex + ".json")
    data = {"schema": 1, "kind": kind, "status": "running", **fields}
    write(path, data)
    return path, data


def finish(receipt, status, detail=""):
    path, data = receipt
    data.update(status=status, detail=detail)
    write(path, data)


def _path(value, parent, prefix=""):
    path = Path(value)
    if path.parent != parent or (prefix and not path.name.startswith(prefix)) or path.is_symlink():
        raise ValueError("Recovery record refers outside its allowed directory")
    if path.resolve().parent != parent.resolve():
        raise ValueError("Symlinked recovery path")
    return path


def recover(root, digest):
    operations = directory(root)
    backups = root / ".steam-shared-library-manager/backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    for record in operations.glob("*.json"):
        if record.is_symlink() or record.stat().st_size > 1024 * 1024:
            raise ValueError("Unsafe recovery receipt")
        data = json.loads(record.read_text())
        if data.get("status") != "running" or data.get("kind") not in ("wrapper", "tool-copy", "runtime-import"):
            continue
        if data["kind"] == "runtime-import":
            staged = _path(data["staging"], root / "steamapps/common", ".runtime-import-")
            if staged.exists():
                staged.rename(backups / ("abandoned-runtime-" + uuid.uuid4().hex))
            data.update(status="recovered", detail="Interrupted runtime transfer retained in backups")
            write(record, data)
            continue
        tool_copy = data["kind"] == "tool-copy"
        parent = root / ("steamapps/common" if tool_copy else "compatibilitytools.d")
        destination = _path(data["destination"], parent)
        staged = _path(data["staging"], parent, ".tool-copy-" if tool_copy else ".manager-stage-")
        backup = _path(data["backup"], backups, "tool-" if tool_copy else "wrapper-")
        if tool_copy:
            manifest = _path(data["manifest"], root / "steamapps", "appmanifest_")
            new_is_staged = (staged / "package").exists()
            verified = (not new_is_staged and destination.is_dir() and manifest.is_file()
                        and digest(destination)[0] == data["digest"]
                        and hashlib.sha256(manifest.read_bytes()).hexdigest() == data["manifest_digest"])
            if not verified:
                if not new_is_staged and destination.exists():
                    destination.rename(backups / ("aborted-package-" + uuid.uuid4().hex))
                if (backup / "package").exists() and not destination.exists():
                    (backup / "package").rename(destination)
                if (backup / "manifest.acf").exists():
                    if manifest.exists():
                        manifest.rename(backups / ("aborted-manifest-" + uuid.uuid4().hex))
                    (backup / "manifest.acf").rename(manifest)
        else:
            # A fully published wrapper remains usable even if the later config
            # selection was interrupted; setup can finish that selection on retry.
            if not destination.exists() and backup.exists():
                backup.rename(destination)
        if staged.exists():
            staged.rename(backups / ("abandoned-stage-" + uuid.uuid4().hex))
        finish((record, data), "recovered", "Previous installation restored or verified publication retained; all staged data preserved in backups")
