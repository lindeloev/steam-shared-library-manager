import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("monitor", Path(__file__).resolve().parents[1] / "commands/permission-monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class PermissionMonitorTests(unittest.TestCase):
    def test_completed_update_repairs_new_files_and_ignores_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "games"
            game = library / "steamapps/common/Example"
            game.mkdir(parents=True)
            binary = game / "game.exe"
            binary.write_bytes(b'game')
            binary.chmod(0o600)
            manifest = library / "steamapps/appmanifest_42.acf"
            manifest.write_text('"AppState" { "appid" "42" "installdir" "Example" "name" "Example" "StateFlags" "4" "buildid" "100" }')
            known = {}
            with mock.patch.object(monitor, "normal_group_users", return_value=(os.getgid(), (os.getuid(),))):
                self.assertEqual(monitor.reconcile(library, "test", known), 1)
                self.assertTrue(binary.stat().st_mode & 0o020)
                self.assertEqual(monitor.reconcile(library, "test", known), 0)
                binary.unlink()
                binary.write_bytes(b'updated game')
                binary.chmod(0o600)
                manifest.write_text(manifest.read_text().replace('"100"', '"101"'))
                self.assertEqual(monitor.reconcile(library, "test", known), 1)
                self.assertTrue(binary.stat().st_mode & 0o020)
                runtime = game.with_name("SteamLinuxRuntime_sniper")
                runtime.mkdir()
                runtime.chmod(0o700)
                (library / "steamapps/appmanifest_1628350.acf").write_text('"AppState" { "appid" "1628350" "installdir" "SteamLinuxRuntime_sniper" "name" "Steam Linux Runtime 3.0" "StateFlags" "4" "buildid" "1" }')
                monitor.reconcile(library, "test", known)
                self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)

    def test_in_progress_updates_are_not_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "games"
            game = library / "steamapps/common/Example"
            game.mkdir(parents=True)
            (library / "steamapps/appmanifest_42.acf").write_text('"AppState" { "appid" "42" "installdir" "Example" "StateFlags" "1028" "buildid" "2" }')
            with mock.patch.object(monitor, "normal_group_users", return_value=(os.getgid(), (os.getuid(),))):
                self.assertEqual(monitor.reconcile(library, "test", {}), 0)
