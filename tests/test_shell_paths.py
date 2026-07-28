"""Regression tests for shell path-containment validation."""

from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "commands/_common.sh"


class ShellPathValidationTests(unittest.TestCase):
    def run_validation(self, root: Path, target: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "/bin/sh",
                "-c",
                '. "$1"; require_existing_path_below "$2" "$3"',
                "sh",
                str(COMMON),
                str(root),
                str(target),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_accepts_descendant_and_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            inside = home / "inside"
            outside = base / "outside"
            inside.mkdir(parents=True)
            outside.mkdir()

            self.assertEqual(self.run_validation(home, inside).returncode, 0)

            escape = home / "escape"
            escape.symlink_to(outside)
            result = self.run_validation(home, escape)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing path outside", result.stderr)

    def test_library_path_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real_parent = base / "real"
            real_parent.mkdir()
            linked_parent = base / "linked"
            linked_parent.symlink_to(real_parent)
            result = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    '. "$1"; require_safe_library_path "$2"',
                    "sh",
                    str(COMMON),
                    str(linked_parent / "SteamLibrary"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symbolic links", result.stderr)

    def test_prefix_tree_is_made_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            prefix_root = home / ".local/share/steam-shared-library-proton"
            app_prefix = prefix_root / "1234"
            app_prefix.mkdir(parents=True)
            prefix_root.chmod(0o775)
            app_prefix.chmod(0o775)

            result = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    '. "$1"; make_prefix_tree_private "$2" "$3" "$4"',
                    "sh",
                    str(COMMON),
                    str(home),
                    str(prefix_root),
                    pwd.getpwuid(os.getuid()).pw_name,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(prefix_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(app_prefix.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
