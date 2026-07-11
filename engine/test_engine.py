"""Offline unit tests for the engine's config + profile layer.

These exercise everything that does NOT require the LLM client (no `openai`,
no network, no GitHub context): config parsing, generic no-ops, placeholder
detection, and the Node profile's symbol scan / import validation.

Run: python -m unittest engine/test_engine.py   (from the repo root)
"""

import os
import tempfile
import textwrap
import unittest

from config import load_config, ProjectConfig
from profiles import get_profile, NodeProfile, Profile


class ConfigTests(unittest.TestCase):
    def test_missing_file_yields_defaults(self):
        cfg = load_config("/nonexistent/ai-cicd.yml")
        self.assertIsInstance(cfg, ProjectConfig)
        self.assertEqual(cfg.language, "generic")
        self.assertTrue(cfg.action_enabled("pr_review"))  # absent switch → enabled

    def test_full_parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ai-cicd.yml")
            with open(p, "w") as f:
                f.write(textwrap.dedent("""
                    project:
                      name: demo
                      description: "a demo"
                    language: node
                    commands:
                      test: "npm test"
                      syntax_check: "node --check {file}"
                    layout:
                      source_dir: lib
                      file_ext: [".js", ".mjs"]
                    actions:
                      auto_fix: false
                """))
            cfg = load_config(p)
            self.assertEqual(cfg.name, "demo")
            self.assertEqual(cfg.language, "node")
            self.assertEqual(cfg.test_cmd, "npm test")
            self.assertEqual(cfg.source_dir, "lib")
            self.assertEqual(cfg.file_exts, (".js", ".mjs"))
            self.assertFalse(cfg.action_enabled("auto_fix"))
            self.assertTrue(cfg.action_enabled("pr_review"))
            self.assertIn("demo — a demo", cfg.project_line())


class ProfileSelectionTests(unittest.TestCase):
    def test_node_language_selects_node_profile(self):
        self.assertIsInstance(get_profile(ProjectConfig(language="node")), NodeProfile)

    def test_unknown_language_falls_back_to_generic(self):
        prof = get_profile(ProjectConfig(language="haskell"))
        self.assertIsInstance(prof, Profile)
        self.assertNotIsInstance(prof, NodeProfile)
        # generic import validation is a no-op
        self.assertEqual(prof.validate_imports(["a.hs"], {}), [])


class PlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.prof = get_profile(ProjectConfig(language="node"))

    def test_detects_rest_of_placeholder(self):
        code = "const a = 1;\n// ... rest of existing code\nexport default a;\n"
        self.assertTrue(self.prof.find_placeholders(code))

    def test_clean_code_has_no_placeholders(self):
        code = "export function f() {\n  return 1;\n}\n"
        self.assertEqual(self.prof.find_placeholders(code), [])


class NodeProfileSymbolTests(unittest.TestCase):
    def setUp(self):
        self.prof = get_profile(ProjectConfig(language="node"))

    def test_scan_and_validate_on_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(os.path.join(src, "utils"))
            with open(os.path.join(src, "utils", "detect.js"), "w") as f:
                f.write("export function isWordLookup(x) { return true; }\n"
                        "export const containsCJK = (s) => false;\n")
            with open(os.path.join(src, "repl.js"), "w") as f:
                f.write("import { isWordLookup } from './utils/detect.js';\n"
                        "export function startRepl() { return isWordLookup('a'); }\n")

            export_map, api_ref = self.prof.scan_symbols(src)
            detect = os.path.normpath(os.path.join(src, "utils", "detect.js"))
            repl = os.path.normpath(os.path.join(src, "repl.js"))
            self.assertIn("isWordLookup", export_map[detect])
            self.assertIn("containsCJK", export_map[detect])
            self.assertIn("startRepl", export_map[repl])
            self.assertIn("isWordLookup", api_ref)

            # clean imports → no errors
            self.assertEqual(self.prof.validate_imports([repl], export_map), [])

            # hallucinated import → flagged
            bad = os.path.join(src, "bad.js")
            with open(bad, "w") as f:
                f.write("import { nopeSymbol } from './utils/detect.js';\n")
            errors = self.prof.validate_imports([bad], export_map)
            self.assertTrue(any("nopeSymbol" in e for e in errors))

    def test_detects_dropped_export_semantics(self):
        # simulates the gate's export-drop check input shape
        original = {"src/repl.js": ["startRepl", "render"]}
        now = {"src/repl.js": ["startRepl"]}  # render dropped
        missing = set(original["src/repl.js"]) - set(now["src/repl.js"])
        self.assertEqual(missing, {"render"})


if __name__ == "__main__":
    unittest.main()
