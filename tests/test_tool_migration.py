import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "commands" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolMigrationTests(unittest.TestCase):
    def setUp(self):
        self.migration = load("tool-migration")
        self.vdf = load("vdf")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.shared = self.base / "shared"
        self.local = self.base / "local"
        self.source = self.shared / "steamapps/common/SteamLinuxRuntime_sniper"
        self.source.mkdir(parents=True)
        (self.source / "runtime").write_bytes(b'package')
        (self.source / "runtime").chmod(0o775)
        (self.source / "var").mkdir()
        (self.source / "var/private-cache").write_text('do not copy')
        (self.shared / "steamapps/appmanifest_1628350.acf").write_text('"AppState" { "appid" "1628350" "name" "Steam Linux Runtime 3.0" "installdir" "SteamLinuxRuntime_sniper" "StateFlags" "4" "buildid" "100" }')
        (self.local / "steamapps/common").mkdir(parents=True)
        self.state = self.local / "backups"
        self.state.mkdir()

    def test_copy_verifies_package_skips_uid_cache_and_removes_shared_permissions(self):
        item = self.migration.plan(self.local, self.shared, self.vdf)[0]
        self.migration.copy_one(item, self.local, self.state)
        copied = self.local / "steamapps/common" / item["directory"]
        self.assertFalse((copied / "var").exists())
        self.assertEqual((copied / "runtime").stat().st_mode & 0o077, 0)
        self.assertEqual(self.migration.tree_digest(copied)[0], item["digest"])
        self.assertEqual(self.migration.plan(self.local, self.shared, self.vdf)[0]["operation"], "keep verified local copy")

    def test_conflict_requires_explicit_replacement_and_retains_backup(self):
        destination = self.local / "steamapps/common/SteamLinuxRuntime_sniper"
        destination.mkdir()
        (destination / "runtime").write_text("different local installation")
        item = self.migration.plan(self.local, self.shared, self.vdf)[0]
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.migration.copy_one(item, self.local, self.state)
        self.assertEqual((destination / "runtime").read_text(), "different local installation")
        self.migration.copy_one(item, self.local, self.state, True)
        backups = list(self.state.glob("tool-*/package/runtime"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "different local installation")

    def test_changed_source_aborts_without_publishing_destination(self):
        item = self.migration.plan(self.local, self.shared, self.vdf)[0]
        (self.source / "runtime").write_bytes(b'new update')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.migration.copy_one(item, self.local, self.state)
        self.assertFalse((self.local / "steamapps/common" / item["directory"]).exists())
        self.assertTrue(self.source.exists())

    def test_cleanup_retains_sources_in_recoverable_quarantine(self):
        items = self.migration.plan(self.local, self.shared, self.vdf)
        for item in items:
            self.migration.copy_one(item, self.local, self.state)
        quarantine = self.migration.quarantine_sources(self.shared, items)
        self.assertFalse(self.source.exists())
        self.assertTrue((quarantine / "SteamLinuxRuntime_sniper/runtime").exists())
        self.assertTrue((quarantine / "appmanifest_1628350.acf").exists())
