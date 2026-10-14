#!/usr/bin/python3
"""Read-only, shareable tool-location audit for Steam acceptance testing."""
from __future__ import annotations
import argparse
import importlib.util
import json
import pwd
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("steam_status", HERE / "status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


def audit(library, accounts):
    tools = []
    preferences = {}
    for name, root in [*accounts, ("shared", library)]:
        if root is None:
            continue
        if name != "shared":
            preferences[name] = status.local_library_is_default(root)
        for manifest in sorted((root / "steamapps").glob("appmanifest_*.acf")):
            text = status.read_text(manifest)
            appid = status.vdf_value(text, "appid")
            label = status.vdf_value(text, "name")
            directory = status.vdf_value(text, "installdir")
            if status.is_support_tool(appid, label, directory):
                tools.append({"account": name, "appid": appid, "name": label,
                              "buildid": status.vdf_value(text, "buildid"),
                              "library": str(root), "directory": directory})
    return {"schema": 1, "remembered_local_install_destination": preferences, "tools": tools,
            "note": "Remembered destination is not proof of future dependency placement. Compare after installing/launching a shared game."}


def changes(before, after):
    old = {(x["account"], x["appid"], x["library"], x["buildid"]) for x in before.get("tools", [])}
    return [x for x in after["tools"] if (x["account"], x["appid"], x["library"], x["buildid"]) not in old]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    user = pwd.getpwuid(__import__('os').getuid())
    root = status.safe_steam_root(Path(user.pw_dir))
    result = audit(args.library, [(user.pw_name, root)])
    if args.compare:
        if args.compare.stat().st_size > 1024 * 1024:
            raise SystemExit("Audit baseline is too large")
        result["new_or_updated_tools"] = changes(json.loads(args.compare.read_text()), result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
