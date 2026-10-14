"""Safety and ACL regression tests use only temporary files owned by the tester."""
import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_backend():
    spec = importlib.util.spec_from_file_location("library_backend", ROOT / "commands/library-backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LibraryBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = load_backend()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.library = self.base / "games"
        (self.library / "steamapps").mkdir(parents=True)

    def repair(self):
        fd = self.backend.open_directory(self.library)
        try:
            return self.backend.repair_tree(fd, os.getgid(), users=(os.getuid(),))
        finally:
            os.close(fd)

    def test_refuses_unrelated_data_and_protected_locations(self):
        (self.library / "private-notes").write_text("private")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            self.backend.validate_library(self.library)
        for path in ("/etc/steam-library", "/var/steam-library", str(Path.home()), "/srv"):
            with self.assertRaises(ValueError):
                self.backend.validate_library(Path(path), existing=False)

    def test_repair_does_not_follow_external_symlinks(self):
        external = self.base / "private"
        external.write_text("private")
        external.chmod(0o600)
        (self.library / "steamapps/escape").symlink_to(external)
        self.repair()
        self.assertEqual(external.stat().st_mode & 0o777, 0o600)
        self.assertEqual(external.read_text(), "private")

    def test_repair_rejects_hardlinked_files(self):
        external = self.base / "private"
        external.write_text("private")
        external.chmod(0o600)
        os.link(external, self.library / "steamapps/hardlink")
        with self.assertRaisesRegex(ValueError, "multiply linked"):
            self.repair()
        self.assertEqual(external.stat().st_mode & 0o777, 0o600)

    def test_refuses_symlinked_steamapps(self):
        (self.library / "steamapps").rmdir()
        outside = self.base / "outside"
        outside.mkdir()
        (self.library / "steamapps").symlink_to(outside)
        with self.assertRaises(OSError):
            self.backend.validate_library(self.library)

    def test_privileged_repair_rejects_service_owned_regular_files(self):
        file = self.library / "steamapps/private"
        file.write_text("private service data")
        file.chmod(0o600)
        fd = os.open(file, os.O_RDONLY)
        try:
            with mock.patch.object(self.backend.os, "geteuid", return_value=0), mock.patch.object(self.backend.pwd, "getpwuid", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_shell="/usr/sbin/nologin")):
                with self.assertRaisesRegex(ValueError, "service"):
                    self.backend.repair_tree(fd, os.getgid())
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        finally:
            os.close(fd)

    def test_membership_change_removes_managed_named_acl(self):
        path = self.library / "steamapps/file"
        path.write_bytes(b'game')
        fd = os.open(path, os.O_RDONLY)
        try:
            acl = self.backend.ACL()
            removed_uid = os.getuid() + 123
            acl.grant(fd, os.getgid(), (removed_uid,))
            before = acl.lib.acl_get_fd(fd)
            count = acl.lib.acl_entries(before)
            acl.lib.acl_free(before)
            acl.grant(fd, os.getgid(), (), remove_users=(removed_uid,))
            after = acl.lib.acl_get_fd(fd)
            self.assertEqual(acl.lib.acl_entries(after), count - 1)
            acl.lib.acl_free(after)
        finally:
            os.close(fd)

    def test_access_and_defaults_include_named_user(self):
        self.repair()
        game = self.library / "steamapps/new-game"
        game.mkdir()
        fd = os.open(game, os.O_RDONLY | os.O_DIRECTORY)
        try:
            acl = self.backend.ACL()
            pointer = acl.lib.acl_get_fd(fd)
            self.assertGreater(acl.lib.acl_entries(pointer), 3)
            acl.lib.acl_free(pointer)
        finally:
            os.close(fd)
