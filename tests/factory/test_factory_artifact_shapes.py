"""Every worker-written list accepts both spellings and compiles to one canonical form.

Canary attempts 7 and 8 died at the governor and context gates because the worker wrote list
entries as objects (`{"path": ..., "why": ...}`, `{"id": ..., "verdict": ...}`) where the
compiler wanted plain strings. The content was correct both times. The refused raw artifacts
are checked in below and must compile to exactly what the plain-string spelling compiles to.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FIX = ROOT / "tests" / "factory" / "fixtures" / "context"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shapes = load_script("factory_shapes")
protocol = load_script("factory_protocol")
artifacts = load_script("factory_artifacts")
architecture = load_script("factory_architecture")


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def die(message: str) -> None:
    raise SystemExit(message)


# validate_context and compile_design check that every context file exists on disk. The
# mutation runner's repo-shaped copy carries no product tree, so the fixture-driven tests
# that need the real files are skipped there; the normaliser and governor tests still run.
PRODUCT_TREE = (ROOT / "app" / "frontend" / "src" / "lib" / "exportMarkdown.ts").exists()
needs_product_tree = unittest.skipUnless(PRODUCT_TREE, "repo-shaped copy without the product tree (mutation runner)")


class NormaliserTests(unittest.TestCase):
    """Pure normaliser behaviour, plus the two validators driven on synthetic data so the
    mutation runner's repo-shaped copy (no product tree) still detects the wiring."""

    def test_validate_context_wiring_on_synthetic_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "synthetic_shape_probe.txt"
            (ROOT / rel).write_text("probe\n", encoding="utf-8")
            try:
                contract = {"version": "2.0", "issue": {"number": 1, "title": "t"}, "summary": "a summary long enough",
                            "behaviors": [{"id": "AC-1", "given": "g", "when": "w", "then": "t", "seam": "s"}],
                            "invariants": [], "out_of_scope": [], "risks": [], "ambiguities": []}
                h = protocol.validate_contract(contract)
                raw = {"version": "1.0", "contract_sha256": h, "files": [{"path": rel, "why": "x"}],
                       "symbols": [{"name": "s"}], "callers": [], "tests": [{"name": rel}], "invariants": [{"why": "i"}],
                       "adrs": [], "history": [], "notes": "ignored"}
                got = protocol.validate_context(raw, h)
                self.assertEqual(got["files"], [rel]); self.assertEqual(got["symbols"], ["s"])
                self.assertEqual(got["tests"], [rel]); self.assertEqual(got["invariants"], ["i"])
                self.assertNotIn("notes", got)
                # The impact compiler is the program that actually died on attempt 8; drive it too.
                impact = load_script("factory_impact")
                impact.run_ts = lambda payload: {"symbols": [], "callers": [], "tests": []}
                inp = Path(tmp) / "context.raw.json"; out = Path(tmp) / "context.enriched.json"
                inp.write_text(json.dumps(raw), encoding="utf-8")
                impact.context_mode(argparse.Namespace(input=str(inp), output=str(out)))
                enriched = json.loads(out.read_text(encoding="utf-8"))
                self.assertIn(rel, enriched["files"])
                self.assertTrue(all(isinstance(x, str) for x in enriched["files"]))
            finally:
                (ROOT / rel).unlink()

    def test_compile_design_wiring_on_synthetic_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "synthetic_shape_probe2.txt"
            (ROOT / rel).write_text("probe\n", encoding="utf-8")
            try:
                contract = {"version": "2.0", "issue": {"number": 1, "title": "t"}, "summary": "a summary long enough",
                            "behaviors": [{"id": "AC-1", "given": "g", "when": "w", "then": "t", "seam": "s"}],
                            "invariants": [], "out_of_scope": [], "risks": [], "ambiguities": []}
                h = protocol.validate_contract(contract)
                context = protocol.validate_context({"version": "1.0", "contract_sha256": h, "files": [rel], "symbols": [],
                                                     "callers": [], "tests": [], "invariants": [], "adrs": [], "history": []}, h)
                root = Path(tmp)
                (root / "c.json").write_text(json.dumps(contract), encoding="utf-8")
                (root / "ctx.json").write_text(protocol.canonical(context).decode() if isinstance(protocol.canonical(context), bytes) else protocol.canonical(context), encoding="utf-8")
                raw = {"version": "1.0", "modules": [{"name": "m", "why": "x"}], "seams": [{"name": "s"}],
                       "public_interfaces": [{"name": "p()"}], "invariants": [{"text": "i"}], "data_flows": [{"name": "d"}],
                       "ac_mapping": {"AC-1": "s"}, "planned_files": [{"path": rel}], "allowed_new_files": [], "notes": "ignored"}
                (root / "d.raw.json").write_text(json.dumps(raw), encoding="utf-8")
                artifacts.compile_design(argparse.Namespace(input=str(root / "d.raw.json"), contract=str(root / "c.json"),
                                                            context=str(root / "ctx.json"), output=str(root / "d.json")))
                design = json.loads((root / "d.json").read_text(encoding="utf-8"))
                self.assertEqual(design["modules"], ["m"]); self.assertEqual(design["seams"], ["s"])
                self.assertEqual(design["ac_mapping"], {"AC-1": ["s"]}); self.assertEqual(design["planned_files"], [rel])
            finally:
                (ROOT / rel).unlink()

    def test_strings_pass_through_unchanged(self):
        self.assertEqual(shapes.normalise_list(["a", "b"], "files", "ctx files", die), ["a", "b"])

    def test_objects_with_canonical_key_become_strings(self):
        value = [{"path": "a.py", "why": "x"}, "b.py"]
        self.assertEqual(shapes.normalise_list(value, "files", "ctx files", die), ["a.py", "b.py"])
        ids = [{"id": "ARCH-1", "verdict": "complies", "notes": "..."}]
        self.assertEqual(shapes.normalise_list(ids, "principles", "gov principles", die), ["ARCH-1"])
        prose = [{"text": "one"}, {"why": "two"}]
        self.assertEqual(shapes.normalise_list(prose, "invariants", "ctx invariants", die), ["one", "two"])

    def test_explicit_applicable_false_is_an_omission(self):
        value = [{"id": "A", "applicable": True}, {"id": "B", "applicable": False}, "C"]
        self.assertEqual(shapes.normalise_list(value, "principles", "gov principles", die), ["A", "C"])
        # only a boolean false omits; other values leave the entry in for the validator to judge
        self.assertEqual(shapes.normalise_list([{"id": "B", "applicable": "no"}], "principles", "x", die), ["B"])
        self.assertEqual(shapes.normalise_list([{"id": "B", "applicable": 0}], "principles", "x", die), ["B"])

    def test_object_without_canonical_key_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            shapes.normalise_list([{"why": "no path"}], "files", "ctx files", die)
        self.assertIn("lacks its canonical key 'path'", str(ctx.exception))

    def test_non_string_value_refused(self):
        with self.assertRaises(SystemExit):
            shapes.normalise_list([{"path": 3}], "files", "ctx files", die)
        with self.assertRaises(SystemExit):
            shapes.normalise_list([{"path": "  "}], "files", "ctx files", die)
        with self.assertRaises(SystemExit):
            shapes.normalise_list([7], "files", "ctx files", die)

    def test_duplicate_after_normalisation_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            shapes.normalise_list(["a.py", {"path": "a.py"}], "files", "ctx files", die)
        self.assertIn("duplicate", str(ctx.exception))

    def test_notes_field_is_dropped_and_non_lists_left_alone(self):
        raw = {"files": [{"path": "a"}], "notes": "why", "version": "1.0"}
        out = shapes.normalise_lists(raw, ("files",), "ctx", die)
        self.assertEqual(out, {"files": ["a"], "version": "1.0"})
        self.assertEqual(shapes.normalise_lists({"files": "nope"}, ("files",), "ctx", die)["files"], "nope")


