"""Account mutation tests run only against temporary Steam trees."""
import importlib.util
import os
import pwd
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_worker():
    spec = importlib.util.spec_from_file_location("account_worker", ROOT / "commands/account-worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AccountWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker()
        self.storage = self.worker.load_storage()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / "Steam"
        (self.home / ".steam").mkdir()
        (self.home / ".steam/root").symlink_to(self.root)
        (self.root / "config").mkdir(parents=True)
        (self.root / "config/libraryfolders.vdf").write_text(f'"libraryfolders" {{ "0" {{ "path" "{self.root}" }} }}')
        self.config = self.root / "config/config.vdf"
        self.config.write_text('"InstallConfigStore" { "CompatToolMapping" { "0" { "name" "proton_experimental" } } }')
        account = self.root / "userdata/123/config/localconfig.vdf"
        account.parent.mkdir(parents=True)
        account.write_text('"UserLocalConfigStore" { "LastInstallFolderIndex" "1" }')
        self.proton = self.root / "steamapps/common/Proton - Experimental"
        self.proton.mkdir(parents=True)
        (self.proton / "proton").write_text('#!/bin/sh\nexit 0\n')
        (self.proton / "proton").chmod(0o755)
        (self.proton / "toolmanifest.vdf").write_text('"manifest" { "version" "2" "commandline" "/proton %verb%" "require_tool_appid" "1628350" }')
        account = pwd.getpwuid(os.getuid())
        self.user = SimpleNamespace(pw_name=account.pw_name, pw_dir=str(self.home), pw_uid=account.pw_uid, pw_gid=account.pw_gid)
        self.args = SimpleNamespace(base_proton=str(self.proton), remove_prefixes=False)
        self.assets = {"proton": b'#!/bin/sh\nexit 0\n', "registration": b'"compatibilitytools" {}', "manifest-helper": (ROOT / "commands/manifest-helper.py").read_bytes(), "vdf": (ROOT / "commands/vdf.py").read_bytes()}
        self.patches = [mock.patch.object(self.storage.pwd, "getpwnam", return_value=self.user),
                        mock.patch.object(self.storage.subprocess, "run", return_value=SimpleNamespace(returncode=1))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_failed_staging_preserves_previous_wrapper(self):
        tool = self.root / "compatibilitytools.d" / self.worker.TOOL_ID
        tool.mkdir(parents=True)
        (tool / "proton").write_text("previous wrapper")
        with mock.patch.object(self.storage, "apply_user", side_effect=ValueError("conflict")):
            with self.assertRaisesRegex(ValueError, "conflict"):
                self.worker.install(self.user, self.args, self.storage, self.assets, False)
        self.assertEqual((tool / "proton").read_text(), "previous wrapper")
        self.assertEqual(list(tool.parent.glob(".manager-stage-*")), [])

    def test_uninstall_clears_only_managed_mappings_and_keeps_saves(self):
        self.config.write_text('"root" { "CompatToolMapping" { "0" { "name" "steam-shared-library-proton" } "42" { "name" "proton_90" } } }')
        saves = self.home / ".local/share/steam-shared-library-proton/42"
        saves.mkdir(parents=True)
        self.worker.uninstall(self.user, self.args, self.storage, False)
        result = self.config.read_text()
        self.assertNotIn('"steam-shared-library-proton"', result)
        self.assertIn('"proton_90"', result)
        self.assertTrue(saves.is_dir())

    def test_setup_selects_wrapper_and_uninstall_restores_previous_default(self):
        original = '"Steam" { "CompatToolMapping" { "0" { "name" "proton_experimental" } "42" { "name" "proton_90" } } }'
        self.config.write_text(original)
        self.worker.install(self.user, self.args, self.storage, self.assets, False)
        mappings = self.worker.VDF.mappings(self.config.read_text())
        self.assertEqual(mappings["0"]["name"], self.worker.TOOL_ID)
        self.assertEqual(mappings["42"]["name"], "proton_90")
        self.worker.uninstall(self.user, self.args, self.storage, False)
        self.assertEqual(self.worker.VDF.mappings(self.config.read_text())["0"]["name"], "proton_experimental")

    def test_unavailable_pinned_version_is_not_collapsed_to_default(self):
        self.config.write_text('"Steam" { "CompatToolMapping" { "42" { "name" "proton_90" } } }')
        with self.assertRaisesRegex(ValueError, "Pinned proton_90"):
            self.worker.migrate(self.user, self.args, self.storage, self.assets, False)
        self.assertEqual(self.worker.VDF.mappings(self.config.read_text())["42"]["name"], "proton_90")

    def test_existing_legacy_prefix_is_preserved_as_configured_location(self):
        legacy = self.home / ".local/share/steam-per-user-proton"
        legacy.mkdir(parents=True)
        (legacy / "42").mkdir()
        self.worker.install(self.user, self.args, self.storage, self.assets, False)
        tool = self.root / "compatibilitytools.d" / self.worker.TOOL_ID
        values = self.worker.MANIFEST.config((tool / "base-proton.conf").read_text())
        self.assertEqual(values["PREFIX_ROOT"], str(legacy))
        self.assertTrue((legacy / "42").exists())
        self.assertEqual(legacy.stat().st_mode & 0o777, 0o700)

    def test_storage_write_refuses_symlinked_parent(self):
        outside = self.home / "outside"
        outside.mkdir()
        file = outside / "localconfig.vdf"
        file.write_text("original")
        linked = self.home / "linked"
        linked.symlink_to(outside)
        with self.assertRaises(OSError):
            self.storage.atomic_update(linked / file.name, "original", "new", self.user)
        self.assertEqual(file.read_text(), "original")
