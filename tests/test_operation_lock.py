import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("lock", Path(__file__).resolve().parents[1] / "commands/operation-lock.py")
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


class AccountLockTests(unittest.TestCase):
    def test_second_writer_is_rejected_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operation.lock"
            with lock.account_lock(path):
                with self.assertRaisesRegex(ValueError, "Another setup"):
                    with lock.account_lock(path):
                        self.fail("Second writer acquired the lock")
            with lock.account_lock(path):
                pass

    def test_symlinked_lock_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "private"
            target.write_text("private")
            link = root / "operation.lock"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                with lock.account_lock(link):
                    pass
            self.assertEqual(target.read_text(), "private")
