import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("vdf", Path(__file__).resolve().parents[1] / "commands/vdf.py")
vdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vdf)


class VDFTests(unittest.TestCase):
    def test_mapping_edit_preserves_comments_escaped_braces_and_pinned_versions(self):
        original = '// opening {\n"Steam" { "note" "quoted \\\"}\\\"" "CompatToolMapping" { "42" { "name" "proton_90" } } }'
        updated = vdf.set_mapping(original, "0", "steam-shared-library-proton")
        self.assertTrue(updated.startswith('// opening {'))
        self.assertEqual(vdf.mappings(updated)["42"]["name"], "proton_90")
        self.assertEqual(vdf.mappings(updated)["0"]["name"], "steam-shared-library-proton")
        self.assertIn('"note" "quoted \\\"}\\\""', updated)

    def test_malformed_or_duplicate_mapping_is_rejected(self):
        for text in ('"Steam" {', '"Steam" { "CompatToolMapping" { "0" {} "0" {} } }'):
            with self.assertRaises(ValueError):
                vdf.mappings(text)

    def test_restore_uses_previous_entry_without_overwriting_other_choices(self):
        text = '"Steam" { "CompatToolMapping" { "0" { "name" "steam-shared-library-proton" } "42" { "name" "proton_90" } } }'
        result = vdf.set_mapping(text, "0", "", source='"0" { "name" "proton_experimental" "priority" "75" }')
        self.assertEqual(vdf.mappings(result)["0"]["name"], "proton_experimental")
        self.assertEqual(vdf.mappings(result)["42"]["name"], "proton_90")

    def test_recovery_cannot_restore_another_apps_entry(self):
        text = '"Steam" { "CompatToolMapping" { "42" { "name" "steam-shared-library-proton" } } }'
        with self.assertRaises(ValueError):
            vdf.set_mapping(text, "42", "", source='"0" { "name" "proton_experimental" }')
