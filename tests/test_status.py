"""Tests for read-only status classification and path containment."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_status():
    path = ROOT / "commands/status.py"
    spec = importlib.util.spec_from_file_location("shared_library_status", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.status = load_status()

    def test_safe_steam_root_must_resolve_below_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            safe_root = home / ".local/share/Steam"
            safe_root.mkdir(parents=True)
            (home / ".steam").mkdir()
            link = home / ".steam/root"
            link.symlink_to(safe_root)
            self.assertEqual(self.status.safe_steam_root(home), safe_root)

            link.unlink()
            outside = root / "outside"
            outside.mkdir()
            link.symlink_to(outside)
            self.assertIsNone(self.status.safe_steam_root(home))

    def test_safe_existing_descendant_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inside = root / "inside"
            outside = root.parent / (root.name + "-outside")
            inside.mkdir()
            outside.mkdir()
            try:
                self.assertEqual(self.status.safe_existing_descendant(root, inside), inside)
                link = root / "escape"
                link.symlink_to(outside)
                self.assertIsNone(self.status.safe_existing_descendant(root, link))
            finally:
                outside.rmdir()

    def test_library_kind_distinguishes_safe_setup_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "library"
            self.assertEqual(self.status.library_kind(library), "missing")
            library.mkdir()
            self.assertEqual(self.status.library_kind(library), "empty_directory")
            (library / "steamapps").mkdir()
            self.assertEqual(self.status.library_kind(library), "steam_library")

    def test_compatibility_mappings_distinguish_default_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / "config.vdf").write_text(
                '"CompatToolMapping"\n'
                "{\n"
                '  "42" { "name" "proton_experimental" "config" "" }\n'
                '  "0" { "name" "steam-shared-library-proton" "config" "" }\n'
                "}\n",
                encoding="utf-8",
            )

            mappings = self.status.compat_tool_mappings(config)

            self.assertEqual(mappings["0"], self.status.TOOL_ID)
            self.assertEqual(mappings["42"], "proton_experimental")
            self.assertTrue(self.status.personal_tool_selected(config))

    def test_registered_library_indexes_follow_top_level_folder_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n'
                "{\n"
                '\t"0" { "path" "/home/person/.steam/root" "apps" { "1" "2" } }\n'
                '\t"4" { "path" "/srv/SteamLibrary" "apps" { "42" "100" } }\n'
                "}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                self.status.registered_library_indexes(config),
                {"/srv/SteamLibrary": 4},
            )

    def test_primary_library_index_is_never_reported_as_shared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / "libraryfolders.vdf").write_text(
                '"libraryfolders" {'
                ' "0" { "path" "/home/person/.local/share/Steam" }'
                ' "1" { "path" "/srv/SteamLibrary" }'
                " }\n",
                encoding="utf-8",
            )

            self.assertEqual(
                self.status.registered_libraries(config),
                {"/srv/SteamLibrary"},
            )

    def test_library_default_requires_each_initialized_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            steam_root = Path(temporary)
            config = steam_root / "config"
            config.mkdir()
            (config / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n'
                '\t"0" { "path" "/home/person/.steam/root" }\n'
                '\t"2" { "path" "/srv/SteamLibrary" }\n'
                "}\n",
                encoding="utf-8",
            )
            account_config = steam_root / "userdata/123/config"
            account_config.mkdir(parents=True)
            localconfig = account_config / "localconfig.vdf"
            localconfig.write_text(
                '"UserLocalConfigStore" { "LastInstallFolderIndex" "2" }\n',
                encoding="utf-8",
            )

            self.assertTrue(
                self.status.library_is_default(
                    steam_root, config, Path("/srv/SteamLibrary")
                )
            )
            localconfig.write_text(
                '"UserLocalConfigStore" { "LastInstallFolderIndex" "0" }\n',
                encoding="utf-8",
            )
            self.assertFalse(
                self.status.library_is_default(
                    steam_root, config, Path("/srv/SteamLibrary")
                )
            )

    def test_game_readiness_does_not_require_an_existing_prefix(self) -> None:
        user = {
            "library_registered": True,
            "tool_installed": True,
            "compat_tool_mappings": {"0": self.status.TOOL_ID},
            "prefixes": [],
        }
        self.assertEqual(
            self.status.game_user_state(user, "42", "windows"),
            "personal_ready",
        )

        user["prefixes"] = ["42"]
        self.assertEqual(
            self.status.game_user_state(user, "42", "windows"),
            "personal_created",
        )

        user["compat_tool_mappings"] = {
            "0": self.status.TOOL_ID,
            "42": "proton_experimental",
        }
        self.assertEqual(
            self.status.game_user_state(user, "42", "windows"),
            "non_personal_override",
        )

    def test_mixed_install_defaults_to_native_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary)
            linux_launcher = game / "game_linux"
            linux_launcher.write_bytes(b"\x7fELF")
            linux_launcher.chmod(0o755)
            (game / "game.exe").write_bytes(b"MZ")
            kind = self.status.platform_kind(game)

            self.assertEqual(kind, "mixed")
            self.assertEqual(
                self.status.platform_label(kind),
                "Native Linux + Windows files",
            )
            self.assertEqual(
                self.status.game_user_state(
                    {
                        "library_registered": True,
                        "tool_installed": False,
                        "compat_tool_mappings": {},
                        "prefixes": [],
                    },
                    "42",
                    kind,
                ),
                "native",
            )

    def test_shared_library_is_not_mistaken_for_native_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary)
            shared_object = game / "libsupport.so"
            shared_object.write_bytes(b"\x7fELF")
            shared_object.chmod(0o755)
            (game / "game.exe").write_bytes(b"MZ")

            self.assertEqual(self.status.platform_kind(game), "windows")

    def test_runtime_manifests_are_not_reported_as_games(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary)
            steamapps = library / "steamapps"
            steamapps.mkdir()
            (steamapps / "appmanifest_1161040.acf").write_text(
                '"AppState" { "appid" "1161040" "name" "Proton BattlEye Runtime" '
                '"installdir" "Proton BattlEye Runtime" }\n',
                encoding="utf-8",
            )
            (steamapps / "appmanifest_42.acf").write_text(
                '"AppState" { "appid" "42" "name" "Example Game" '
                '"installdir" "Example Game" }\n',
                encoding="utf-8",
            )
            game = steamapps / "common/Example Game"
            game.mkdir(parents=True)
            (game / "game.exe").write_bytes(b"MZ")
            (steamapps / "compatdata/42").mkdir(parents=True)

            report = self.status.games(library, [])

            self.assertEqual([entry["appid"] for entry in report], ["42"])
            self.assertEqual(report[0]["platform"], "Windows / Proton")
            self.assertTrue(report[0]["shared_prefix_exists"])


if __name__ == "__main__":
    unittest.main()
