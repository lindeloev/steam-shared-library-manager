"""Pure presentation helpers shared by the Tk interface and tests."""

from __future__ import annotations

import shlex
from dataclasses import dataclass


SAFE_GAME_STATES = {"native", "personal_ready", "personal_created"}
GAME_STATE_LABELS = {
    "library_not_added": "shared folder not added",
    "non_personal_override": "non-personal per-game override",
    "non_personal_default": "non-personal default tool",
    "personal_tool_not_selected": "personal tool not selected",
    "tool_missing": "wrapper not installed",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class GameTableRow:
    values: tuple[str, str, str, str, str]
    tag: str


def format_admin_request(script: str, arguments: list[str]) -> str:
    """Format an argv array transparently without implying shell evaluation."""
    return shlex.join([f"commands/{script}", *arguments])


def build_game_rows(games: list[dict[str, object]], selected: list[str]) -> list[GameTableRow]:
    """Turn status data into concise, testable game-table rows."""
    rows: list[GameTableRow] = []
    for game in games:
        states = game.get("user_states", {})
        if not isinstance(states, dict):
            states = {}
        ready = bool(selected) and all(states.get(name) in SAFE_GAME_STATES for name in selected)
        if not selected:
            readiness = "Select people in Step 1"
        elif ready:
            waiting = [name for name in selected if states.get(name) == "personal_ready"]
            readiness = (
                "Ready; prefix created on first launch: " + ", ".join(waiting)
                if waiting else "Ready"
            )
        else:
            problems = [
                f"{name}: {GAME_STATE_LABELS.get(str(states.get(name)), 'unknown')}"
                for name in selected
                if states.get(name) not in SAFE_GAME_STATES
            ]
            readiness = "Review — " + "; ".join(problems)
        if game.get("shared_prefix_exists"):
            readiness += "; shared prefix exists"

        prefix_users = game.get("prefix_users", [])
        setups = ", ".join(str(name) for name in prefix_users) if prefix_users else "—"
        rows.append(GameTableRow(
            values=(
                str(game.get("appid", "")),
                str(game.get("name", "")),
                str(game.get("platform", "Unknown")),
                readiness,
                setups,
            ),
            tag="ready_for_everyone" if ready else "needs_attention" if selected else "",
        ))
    return rows
