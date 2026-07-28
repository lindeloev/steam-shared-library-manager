#!/usr/bin/env python3
"""Run the GUI's limited administrative commands in one pkexec session.

The GUI owns this process's stdin/stdout pipes.  Keeping it alive means
PolicyKit authorizes once per open manager window instead of once per refresh
or button press.  Only the small, fixed command set below can be dispatched.
"""

from __future__ import annotations

import json
import os
import pwd
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
GROUP = "steamgames"
COMMAND_FILES = (
    "_common.sh",
    "add-user.sh",
    "close-steam.sh",
    "configure-steam-storage.py",
    "grant-library-users.sh",
    "install.sh",
    "migrate-existing-games.sh",
    "proton",
    "repair-shared-library.sh",
    "setup-shared-library.sh",
    "status.py",
)
PROJECT_FILES = ("steam-shared-library-proton.vdf", "toolmanifest.vdf")
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024


def is_absolute_path(value: str) -> bool:
    return (
        value.startswith("/")
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


def are_normal_users(values: list[str]) -> bool:
    """Accept existing human accounts, never root or service accounts."""
    if not values:
        return False
    for name in values:
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            return False
        if account.pw_uid < 1000 or account.pw_shell.endswith(("nologin", "false")):
            return False
    return True


def arguments_are_allowed(command: str, arguments: list[str]) -> bool:
    """Match only command forms emitted by the GUI."""
    if command == "status.py":
        return (
            len(arguments) == 4
            and arguments[0] == "--library"
            and is_absolute_path(arguments[1])
            and arguments[2:] == ["--group", GROUP]
        )
    if command == "setup-shared-library.sh":
        return (
            len(arguments) >= 5
            and arguments[0] == "--library"
            and is_absolute_path(arguments[1])
            and arguments[2:4] == ["--group", GROUP]
            and are_normal_users(arguments[4:])
        )
    if command == "repair-shared-library.sh":
        return (
            len(arguments) == 4
            and arguments[0] == "--library"
            and is_absolute_path(arguments[1])
            and arguments[2:] == ["--group", GROUP]
        )
    if command == "grant-library-users.sh":
        return (
            len(arguments) >= 3
            and arguments[:2] == ["--group", GROUP]
            and are_normal_users(arguments[2:])
        )
    if command == "add-user.sh":
        if arguments[:1] != ["--close-steam"]:
            return False
        offset = 1
        if arguments[offset:offset + 1] == ["--default-library"]:
            if len(arguments) <= offset + 1 or not is_absolute_path(arguments[offset + 1]):
                return False
            offset += 2
        return (
            len(arguments) >= offset + 5
            and arguments[offset:offset + 2] == ["--group", GROUP]
            and arguments[offset + 2] == "--base-proton"
            and is_absolute_path(arguments[offset + 3])
            and are_normal_users(arguments[offset + 4:])
        )
    if command == "close-steam.sh":
        users = arguments[1:] if arguments[:1] == ["--force"] else arguments
        return are_normal_users(users)
    if command == "migrate-existing-games.sh":
        return (
            len(arguments) >= 2
            and arguments[0] == "--apply"
            and are_normal_users(arguments[1:])
        )
    return False


def valid_request(request: object) -> tuple[str, list[str]] | None:
    if not isinstance(request, dict):
        return None
    command = request.get("command")
    arguments = request.get("arguments")
    if not isinstance(command, str) or not isinstance(arguments, list):
        return None
    if not all(isinstance(argument, str) and "\x00" not in argument for argument in arguments):
        return None
    if not arguments_are_allowed(command, arguments):
        return None
    return command, arguments


def respond(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def copy_regular_file(source_directory: int, name: str, destination: Path) -> None:
    """Copy one already-authorized source file without following a file symlink."""
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_directory)
    try:
        source_status = os.fstat(source)
        if not stat.S_ISREG(source_status.st_mode):
            raise OSError(f"Project source is not a regular file: {name}")
        if source_status.st_size > MAX_SOURCE_FILE_BYTES:
            raise OSError(f"Project source is unexpectedly large: {name}")
        data = bytearray()
        while chunk := os.read(source, 64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_SOURCE_FILE_BYTES:
                raise OSError(f"Project source grew unexpectedly large: {name}")
    finally:
        os.close(source)
    destination.write_bytes(data)
    destination.chmod(0o700)


def snapshot_project(destination_root: Path) -> Path:
    """Freeze helper inputs in a root-private directory for this GUI session."""
    project = destination_root / "project"
    commands = project / "commands"
    commands.mkdir(parents=True, mode=0o700)
    project.chmod(0o700)
    commands.chmod(0o700)
    command_source = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    project_source = os.open(HERE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in COMMAND_FILES:
            copy_regular_file(command_source, name, commands / name)
        for name in PROJECT_FILES:
            copy_regular_file(project_source, name, project / name)
    finally:
        os.close(command_source)
        os.close(project_source)
    return commands


def run_invocation(invocation: list[str], timeout: float = 10 * 60) -> tuple[int, str]:
    """Run one command and terminate its complete process group on timeout."""
    process = subprocess.Popen(
        invocation,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _stderr = process.communicate(timeout=timeout)
        return process.returncode, output
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            output, _stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _stderr = process.communicate()
        return 124, f"Command timed out after {timeout:g} seconds.\n{output}".rstrip()


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This helper must be started through pkexec.")
    with tempfile.TemporaryDirectory(prefix="steam-shared-library-manager-") as temporary:
        try:
            runtime_commands = snapshot_project(Path(temporary))
        except OSError as error:
            raise SystemExit(f"Could not prepare a private command snapshot: {error}") from error
        # The GUI closes this pipe when its window closes; EOF ends the session.
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            try:
                request = valid_request(json.loads(line))
            except json.JSONDecodeError:
                request = None
            if request is None:
                respond({"code": 2, "output": "Invalid administrative request."})
                continue
            command, arguments = request
            executable = runtime_commands / command
            if command == "status.py":
                invocation = [sys.executable, "-I", "-B", str(executable), *arguments]
            else:
                invocation = [str(executable), *arguments]
            try:
                code, output = run_invocation(invocation)
                respond({"code": code, "output": output})
            except OSError as error:
                respond({"code": 127, "output": str(error)})


if __name__ == "__main__":
    main()
