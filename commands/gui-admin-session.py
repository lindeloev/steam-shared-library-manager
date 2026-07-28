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
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
GROUP = "steamgames"


def is_absolute_path(value: str) -> bool:
    return value.startswith("/") and "\0" not in value


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


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This helper must be started through pkexec.")
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
        executable = HERE / command
        if command == "status.py":
            invocation = [sys.executable, str(executable), *arguments]
        else:
            invocation = [str(executable), *arguments]
        try:
            result = subprocess.run(
                invocation,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10 * 60,
            )
            respond({"code": result.returncode, "output": result.stdout})
        except subprocess.TimeoutExpired as error:
            partial = error.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            output = f"Command timed out after 10 minutes.\n{partial}".rstrip()
            respond({"code": 124, "output": output})
        except OSError as error:
            respond({"code": 127, "output": str(error)})


if __name__ == "__main__":
    main()
