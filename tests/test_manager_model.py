"""Tests for presentation logic kept outside the Tk window."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from manager_model import build_game_rows, default_shared_library, format_admin_request


class ManagerModelTests(unittest.TestCase):
    def test_default_library_skips_canonical_primary_steam_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            steam_root = home / ".local/share/Steam"
            external = home / "Games/SharedSteam"
            (steam_root / "config").mkdir(parents=True)
            (steam_root / "steamapps").mkdir()
            (external / "steamapps").mkdir(parents=True)
            (home / ".steam").mkdir()
            (home / ".steam/root").symlink_to(steam_root)
            (steam_root / "config/libraryfolders.vdf").write_text(
                '"libraryfolders" {'
                f' "0" {{ "path" "{steam_root}" }}'
                f' "2" {{ "path" "{external}" }}'
                " }\n",
                encoding="utf-8",
            )

            self.assertEqual(default_shared_library(home), str(external))

    def test_admin_request_uses_unambiguous_argument_quoting(self) -> None:
        self.assertEqual(
            format_admin_request("install.sh", ["--base-proton", "/srv/Proton Experimental"]),
            "commands/install.sh --base-proton '/srv/Proton Experimental'",
        )

    def test_unlaunched_but_safe_game_is_green(self) -> None:
        rows = build_game_rows(
            [{
                "appid": "42",
                "name": "Example",
                "platform": "Windows / Proton",
                "prefix_users": ["jonas"],
                "user_states": {
                    "jonas": "personal_created",
                    "adam": "personal_ready",
                },
                "shared_prefix_exists": True,
            }],
            ["adam", "jonas"],
        )

        self.assertEqual(rows[0].tag, "ready_for_everyone")
        self.assertIn("prefix created on first launch: adam", rows[0].values[3])
        self.assertIn("shared prefix exists", rows[0].values[3])

    def test_non_personal_override_requires_attention(self) -> None:
        rows = build_game_rows(
            [{
                "appid": "42",
                "name": "Example",
                "platform": "Windows / Proton",
                "prefix_users": [],
                "user_states": {"jonas": "non_personal_override"},
                "shared_prefix_exists": False,
            }],
            ["jonas"],
        )

        self.assertEqual(rows[0].tag, "needs_attention")
        self.assertIn("non-personal per-game override", rows[0].values[3])


if __name__ == "__main__":
    unittest.main()
