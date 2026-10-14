#!/usr/bin/python3
"""Generate a safe wrapper manifest and refresh it before delegating to Proton."""
import argparse
import json
import subprocess
import importlib.util
import os
import re
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("vdf", Path(__file__).with_name("vdf.py"))
VDF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(VDF)


def generate(text):
    entry = VDF.object_entry(text, "manifest")
    if entry is None or VDF.scalar(entry, "version") != "2":
        raise ValueError("Unsupported Proton manifest version; no wrapper manifest was changed")
    command = VDF.scalar(entry, "commandline")
    if command != "/proton %verb%":
        raise ValueError("Unsupported Proton command line; wrapper must delegate through /proton")
    values = {"version": "2", "commandline": "/proton %verb%"}
    for key in ("require_tool_appid", "use_sessions", "compatmanager_layer_name"):
        value = VDF.scalar(entry, key)
        if not value:
            continue
        if key == "require_tool_appid" and not value.isdigit():
            raise ValueError("Invalid runtime dependency")
        if key == "use_sessions" and value not in ("0", "1"):
            raise ValueError("Invalid session setting")
        if key == "compatmanager_layer_name" and not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("Invalid compatibility layer name")
        values[key] = value
    supported = set(values) | {"require_tool_appid", "use_sessions", "compatmanager_layer_name"}
    unknown = [child.key for child in entry.value if child.key not in supported]
    if unknown:
        raise ValueError("New/unsupported Proton manifest fields: " + ", ".join(unknown))
    return '"manifest"\n{\n' + ''.join(f'\t"{key}" "{VDF.escape(value)}"\n' for key, value in values.items()) + '}\n'


def config(text):
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in ("BASE_PROTON", "PREFIX_ROOT") or key in values:
            raise ValueError("Invalid wrapper configuration")
        values[key] = value
    return values


def steam_identity(uid=None):
    result = subprocess.run(["/usr/bin/pgrep", "-u", str(os.getuid() if uid is None else uid), "-x", "steam"], capture_output=True, text=True)
    identities = {}
    for pid in result.stdout.split():
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            identities[pid] = fields[19]  # Linux starttime, robust to spaces in comm.
        except (OSError, IndexError):
            pass
    return identities


def restart_pending(tool, uid=None):
    marker = tool / "needs-steam-restart.json"
    if not marker.exists():
        return False
    if marker.is_symlink() or marker.stat().st_size > 16384:
        raise ValueError("Unsafe Steam restart marker")
    previous = json.loads(marker.read_text()).get("steam", {})
    current = steam_identity(uid)
    # If Steam was hidden inside a PID namespace, require manager setup while
    # Steam is closed rather than guessing that a restart happened.
    return not previous or any(current.get(pid) == started for pid, started in previous.items())


def refresh(tool):
    root = tool.parent.parent.resolve(strict=True)
    home = Path.home().resolve(strict=True)
    if not root.is_relative_to(home):
        raise ValueError("Wrapper must be in this account's local Steam tree")
    values = config((tool / "base-proton.conf").read_text())
    proton = Path(values["BASE_PROTON"])
    if proton.resolve() != proton or proton.parent != root / "steamapps/common":
        raise ValueError("Proton must be a plain directory in the local library")
    source = proton / "toolmanifest.vdf"
    if source.is_symlink() or source.stat().st_size > 1024 * 1024:
        raise ValueError("Unsafe local Proton manifest")
    generated = generate(source.read_text())
    installed = tool / "toolmanifest.vdf"
    if installed.is_symlink():
        raise ValueError("Refusing symlinked wrapper manifest")
    previous = installed.read_text()
    if previous == generated:
        marker = tool / "needs-steam-restart.json"
        if marker.exists():
            if restart_pending(tool):
                return True
            marker.unlink()
        return False
    old_runtime = VDF.scalar(VDF.object_entry(previous, "manifest"), "require_tool_appid")
    new_runtime = VDF.scalar(VDF.object_entry(generated, "manifest"), "require_tool_appid")
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=tool)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, installed)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    changed_runtime = old_runtime != new_runtime
    if changed_runtime:
        marker = tool / "needs-steam-restart.json"
        if marker.is_symlink():
            raise ValueError("Refusing symlinked restart marker")
        fd, temporary = tempfile.mkstemp(prefix=".restart-", dir=tool)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"steam": steam_identity()}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return changed_runtime


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if refresh(args.tool_dir):
            raise SystemExit("Proton's runtime dependency changed. The wrapper manifest was refreshed; restart Steam before launching again.")
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
