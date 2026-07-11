"""Tests for the AI CI/CD bootstrapper generator.

Run: python -m unittest skill/test_init.py   (from the repo root)
"""

import os
import sys
import tempfile
import textwrap
import unittest

import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import init_ai_cicd as gen
import config as engine_config  # the engine's own loader — cross-validation


def _make_node(root):
    with open(os.path.join(root, "package.json"), "w") as f:
        f.write('{"name":"demo-cli","scripts":{"test":"npm test","build":"tsc"},'
                '"dependencies":{"ink":"^4"}}')
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "index.js"), "w").close()


def _make_python(root):
    open(os.path.join(root, "pyproject.toml"), "w").close()
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "app.py"), "w").close()


def _make_go(root):
    open(os.path.join(root, "go.mod"), "w").close()
    open(os.path.join(root, "main.go"), "w").close()


class DetectionTests(unittest.TestCase):
    def test_node(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            r = gen.detect_project(d)
            self.assertEqual(r["language"], "node")
            self.assertEqual(r["test"], "npm test")
            self.assertEqual(r["source_dir"], "src")
            self.assertEqual(r["file_ext"], [".js"])
            self.assertTrue(r["security_scan"])

    def test_node_typescript(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            open(os.path.join(d, "tsconfig.json"), "w").close()
            r = gen.detect_project(d)
            self.assertEqual(r["file_ext"], [".ts"])
            self.assertEqual(r["syntax_check"], "")  # no node --check for TS

    def test_python(self):
        with tempfile.TemporaryDirectory() as d:
            _make_python(d)
            r = gen.detect_project(d)
            self.assertEqual(r["language"], "python")
            self.assertEqual(r["test"], "pytest")
            self.assertEqual(r["syntax_check"], "python -m py_compile {file}")
            self.assertFalse(r["security_scan"])

    def test_go(self):
        with tempfile.TemporaryDirectory() as d:
            _make_go(d)
            r = gen.detect_project(d)
            self.assertEqual(r["language"], "go")
            self.assertEqual(r["test"], "go test ./...")
            self.assertEqual(r["source_dir"], ".")

    def test_generic(self):
        with tempfile.TemporaryDirectory() as d:
            r = gen.detect_project(d)
            self.assertEqual(r["language"], "generic")


class ConfigRenderTests(unittest.TestCase):
    def test_generated_config_is_valid_yaml_and_engine_consumable(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            det = gen.detect_project(d)
            text = gen.render_config(det)
            # parses as YAML
            parsed = yaml.safe_load(text)
            self.assertEqual(parsed["language"], "node")
            # AND the engine's own loader accepts it
            p = os.path.join(d, "ai-cicd.yml")
            open(p, "w").write(text)
            cfg = engine_config.load_config(p)
            self.assertEqual(cfg.language, "node")
            self.assertEqual(cfg.test_cmd, "npm test")
            self.assertEqual(cfg.file_exts, (".js",))
            self.assertTrue(cfg.action_enabled("pr_review"))

    def test_python_disables_security_triage(self):
        with tempfile.TemporaryDirectory() as d:
            _make_python(d)
            cfg_text = gen.render_config(gen.detect_project(d))
            parsed = yaml.safe_load(cfg_text)
            self.assertFalse(parsed["actions"]["security_triage"])


class WorkflowRenderTests(unittest.TestCase):
    def test_all_workflows_valid_yaml_and_reference_action(self):
        det = {"name": "x", "language": "node", "test": "npm test", "build": "",
               "syntax_check": "node --check {file}", "audit": "npm audit --json",
               "source_dir": "src", "test_dir": "test", "file_ext": [".js"],
               "security_scan": True, "setup": gen._NODE_SETUP}
        wf = gen.render_workflows(det, gen.ACTION_REF)
        # node → security-scan present, 8 workflows
        self.assertIn("ai-security-scan.yml", wf)
        self.assertEqual(len(wf), 8)
        for name, content in wf.items():
            doc = yaml.safe_load(content)  # raises on malformed YAML
            self.assertIsInstance(doc, dict, name)
        # every AI workflow references the action
        for name in ("ai-pr-review.yml", "ai-auto-fix.yml", "ai-implement-issue.yml"):
            self.assertIn(gen.ACTION_REF, wf[name])

    def test_go_omits_security_scan(self):
        det = {"name": "x", "language": "go", "test": "go test ./...", "build": "go build ./...",
               "syntax_check": "", "audit": "", "source_dir": ".", "test_dir": ".",
               "file_ext": [".go"], "security_scan": False, "setup": gen._GO_SETUP}
        wf = gen.render_workflows(det, gen.ACTION_REF)
        self.assertNotIn("ai-security-scan.yml", wf)
        self.assertIn("actions/setup-go", wf["ai-auto-fix.yml"])


class GenerateEndToEndTests(unittest.TestCase):
    def test_generate_and_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            det, log = gen.generate(d, mode="reference")
            self.assertEqual(det["language"], "node")
            written = [p for s, p in log if s == "written"]
            self.assertTrue(any(p.endswith("ai-cicd.yml") for p in written))
            self.assertTrue(os.path.exists(os.path.join(d, ".github", "workflows", "ai-pr-review.yml")))

            # second run: everything already present and identical → no writes
            _, log2 = gen.generate(d, mode="reference")
            self.assertTrue(all(s in ("unchanged",) for s, _ in log2), log2)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            _, log = gen.generate(d, mode="reference", dry=True)
            self.assertTrue(all(s == "would write" for s, _ in log))
            self.assertFalse(os.path.exists(os.path.join(d, ".github")))

    def test_vendor_mode_copies_engine(self):
        with tempfile.TemporaryDirectory() as d:
            _make_node(d)
            _, log = gen.generate(d, mode="vendor")
            engine_dir = os.path.join(d, ".github", "ai-cicd-engine")
            self.assertTrue(os.path.exists(os.path.join(engine_dir, "ai_agent.py")))
            self.assertTrue(os.path.exists(os.path.join(engine_dir, "config.py")))
            self.assertFalse(os.path.exists(os.path.join(engine_dir, "test_engine.py")))


if __name__ == "__main__":
    unittest.main()
