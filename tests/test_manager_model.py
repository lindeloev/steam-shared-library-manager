"""Tests for presentation logic kept outside the Tk window."""

from __future__ import annotations

import unittest

from manager_model import build_game_rows, format_admin_request


class ManagerModelTests(unittest.TestCase):
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
