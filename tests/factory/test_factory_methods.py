"""Method text is a protected, pinned artifact the kernel injects per role.

Workers run --bare and load no plugins; whatever discipline they follow arrives as text in
their prompt. The manifest is validated fail-closed, the text is protected by the guard, and the
documentation no longer claims a plugin the launcher disables.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import methods as m  # noqa: E402
from factory_kernel.worker_policy import ROLE_TOOLS  # noqa: E402

GUARD = ROOT / "scripts" / "factory_security.py"
WORKER_RUNTIME = ROOT / "factory_kernel" / "worker_runtime.py"
MATT_DOC = ROOT / "docs" / "agents" / "matt-skills.md"
DOMAIN_DOC = ROOT / "docs" / "agents" / "domain.md"


def copy_methods(tmp: Path) -> Path:
    root = tmp / "repo"
    (root / ".factory").mkdir(parents=True)
    shutil.copytree(ROOT / ".factory" / "methods", root / ".factory" / "methods")
    return root


class ManifestTests(unittest.TestCase):
    def test_checked_in_manifest_loads_and_covers_the_build_roles(self):
        loaded = m.load_manifest(ROOT)
        ids = {x.id for x in loaded}
        self.assertEqual(ids, {"minimal-complexity", "deep-module-design", "tdd",
                               "diagnosing-bugs", "code-review-spec", "code-review-standards"})
        for role in ("context", "implement", "repair", "review-spec", "review-standards",
                     "investigate", "architecture"):
            self.assertTrue(m.methods_for_role(ROOT, role), role)
        for role in ("triage", "holdout", "contract-certifier"):
            self.assertEqual(m.methods_for_role(ROOT, role), ())
        for method in loaded:
            self.assertTrue(method.path.read_text(encoding="utf-8").strip())
            if method.source == "mattpocock/skills":
                self.assertEqual(method.upstream_ref, "0ab1b63a410a03d3627979a109c8695de27af954")

    def _mutate(self, tmp: Path, change) -> Path:
        root = copy_methods(tmp)
        path = root / ".factory" / "methods" / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data, root)
        path.write_text(json.dumps(data), encoding="utf-8")
        return root

    def test_unknown_role_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: d["methods"][0]["roles"].append("wizard"))
            with self.assertRaisesRegex(ValueError, "unknown role"):
                m.load_manifest(root)

    def test_missing_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: d["methods"][0].__setitem__("file", "absent.md"))
            with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                m.load_manifest(root)

    def test_empty_text_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: (r / ".factory/methods" / d["methods"][0]["file"]).write_text("  \n"))
            with self.assertRaisesRegex(ValueError, "empty"):
                m.load_manifest(root)

    def test_duplicate_id_and_duplicate_file_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: d["methods"].append(dict(d["methods"][0])))
            with self.assertRaisesRegex(ValueError, "repeats"):
                m.load_manifest(root)

    def test_path_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: d["methods"][0].__setitem__("file", "../kernel.json"))
            with self.assertRaisesRegex(ValueError, "plain name"):
                m.load_manifest(root)

    def test_unknown_source_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._mutate(Path(tmp), lambda d, r: d["methods"][0].__setitem__("source", "somewhere"))
            with self.assertRaisesRegex(ValueError, "source is unknown"):
                m.load_manifest(root)

    def test_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing"):
                m.load_manifest(Path(tmp))


class InjectionTests(unittest.TestCase):
    def test_role_block_contains_its_methods_and_not_others(self):
        block = m.method_block(ROOT, "implement")
        self.assertIn("ENGINEERING METHODS", block)
        self.assertIn("# Method: minimal complexity", block)
        self.assertIn("# Method: test-driven implementation", block)
        self.assertNotIn("# Method: diagnosing bugs", block)
        self.assertNotIn("Spec axis", block)
        self.assertEqual(m.method_block(ROOT, "triage"), "")

    def test_worker_runtime_injects_the_block_into_every_agent_prompt(self):
        source = WORKER_RUNTIME.read_text(encoding="utf-8")
        self.assertIn("method_block(", source)
        tree = ast.parse(source)
        agent = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_agent")
        calls = [n for n in ast.walk(agent) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "prompt_text"]
        self.assertEqual(len(calls), 1)
        kwargs = {k.arg for k in calls[0].keywords}
        self.assertIn("methods", kwargs)

    def test_prompt_text_places_methods_between_prompt_and_context(self):
        from factory_kernel.providers import prompt_text
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "role.md"
            p.write_text("ROLE PROMPT", encoding="utf-8")
            text = prompt_text(p, preamble="PRE", methods="METHODS", context="CTX")
        self.assertLess(text.index("PRE"), text.index("ROLE PROMPT"))
        self.assertLess(text.index("ROLE PROMPT"), text.index("METHODS"))
        self.assertLess(text.index("METHODS"), text.index("CTX"))


class ProtectionAndDocsTests(unittest.TestCase):
    def test_methods_directory_is_trust_root(self):
        spec = importlib.util.spec_from_file_location("fs", GUARD)
        fs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fs)
        self.assertTrue(fs.protected_path(".factory/methods/manifest.json"))
        self.assertTrue(fs.protected_path(".factory/methods/tdd.md"))

    @unittest.skipUnless(MATT_DOC.exists(), "repo-shaped copy without docs (mutation runner)")
    def test_docs_no_longer_claim_the_plugin_is_used_by_workers(self):
        text = MATT_DOC.read_text(encoding="utf-8")
        self.assertNotIn("uses the real", text)
        self.assertNotIn("preflight fails closed if the plugin", text)
        self.assertIn("--bare", text)
        self.assertIn(".factory/methods/manifest.json", text)

    @unittest.skipUnless(DOMAIN_DOC.exists(), "repo-shaped copy without docs (mutation runner)")
    def test_domain_doc_states_the_convention_is_dormant(self):
        text = DOMAIN_DOC.read_text(encoding="utf-8")
        self.assertIn("dormant", text)
        self.assertFalse((ROOT / "CONTEXT.md").exists())
        self.assertFalse((ROOT / "docs" / "adr").exists())

    def test_every_role_in_the_manifest_exists_in_policy(self):
        for method in m.load_manifest(ROOT):
            for role in method.roles:
                self.assertIn(role, ROLE_TOOLS)


if __name__ == "__main__":
    unittest.main()
