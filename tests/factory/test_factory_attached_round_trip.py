"""Runner output stored in evidence is sanitised, and an attached block must survive the PR body.

The first production `validate_pr` (worker run 33931048575) refused PR #74 because the proof's
`red_output_tail` carried vitest colour escapes: `\\u001b` came back from the PR body as a
backslash followed by caret-notation `^[`, which is not a JSON escape. These tests pin both halves
of the fix: the sanitiser at the source, and the attach-time round-trip check that fails the
build instead of the validator hours later (D-038).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import attached  # noqa: E402
from factory_kernel import repro  # noqa: E402

FIXTURES = ROOT / "tests" / "factory" / "fixtures" / "proof"
ESC = "\x1b"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SanitiserTests(unittest.TestCase):
    def test_ansi_sequences_are_stripped(self):
        coloured = f"{ESC}[31mFAIL{ESC}[39m {ESC}[1mexpected{ESC}[22m{ESC}[0m"
        self.assertEqual(attached.sanitise_output(coloured), "FAIL expected")

    def test_osc_and_two_byte_escapes_are_stripped(self):
        text = f"{ESC}]0;title\x07before {ESC}(Bafter {ESC}7saved"
        self.assertEqual(attached.sanitise_output(text), "before after saved")

    def test_lone_escape_is_stripped(self):
        self.assertEqual(attached.sanitise_output(f"a{ESC}b"), "ab")

    def test_control_bytes_become_replacement_but_newline_and_tab_survive(self):
        text = "line1\nline2\ttabbed\x00nul\x07bell\x7fdel"
        out = attached.sanitise_output(text)
        self.assertEqual(out, "line1\nline2\ttabbed�nul�bell�del")

    def test_idempotent(self):
        text = f"{ESC}[32m ok {ESC}[0m\x01"
        once = attached.sanitise_output(text)
        self.assertEqual(attached.sanitise_output(once), once)
        self.assertFalse(attached.has_control_bytes(once))

    def test_symptom_inside_coloured_output_still_matches(self):
        """The RED gate matches against sanitised text; a symptom split by colour codes matches."""
        symptom = "expected '- Test Video' to contain"
        coloured = f"{ESC}[31m{symptom[:10]}{ESC}[39m{symptom[10:]}{ESC}[0m"
        self.assertNotIn(symptom, coloured)
        self.assertIn(symptom, attached.sanitise_output(coloured))

    def test_proof_run_sanitises_runner_output(self):
        proof = load_script("factory_proof")
        fake = subprocess.CompletedProcess(["x"], 1, stdout=f"{ESC}[31mred{ESC}[0m", stderr="\x00tail")
        with mock.patch.object(proof.subprocess, "run", return_value=fake):
            rc, out = proof.run(["x"], ".")
        self.assertEqual((rc, out), (1, "red�tail"))

    def test_deferred_symptom_matches_sanitised_tail(self):
        record = {"mode": "deferred", "expected_symptom": "timestamp link unavailable"}
        tail = attached.sanitise_output(f"{ESC}[31m(timestamp link {ESC}[1munavailable){ESC}[0m")
        result = repro.verify_deferred_in_red(record, {"checkpoints": [{"acceptance_id": "AC-1", "red_output_tail": tail}]})
        self.assertTrue(result["matched"])


class FixtureTests(unittest.TestCase):
    def test_pr74_block_as_attached_is_not_json(self):
        block = (FIXTURES / "pr74-proof-block.txt").read_text(encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(block)
        # GitHub rendered the JSON escape `\u001b` as a backslash followed by caret-notation `^[`.
        self.assertIn("\\^[", block, "the corruption is a backslash followed by caret-notation ESC")
        self.assertNotIn(ESC, block)

    def test_run10_proof_sanitised_and_canonicalised_has_no_control_bytes(self):
        proof = json.loads((FIXTURES / "run10-final-green-proof.json").read_text(encoding="utf-8"))
        self.assertTrue(
            any(ESC in cp.get("red_output_tail", "") for cp in proof["checkpoints"]),
            "the fixture must reproduce the condition: a tail with ESC bytes",
        )
        for cp in proof["checkpoints"]:
            cp["red_output_tail"] = attached.sanitise_output(cp["red_output_tail"])
        text = attached.canonical_bytes(proof).decode("utf-8")
        self.assertNotIn(ESC, text)
        self.assertFalse(attached.has_control_bytes(text.replace("\n", "")))
        self.assertEqual(json.loads(text)["green_commit"], proof["green_commit"])


class ExtractTests(unittest.TestCase):
    def body(self, kind: str, payload: str) -> str:
        return f"Fixes #1\n\n<!-- factory-{kind}:start -->\n```factory-{kind}\n{payload}\n```\n{kind}-sha256: x\n<!-- factory-{kind}:end -->\n"

    def test_extract_matches_the_kernel_parser(self):
        from factory_kernel.runtime import KernelRuntime

        body = self.body("contract", '{"a":1}') + self.body("proof", '{"b":2}')
        contract, proof = KernelRuntime._extract_attached(body)
        self.assertEqual((dict(contract), dict(proof)), ({"a": 1}, {"b": 2}))
        self.assertEqual(dict(attached.extract_block(body, "proof")), {"b": 2})

    def test_missing_invalid_and_non_object_are_refused(self):
        with self.assertRaises(ValueError):
            attached.extract_block("nothing here", "proof")
        with self.assertRaises(ValueError):
            attached.extract_block(self.body("proof", "{not json"), "proof")
        with self.assertRaises(ValueError):
            attached.extract_block(self.body("proof", "[1]"), "proof")

    def test_round_trip_ok_compares_canonical_bytes(self):
        value = {"z": 1, "a": [1, 2]}
        self.assertTrue(attached.round_trip_ok(self.body("design", '{"a":[1,2],"z":1}'), "design", value))
        self.assertFalse(attached.round_trip_ok(self.body("design", '{"a":[1,2],"z":2}'), "design", value))
        self.assertFalse(attached.round_trip_ok(self.body("design", '{"a":[1,2],"z":1\\' + ESC), "design", value))


class AttachRoundTripTests(unittest.TestCase):
    """attach edits the PR, reads the body back, and refuses when the block did not survive."""

    def setUp(self):
        self.proof = load_script("factory_proof")
        self.calls: list[list[str]] = []

    def _proof(self, head: str) -> dict:
        return {
            "version": "2.0", "green_commit": head,
            "green_results": [{"acceptance_id": "AC-1", "exit": 0, "output_sha256": "0" * 64}],
            "architecture_builder": {"x": 1}, "architecture_guard": {"sha256": "1" * 64},
            "design_sha256": "", "checkpoints": [], "files": {},
        }

    def _run_attach(self, tmp: Path, mangle: bool):
        head = "d" * 40
        design = {"version": "1.0", "planned_files": ["a.py"]}
        proof = self._proof(head)
        proof["architecture_builder_sha256"] = self.proof.digest(proof["architecture_builder"])
        proof["design_sha256"] = self.proof.digest(design)
        (tmp / "design.json").write_text(self.proof.canonical(design), encoding="utf-8")
        (tmp / "final.json").write_text(self.proof.canonical(proof), encoding="utf-8")
        state = {"body": "Fixes #1\n"}

        def fake_check_output(argv, **kw):
            self.calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "view"]:
                return json.dumps({"body": state["body"], "headRefOid": head})
            if argv[:2] == ["git", "rev-parse"]:
                return head + "\n"
            raise AssertionError(argv)

        def fake_run(argv, **kw):
            self.calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "edit"]:
                sent = Path(argv[argv.index("--body-file") + 1]).read_text(encoding="utf-8")
                state["body"] = sent.replace("\\u001b", "\\^[") if mangle else sent
                return subprocess.CompletedProcess(argv, 0, "", "")
            raise AssertionError(argv)

        args = mock.Mock(proof=str(tmp / "final.json"), pr="74")
        with mock.patch.object(self.proof.subprocess, "check_output", side_effect=fake_check_output), \
             mock.patch.object(self.proof.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.proof, "clean"), \
             mock.patch.dict("os.environ", {"ARTIFACTS_DIR": str(tmp)}):
            self.proof.attach(args)
        return state["body"]

    def test_intact_round_trip_attaches_via_body_file_and_reads_back(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            body = self._run_attach(Path(d), mangle=False)
        self.assertIn("```factory-proof", body)
        edit = next(c for c in self.calls if c[:3] == ["gh", "pr", "edit"])
        self.assertIn("--body-file", edit)
        self.assertNotIn("--body", [x for x in edit if x != "--body-file"])
        views = [c for c in self.calls if c[:3] == ["gh", "pr", "view"]]
        self.assertGreaterEqual(len(views), 2, "attach must read the body back after editing")

    def test_mangled_round_trip_is_refused(self):
        import tempfile

        proof_with_esc = self._proof
        def with_esc(head):
            p = proof_with_esc(head)
            p["checkpoints"] = [{"acceptance_id": "AC-1", "red_output_tail": "x" + ESC + "y"}]
            return p
        self._proof = with_esc
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            self._run_attach(Path(d), mangle=True)


if __name__ == "__main__":
    unittest.main()
