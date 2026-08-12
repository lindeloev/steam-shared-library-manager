"""Behavior tests for the installed Proton wrapper."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProtonWrapperTests(unittest.TestCase):
    def test_app_id_is_found_from_game_executable_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool"
            base_proton = root / "base-proton"
            data_home = root / "data"
            steamapps = root / "Shared Library/steamapps"
            game = steamapps / "common/Example Game/Binaries/Win64/Game.exe"
            runtime_working_directory = root / "runtime"
            tool.mkdir()
            base_proton.mkdir()
            game.parent.mkdir(parents=True)
            runtime_working_directory.mkdir()
            (steamapps / "appmanifest_4242.acf").write_text(
                '"AppState" { "appid" "4242" "installdir" "Example Game" }\n',
                encoding="utf-8",
            )

            wrapper = tool / "proton"
            shutil.copy2(ROOT / "commands/proton", wrapper)
            wrapper.chmod(0o755)
            delegated = base_proton / "proton"
            delegated.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            delegated.chmod(0o755)
            (tool / "base-proton.conf").write_text(
                f"BASE_PROTON={base_proton}\n",
                encoding="utf-8",
            )

            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"SteamGameId", "SteamGameID", "SteamAppId", "SteamAppID", "STEAM_APPID"}
            }
            environment.update({"HOME": str(root / "home"), "XDG_DATA_HOME": str(data_home)})
            result = subprocess.run(
                [str(wrapper), "waitforexitandrun", str(game)],
                cwd=runtime_working_directory,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((data_home / "steam-shared-library-proton/4242").is_dir())

    def test_existing_prefix_permissions_are_made_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool"
            base_proton = root / "base-proton"
            data_home = root / "data"
            tool.mkdir()
            base_proton.mkdir()

            wrapper = tool / "proton"
            shutil.copy2(ROOT / "commands/proton", wrapper)
            wrapper.chmod(0o755)
            delegated = base_proton / "proton"
            delegated.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            delegated.chmod(0o755)
            (tool / "base-proton.conf").write_text(
                f"BASE_PROTON={base_proton}\n",
                encoding="utf-8",
            )

            prefix_root = data_home / "steam-shared-library-proton"
            app_prefix = prefix_root / "1234"
            app_prefix.mkdir(parents=True)
            prefix_root.chmod(0o775)
            app_prefix.chmod(0o775)

            environment = {
                **os.environ,
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(data_home),
                "SteamGameId": "1234",
            }
            result = subprocess.run(
                [str(wrapper), "run"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(prefix_root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(app_prefix.stat().st_mode), 0o700)

    def test_relative_data_home_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool"
            base_proton = root / "base-proton"
            tool.mkdir()
            base_proton.mkdir()
            wrapper = tool / "proton"
            shutil.copy2(ROOT / "commands/proton", wrapper)
            wrapper.chmod(0o755)
            delegated = base_proton / "proton"
            delegated.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            delegated.chmod(0o755)
            (tool / "base-proton.conf").write_text(
                f"BASE_PROTON={base_proton}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [str(wrapper), "run"],
                cwd=root,
                env={
                    **os.environ,
                    "HOME": str(root / "home"),
                    "XDG_DATA_HOME": "relative-data",
                    "SteamGameId": "1234",
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("XDG_DATA_HOME must be an absolute path", result.stderr)
            self.assertFalse((root / "relative-data").exists())


if __name__ == "__main__":
    unittest.main()
