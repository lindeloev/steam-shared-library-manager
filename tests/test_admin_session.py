"""Validation tests for the privileged GUI request boundary."""

from __future__ import annotations

import importlib.util
import pwd
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_admin_session():
    path = ROOT / "commands/gui-admin-session.py"
    spec = importlib.util.spec_from_file_location("gui_admin_session", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RequestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_admin_session()

    def test_accepts_allowlisted_command_and_string_arguments(self) -> None:
        request = {
            "command": "status.py",
            "arguments": ["--library", "/srv/SteamLibrary", "--group", "steamgames"],
        }
        self.assertEqual(self.module.valid_request(request), ("status.py", request["arguments"]))

    def test_rejects_unknown_command(self) -> None:
        request = {"command": "/bin/sh", "arguments": ["-c", "id"]}
        self.assertIsNone(self.module.valid_request(request))

    def test_rejects_non_string_and_nul_arguments(self) -> None:
        self.assertIsNone(self.module.valid_request({"command": "status.py", "arguments": [1]}))
        self.assertIsNone(
            self.module.valid_request({"command": "status.py", "arguments": ["bad\0argument"]})
        )

    def test_rejects_group_other_than_steamgames(self) -> None:
        normal_user = next(
            account.pw_name
            for account in pwd.getpwall()
            if account.pw_uid >= 1000 and not account.pw_shell.endswith(("nologin", "false"))
        )
        request = {
            "command": "grant-library-users.sh",
            "arguments": ["--group", "sudo", normal_user],
        }
        self.assertIsNone(self.module.valid_request(request))

    def test_accepts_optional_default_library_for_add_user(self) -> None:
        normal_user = next(
            account.pw_name
            for account in pwd.getpwall()
            if account.pw_uid >= 1000 and not account.pw_shell.endswith(("nologin", "false"))
        )
        request = {
            "command": "add-user.sh",
            "arguments": [
                "--close-steam",
                "--default-library",
                "/srv/SteamLibrary",
                "--group",
                "steamgames",
                "--base-proton",
                "/srv/SteamLibrary/steamapps/common/Proton - Experimental",
                normal_user,
            ],
        }
        self.assertEqual(
            self.module.valid_request(request),
            ("add-user.sh", request["arguments"]),
        )


if __name__ == "__main__":
    unittest.main()
