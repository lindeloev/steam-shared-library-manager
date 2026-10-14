import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("manifest", Path(__file__).resolve().parents[1] / "commands/manifest-helper.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ManifestTests(unittest.TestCase):
    def test_generation_refuses_extra_commands_and_bad_dependencies(self):
        for text in ('"manifest" { "version" "2" "commandline" "/other-program" }',
                     '"manifest" { "version" "2" "commandline" "/proton %verb%" "require_tool_appid" "not-an-id" }',
                     '"manifest" { "version" "2" "commandline" "/proton %verb%" "new_launch_command" "anything" }'):
            with self.assertRaises(ValueError):
                helper.generate(text)

    def test_runtime_change_refreshes_manifest_and_requires_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / "Steam"
            tool = root / "compatibilitytools.d/personal"
            proton = root / "steamapps/common/Proton - Experimental"
            tool.mkdir(parents=True)
            proton.mkdir(parents=True)
            text = '"manifest" { "version" "2" "commandline" "/proton %verb%" "require_tool_appid" "1628350" }'
            (tool / "base-proton.conf").write_text(f"BASE_PROTON={proton}\nPREFIX_ROOT={home}/private\n")
            (tool / "toolmanifest.vdf").write_text(helper.generate(text))
            (proton / "toolmanifest.vdf").write_text(text.replace("1628350", "4183110"))
            with mock.patch.object(Path, "home", return_value=home), mock.patch.object(helper, "steam_identity", return_value={"100": "original-start"}) as identity:
                self.assertTrue(helper.refresh(tool))
                self.assertTrue(helper.refresh(tool))  # A retry is not a restart.
                identity.return_value = {"101": "new-start"}
                self.assertFalse(helper.refresh(tool))
            self.assertIn('"4183110"', (tool / "toolmanifest.vdf").read_text())
