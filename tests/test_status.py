"""Tests for read-only status classification and path containment."""

from __future__ import annotations

import importlib.util
import os
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

    def test_local_default_checks_every_account_and_ignores_nested_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for account in ("123", "456"):
                config = root / f"userdata/{account}/config/localconfig.vdf"
                config.parent.mkdir(parents=True)
                config.write_text('"UserLocalConfigStore" { "nested" { "LastInstallFolderIndex" "9" } "LastInstallFolderIndex" "0" }')
            # A nested key must not shadow the actual top-level default.
            self.assertTrue(self.status.local_library_is_default(root))
            config.write_text('"UserLocalConfigStore" { "LastInstallFolderIndex" "1" }')
            self.assertFalse(self.status.local_library_is_default(root))

    def test_future_tools_are_flagged_and_excluded_from_games(self) -> None:
        self.assertFalse(self.status.is_support_tool("999998", "Proton Pulse", "Proton Pulse"))
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary)
            steamapps = library / "steamapps"
            (steamapps / "common/SteamLinuxRuntime_future").mkdir(parents=True)
            (steamapps / "common/SteamLinuxRuntime_future/runtime").write_text("package")
            (steamapps / "appmanifest_999999.acf").write_text(
                '"AppState" { "appid" "999999" "name" "Proton 99.0" "installdir" "Proton 99.0" }'
            )
            self.assertEqual(self.status.shared_support_tools(library), ["Proton 99.0", "SteamLinuxRuntime_future"])
            self.assertEqual(self.status.games(library, []), [])

    def test_unregistered_runtime_cache_remnants_do_not_block_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary)
            common = library / "steamapps/common"
            cache = common / "SteamLinuxRuntime_sniper/var"
            cache.mkdir(parents=True)
            (cache / "generated-cache").write_text("cache")
            (common / "SteamLinuxRuntime_soldier").mkdir()
            self.assertEqual(self.status.shared_support_tools(library), [])
            # A manifest still represents a registered installation, even when
            # its package is missing; a real package also remains detectable.
            manifest = library / "steamapps/appmanifest_1628350.acf"
            manifest.write_text('"AppState" { "appid" "1628350" "name" "Steam Linux Runtime 3.0" "installdir" "SteamLinuxRuntime_sniper" }')
            self.assertEqual(self.status.shared_support_tools(library), ["Steam Linux Runtime 3.0"])
            manifest.unlink()
            (cache.parent / "runtime").write_text("package")
            self.assertEqual(self.status.shared_support_tools(library), ["SteamLinuxRuntime_sniper"])

    def test_runtime_cache_detection_does_not_ignore_links_or_proton_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary)
            common = library / "steamapps/common"
            common.mkdir(parents=True)
            (common / "outside").mkdir()
            (common / "SteamLinuxRuntime_link").symlink_to(common / "outside")
            runtime = common / "SteamLinuxRuntime_sniper"
            runtime.mkdir()
            (runtime / "var").symlink_to(common / "outside")
            (common / "Proton Hotfix").mkdir()
            self.assertEqual(self.status.shared_support_tools(library),
                             ["Proton Hotfix", "SteamLinuxRuntime_link", "SteamLinuxRuntime_sniper"])

    def test_status_reader_does_not_follow_links_or_block_on_fifos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.write_text("private configuration")
            link = root / "config.vdf"
            link.symlink_to(private)
            self.assertEqual(self.status.read_text(link), "")
            fifo = root / "appmanifest_42.acf"
            __import__("os").mkfifo(fifo)
            self.assertEqual(self.status.read_text(fifo), "")

    def test_local_wrapper_rejects_missing_runtime_and_stale_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proton = root / "steamapps/common/Proton - Experimental"
            tool = root / "compatibilitytools.d" / self.status.TOOL_ID
            proton.mkdir(parents=True)
            tool.mkdir(parents=True)
            manifest = '"manifest" { "commandline" "/proton %verb%" "require_tool_appid" "1628350" }'
            (proton / "toolmanifest.vdf").write_text(manifest)
            (tool / "toolmanifest.vdf").write_text(manifest)
            (tool / "base-proton.conf").write_text(f"BASE_PROTON={proton}\n")
            (tool / "proton").write_bytes((ROOT / "commands/proton").read_bytes())
            (tool / "proton").chmod(0o755)
            ready, problems = self.status.wrapper_health(root, tool, [str(proton)])
            self.assertFalse(ready)
            self.assertTrue(any("runtime" in problem for problem in problems))
            runtime = root / "steamapps/common/SteamLinuxRuntime_sniper"
            runtime.mkdir()
            (root / "steamapps/appmanifest_1628350.acf").write_text('"AppState" { "appid" "1628350" "StateFlags" "4" "installdir" "SteamLinuxRuntime_sniper" }')
            self.assertTrue(self.status.wrapper_health(root, tool, [str(proton)])[0])
            (proton / "toolmanifest.vdf").write_text(manifest.replace("1628350", "4183110"))
            self.assertFalse(self.status.wrapper_health(root, tool, [str(proton)])[0])

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

    def test_mixed_install_requires_launch_verification_without_override(self) -> None:
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
                "launch_mode_unknown",
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

    def test_proton_fixup_status_uses_exact_python_mtime_string(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proton = Path(temporary) / "Proton - Experimental"
            marker = proton / "files/steampipe_fixups_mtime"
            marker.parent.mkdir(parents=True)
            manifest = proton / "steampipe_fixups.json"
            manifest.write_text('{}\n', encoding="utf-8")
            exact = str(os.path.getmtime(manifest))

            marker.write_text(str(int(os.path.getmtime(manifest))) + "\n", encoding="utf-8")
            self.assertTrue(self.status.proton_fixup_status(proton)["pending"])

            marker.write_text(exact + "\n", encoding="utf-8")
            report = self.status.proton_fixup_status(proton)
            self.assertFalse(report["pending"])
            self.assertEqual(report["expected_mtime"], exact)


if __name__ == "__main__":
    unittest.main()
