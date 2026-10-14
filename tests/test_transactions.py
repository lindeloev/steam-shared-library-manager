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


class TransactionTests(unittest.TestCase):
    def test_interrupted_wrapper_swap_restores_previous_registration(self):
        transactions = load("transactions")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "compatibilitytools.d"
            parent.mkdir()
            target = parent / "steam-shared-library-proton"
            stage = parent / ".manager-stage-example"
            stage.mkdir()
            (stage / "proton").write_text("new wrapper")
            backup = root / ".steam-shared-library-manager/backups/wrapper-old"
            backup.mkdir(parents=True)
            (backup / "proton").write_text("previous wrapper")
            transactions.start(root, "wrapper", {"destination": str(target), "staging": str(stage), "backup": str(backup)})
            transactions.recover(root, lambda _: ("", 0))
            self.assertEqual((target / "proton").read_text(), "previous wrapper")
            self.assertFalse(stage.exists())
            self.assertEqual(len(list(backup.parent.glob("abandoned-stage-*"))), 1)

    def test_recovery_rejects_paths_outside_manager_storage(self):
        transactions = load("transactions")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Steam"
            root.mkdir()
            outside = Path(temporary) / "private"
            outside.mkdir()
            transactions.start(root, "wrapper", {"destination": str(outside), "staging": str(outside), "backup": str(outside)})
            with self.assertRaises(ValueError):
                transactions.recover(root, lambda _: ("", 0))
            self.assertTrue(outside.exists())
