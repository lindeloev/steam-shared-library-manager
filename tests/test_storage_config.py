"""Tests for Steam library registration and default-storage configuration."""

from __future__ import annotations

import importlib.util
import os
import pwd
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_storage_config():
    path = ROOT / "commands/configure-steam-storage.py"
    spec = importlib.util.spec_from_file_location("configure_steam_storage", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.storage = load_storage_config()

    def test_registers_library_at_next_index_and_preserves_existing_entry(self) -> None:
        original = (
            '"libraryfolders"\n'
            "{\n"
            '\t"0"\n'
            "\t{\n"
            '\t\t"path"\t\t"/home/person/.steam/root"\n'
            '\t\t"apps"\n'
            "\t\t{\n"
            '\t\t\t"42"\t\t"1000"\n'
            "\t\t}\n"
            "\t}\n"
            "}\n"
        )

        updated, index = self.storage.ensure_library_registered(
            original, "/srv/SteamLibrary", 123456789
        )

        self.assertEqual(index, 1)
        self.assertIn('"42"\t\t"1000"', updated)
        self.assertIn('"path"\t\t"/srv/SteamLibrary"', updated)
        self.assertEqual(
            self.storage.library_indexes(updated)["/srv/SteamLibrary"], 1
        )

    def test_existing_library_is_not_rewritten(self) -> None:
        original = (
            '"libraryfolders"\n{\n'
            '\t"3"\n\t{\n\t\t"path"\t\t"/srv/SteamLibrary"\n\t}\n'
            "}\n"
        )

        updated, index = self.storage.ensure_library_registered(
            original, "/srv/SteamLibrary", 999
        )

        self.assertEqual(updated, original)
        self.assertEqual(index, 3)

    def test_sets_and_replaces_top_level_default_index(self) -> None:
        original = (
            '"UserLocalConfigStore"\n'
            "{\n"
            '\t"friends"\n'
            "\t{\n"
            '\t\t"LastInstallFolderIndex"\t\t"99"\n'
            "\t}\n"
            "}\n"
        )
        inserted = self.storage.set_default_folder_index(original, 2)
        self.assertIn('\t"LastInstallFolderIndex"\t\t"2"\n}', inserted)
        self.assertIn('\t\t"LastInstallFolderIndex"\t\t"99"', inserted)

        replaced = self.storage.set_default_folder_index(inserted, 4)
        self.assertIn('\t"LastInstallFolderIndex"\t\t"4"', replaced)
        self.assertNotIn('\t"LastInstallFolderIndex"\t\t"2"', replaced)

    def test_preflight_registers_shared_games_but_resets_default_to_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            steam_root = home / "Steam"
            (home / ".steam").mkdir()
            (home / ".steam/root").symlink_to(steam_root)
            (steam_root / "config").mkdir(parents=True)
            (steam_root / "config/libraryfolders.vdf").write_text(
                f'"libraryfolders" {{ "0" {{ "path" "{steam_root}" }} }}'
            )
            config = steam_root / "userdata/123/config/localconfig.vdf"
            config.parent.mkdir(parents=True)
            config.write_text('"UserLocalConfigStore" { "LastInstallFolderIndex" "7" }')
            user = SimpleNamespace(pw_dir=str(home))
            with mock.patch.object(self.storage.pwd, "getpwnam", return_value=user), \
                 mock.patch.object(self.storage.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
                plan = self.storage.prepare_user("person", Path("/srv/SteamLibrary"))
                local_only = self.storage.prepare_user("person", None)
            self.assertIn("/srv/SteamLibrary", plan[3])
            self.assertIn('"LastInstallFolderIndex" "0"', plan[5][0][2])
            self.assertEqual(local_only[2], local_only[3])
            self.assertIn('"LastInstallFolderIndex" "0"', local_only[5][0][2])
            self.assertIn('"7"', config.read_text())  # Preflight never writes.

    def test_atomic_update_backs_up_only_when_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "localconfig.vdf"
            path.write_text("old\n", encoding="utf-8")
            user = pwd.getpwuid(os.getuid())

            backup = self.storage.atomic_update(path, "old\n", "new\n", user)

            self.assertIsNotNone(backup)
            assert backup is not None
            self.assertEqual(backup.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertIsNone(self.storage.atomic_update(path, "new\n", "new\n", user))

    def test_atomic_update_refuses_a_post_preflight_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "localconfig.vdf"
            path.write_text("changed elsewhere\n", encoding="utf-8")
            user = pwd.getpwuid(os.getuid())

            with self.assertRaisesRegex(ValueError, "changed after preflight"):
                self.storage.atomic_update(path, "old\n", "manager update\n", user)

            self.assertEqual(path.read_text(encoding="utf-8"), "changed elsewhere\n")
            self.assertEqual(
                list(path.parent.glob("*.steam-shared-library-manager-backup.*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
