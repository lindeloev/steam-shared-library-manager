"""Tests for the trusted privileged Proton SteamPipe repair."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_repair():
    path = ROOT / "commands/repair-proton-fixups.py"
    spec = importlib.util.spec_from_file_location("repair_proton_fixups", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtonFixupRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repair = load_repair()

    def make_proton(self, root: Path) -> tuple[Path, Path]:
        library = root / "library"
        proton = library / "steamapps/common/Proton - Experimental"
        (proton / "files").mkdir(parents=True)
        launcher = proton / "proton"
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher.chmod(0o755)
        return library, proton

    def test_restore_applies_read_only_mode_and_exact_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _library, proton = self.make_proton(Path(temporary))
            target = proton / "files/lib/example.dll"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"example")
            target.chmod(0o664)
            manifest = proton / "steampipe_fixups.json"
            manifest.write_text(
                json.dumps({"empty_dirs": ["files/missing/empty"], "no_write_paths": ["files/lib/example.dll"]}),
                encoding="utf-8",
            )

            changed = self.repair.restore(proton)

            self.assertEqual(changed, 1)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)
            self.assertTrue((proton / "files/missing/empty").is_dir())
            self.assertEqual(
                (proton / "files/steampipe_fixups_mtime").read_text(encoding="utf-8"),
                str(os.path.getmtime(manifest)) + "\n",
            )
            self.assertTrue((proton / "files/steampipe_fixups_mtime").stat().st_mode & stat.S_IWGRP)

    def test_restore_rejects_manifest_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _library, proton = self.make_proton(Path(temporary))
            (proton / "steampipe_fixups.json").write_text(
                json.dumps({"empty_dirs": [], "no_write_paths": ["../outside"]}),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                self.repair.restore(proton)

    def test_validation_rejects_symlinked_proton_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library, proton = self.make_proton(root)
            link = proton.with_name("Proton Link")
            link.symlink_to(proton, target_is_directory=True)

            with self.assertRaises(SystemExit):
                self.repair.validated_proton(library, link)


if __name__ == "__main__":
    unittest.main()