@needs_product_tree
class ContextShapeTests(unittest.TestCase):
    """validate_context and the impact compiler accept the run-8 object spelling."""

    def setUp(self):
        self.contract = fixture("run-33916377607-issue-49-task-contract.json")
        self.hash = protocol.validate_contract(dict(self.contract))
        self.raw = fixture("run-33916377607-issue-49-context.raw.json")

    def test_run8_context_raw_is_all_objects(self):
        for key in ("files", "symbols", "callers", "tests", "invariants", "adrs", "history"):
            self.assertTrue(all(isinstance(x, dict) for x in self.raw[key]), key)

    def strings_form(self) -> dict:
        out = dict(self.raw)
        keys = shapes.CANONICAL_KEYS
        for key in ("files", "symbols", "callers", "tests", "invariants", "adrs", "history"):
            out[key] = [next(x[k] for k in keys[key] if k in x) for x in self.raw[key]]
        return out

    def test_validate_context_normalises_to_the_string_form(self):
        got = protocol.validate_context(dict(self.raw), self.hash)
        want = protocol.validate_context(self.strings_form(), self.hash)
        self.assertEqual(got, want)
        self.assertTrue(all(isinstance(x, str) for x in got["files"]))
        self.assertEqual(protocol.canonical(got), protocol.canonical(want))

    def test_validate_context_refuses_object_without_path(self):
        raw = dict(self.raw)
        raw["files"] = [{"why": "no path"}]
        with self.assertRaises(SystemExit):
            protocol.validate_context(raw, self.hash)

    def test_impact_context_mode_accepts_objects(self):
        impact = load_script("factory_impact")
        # The TypeScript half of the analysis needs the frontend's node_modules, which a bare
        # checkout lacks; it is not what this test proves, so it is stubbed deterministically.
        impact.run_ts = lambda payload: {"symbols": [], "callers": [], "tests": sorted(payload["files"])}
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "context.raw.json"
            inp.write_text(json.dumps(self.raw), encoding="utf-8")
            out_obj = Path(tmp) / "obj.json"
            impact.context_mode(argparse.Namespace(input=str(inp), output=str(out_obj)))
            inp.write_text(json.dumps(self.strings_form()), encoding="utf-8")
            out_str = Path(tmp) / "str.json"
            impact.context_mode(argparse.Namespace(input=str(inp), output=str(out_str)))
            a = json.loads(out_obj.read_text(encoding="utf-8"))
            b = json.loads(out_str.read_text(encoding="utf-8"))
            self.assertEqual(a["files"], b["files"])
            self.assertEqual(a["deterministic_impact"]["sha256"], b["deterministic_impact"]["sha256"])
            self.assertIn("app/frontend/src/lib/exportMarkdown.ts", a["files"])


