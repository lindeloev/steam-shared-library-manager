#!/usr/bin/python3
"""Install immutable monitor code and register one validated shared library."""
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
import argparse

HERE = Path(__file__).resolve().parent


def atomic_root_file(path, content, mode=0o600):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"Unsafe service path: {path}")
    fd, name = tempfile.mkstemp(prefix=".manager-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--group", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Monitor installation requires administrator privileges")
    spec = importlib.util.spec_from_file_location("library_backend", HERE / "library-backend.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    library = backend.validate_library(Path(args.library))
    if not Path("/run/systemd/system").is_dir():
        print("Automatic permission monitoring unavailable: systemd is not running. Manual repair remains available.")
        return
    config_dir = Path("/etc/steam-shared-library-manager")
    program_dir = Path("/usr/local/lib/steam-shared-library-manager")
    for directory in (config_dir, program_dir, Path("/var/lib/steam-shared-library-manager")):
        if directory.is_symlink():
            raise ValueError(f"Refusing symlinked service directory: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
            raise ValueError(f"Service directory must be owned exclusively by root: {directory}")
    config_path = config_dir / "libraries.json"
    config = json.loads(config_path.read_text()) if config_path.exists() and not config_path.is_symlink() else {"libraries": []}
    previous = next((item.get("managed_users", []) for item in config["libraries"] if item["path"] == str(library)), [])
    managed_users = sorted(set(previous) | set(backend.group_users(__import__('grp').getgrnam(args.group).gr_gid)))
    config["libraries"] = [item for item in config["libraries"] if item["path"] != str(library)] + [{"path": str(library), "group": args.group, "managed_users": managed_users}]
    for name in ("permission-monitor.py", "library-backend.py", "vdf.py"):
        if (HERE / name).is_symlink():
            raise ValueError("Refusing symlinked monitor source")
        atomic_root_file(program_dir / name, (HERE / name).read_bytes(), 0o700)
    atomic_root_file(config_path, (json.dumps(config, indent=2) + "\n").encode())
    service = '''[Unit]
Description=Maintain permissions for shared Steam game updates
After=local-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -I -B /usr/local/lib/steam-shared-library-manager/permission-monitor.py
User=root
NoNewPrivileges=yes
ProtectSystem=full
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
UMask=0077
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
'''
    atomic_root_file(Path("/etc/systemd/system/steam-shared-library-manager.service"), service.encode(), 0o644)
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True)
    subprocess.run(["/usr/bin/systemctl", "enable", "--now", "steam-shared-library-manager.service"], check=True)
    subprocess.run(["/usr/bin/systemctl", "restart", "steam-shared-library-manager.service"], check=True)
    print("Automatic permission repair enabled: completed game updates are checked every 30 seconds.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