@needs_product_tree
class DesignShapeTests(unittest.TestCase):
    """compile_design accepts the run-8 object spelling and compiles it identically."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.contract_path = root / "task-contract.json"
        shutil.copy(FIX / "run-33916377607-issue-49-task-contract.json", self.contract_path)
        contract = fixture("run-33916377607-issue-49-task-contract.json")
        h = protocol.validate_contract(dict(contract))
        raw_ctx = fixture("run-33916377607-issue-49-context.raw.json")
        self.context = protocol.validate_context(dict(raw_ctx), h)
        self.context_path = root / "context.json"
        self.context_path.write_text(protocol.canonical(self.context).decode() if isinstance(protocol.canonical(self.context), bytes) else protocol.canonical(self.context), encoding="utf-8")
        self.raw = fixture("run-33916377607-issue-49-design.raw.json")

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, raw: dict, name: str) -> dict:
        root = Path(self.tmp.name)
        inp = root / f"{name}.raw.json"
        inp.write_text(json.dumps(raw), encoding="utf-8")
        out = root / f"{name}.json"
        artifacts.compile_design(argparse.Namespace(
            input=str(inp), contract=str(self.contract_path), context=str(self.context_path), output=str(out)
        ))
        return json.loads(out.read_text(encoding="utf-8"))

    def strings_form(self) -> dict:
        out = dict(self.raw)
        for key in ("modules", "seams", "public_interfaces", "invariants", "data_flows"):
            out[key] = [x if isinstance(x, str) else next(x[k] for k in shapes.CANONICAL_KEYS[key] if k in x) for x in self.raw[key]]
        return out

    def test_run8_design_raw_uses_objects(self):
        self.assertTrue(all(isinstance(x, dict) for x in self.raw["modules"]))
        self.assertTrue(all(isinstance(x, dict) for x in self.raw["seams"]))

    def test_object_and_string_designs_compile_identically(self):
        a = self.compile(self.raw, "obj")
        b = self.compile(self.strings_form(), "str")
        self.assertEqual(a, b)
        self.assertTrue(all(isinstance(x, str) for x in a["seams"]))
        self.assertEqual(sorted(a["ac_mapping"]), [b["id"] for b in fixture("run-33916377607-issue-49-task-contract.json")["behaviors"]])

    def test_design_object_without_name_refused(self):
        raw = dict(self.raw)
        raw["modules"] = [{"why": "no name"}]
        with self.assertRaises(SystemExit):
            self.compile(raw, "bad")

    def test_single_seam_string_in_ac_mapping_is_wrapped(self):
        raw = self.strings_form()
        first = next(iter(raw["ac_mapping"]))
        raw["ac_mapping"] = {**raw["ac_mapping"], first: raw["ac_mapping"][first][0]}
        a = self.compile(raw, "wrapped")
        self.assertEqual(a["ac_mapping"][first], [self.strings_form()["ac_mapping"][first][0]])


class GovernorShapeTests(unittest.TestCase):
    """compile_value accepts the run-7 governor: ids as objects, rationale as a string."""

    def setUp(self):
        self.policy = json.loads((ROOT / ".factory" / "architecture.json").read_text(encoding="utf-8"))
        self.contract = fixture("run-33914596611-issue-49-task-contract.json")
        self.context = fixture("run-33914596611-issue-49-context.json")
        self.design = fixture("run-33914596611-issue-49-design.json")
        self.raw = fixture("run-33914596611-issue-49-architecture-governor.raw.json")

    def test_run7_governor_uses_id_objects(self):
        self.assertTrue(all(isinstance(x, dict) and "id" in x for x in self.raw["principles"]))
        self.assertIsInstance(self.raw["rationale"], str)

    def expected_ids(self) -> dict:
        files = architecture.governed_files(self.context, self.design)
        return {
            "principles": architecture.applicable(self.policy["principles"], files, "scope"),
            "migrations": architecture.applicable(self.policy["migrations"], files, "paths", active_only=True),
            "debts": architecture.applicable(self.policy["debt"], files, "paths"),
        }

    def test_run7_spelling_is_accepted_and_its_real_mismatch_is_still_named(self):
        """Run 7's governor judged relevance semantically and omitted two prefix-applicable ids.

        The spelling (id objects, applicable flags, a prose rationale) is now accepted; the
        omission is still refused, and the refusal names the ids, which is the audit's R6 and
        the reason #70 hands the governor the computed sets.
        """
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            architecture.compile_value(self.policy, dict(self.raw), self.contract, self.context, self.design)
        message = err.getvalue()
        self.assertIn("must exactly match applicable policy ids", message)
        self.assertNotIn("lacks its canonical key", message)
        self.assertNotIn("must be a list", message)

    def corrected(self) -> dict:
        """Run 7's governor with its applicable flags set the way the compiler computes them."""
        raw = dict(self.raw)
        expected = self.expected_ids()
        for key in ("principles", "migrations", "debts"):
            raw[key] = [dict(x, applicable=x["id"] in expected[key]) for x in self.raw[key]]
        return raw

    def test_run7_governor_compiles_once_its_flags_match_the_computed_sets(self):
        got = architecture.compile_value(self.policy, self.corrected(), self.contract, self.context, self.design)
        self.assertEqual(got["decision"], "proceed")
        self.assertEqual(got["principles"], self.expected_ids()["principles"])
        self.assertTrue(all(isinstance(x, str) for x in got["principles"]))
        self.assertIsInstance(got["rationale"], list)

    def test_id_objects_and_id_strings_compile_identically(self):
        objects = self.corrected()
        strings = dict(objects)
        for key in ("principles", "migrations", "debts"):
            strings[key] = [x["id"] for x in objects[key] if x.get("applicable") is not False]
        strings["rationale"] = [self.raw["rationale"]]
        a = architecture.compile_value(self.policy, objects, self.contract, self.context, self.design)
        b = architecture.compile_value(self.policy, strings, self.contract, self.context, self.design)
        self.assertEqual(a, b)

    def test_id_object_without_id_refused(self):
        raw = self.corrected()
        raw["principles"] = [{"verdict": "complies"}] + list(raw["principles"][1:])
        with self.assertRaises(SystemExit):
            architecture.compile_value(self.policy, raw, self.contract, self.context, self.design)

    def test_run7_governor_marked_inapplicable_entries_false(self):
        """The governor walked the whole policy and flagged each entry; that is how it said 'omit'."""
        flagged = [x for x in self.raw["principles"] + self.raw["migrations"] + self.raw["debts"] if x.get("applicable") is False]
        self.assertGreaterEqual(len(flagged), 10)

    def test_inapplicable_id_without_the_flag_is_still_refused(self):
        raw = self.corrected()
        raw["principles"] = [dict(x, applicable=True) for x in raw["principles"]]
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            architecture.compile_value(self.policy, raw, self.contract, self.context, self.design)
        self.assertIn("exactly match applicable policy ids", err.getvalue())

    def test_wrong_ids_still_refused_after_normalisation(self):
        raw = self.corrected()
        raw["principles"] = [{"id": "ARCH-NOT-A-POLICY"}]
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            architecture.compile_value(self.policy, raw, self.contract, self.context, self.design)
        self.assertIn("exactly match applicable policy ids", err.getvalue())


class PromptShapeTests(unittest.TestCase):
    def test_context_prompt_shows_plain_string_skeletons_and_notes(self):
        text = (ROOT / ".factory" / "prompts" / "context.md").read_text(encoding="utf-8")
        self.assertIn('"files": ["app/path/one.py"', text)
        self.assertIn('"seams": ["one.py#function_name"]', text)
        self.assertIn("Every array holds plain strings; put explanations in `notes`", text)
        self.assertEqual(text.count('"notes": "free text; the compiler ignores it"'), 2)

    def test_other_prompts_name_notes_for_explanations(self):
        for name in ("architecture.md", "conformance.md", "test-author.md"):
            text = (ROOT / ".factory" / "prompts" / name).read_text(encoding="utf-8")
            self.assertIn("plain strings", text, name)
            self.assertIn("`notes`", text, name)


if __name__ == "__main__":
    unittest.main()
