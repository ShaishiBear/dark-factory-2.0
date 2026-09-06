"""A checkpoint can guard kept behaviour: it must pass at RED and at GREEN, never fail.

Build run 34008561672 (issue #103) compiled AC-3 as behaviour the issue says is kept ("the old
stream's fetch is aborted ... this kept behaviour is pinned by the existing tests, which must
stay green"). The test author declared a red checkpoint for it, and the RED gate refused
`AC-3 RED command unexpectedly passed`, correctly: a test of kept behaviour passes on the
unchanged tree by definition, and test-spec 2.0 had one checkpoint shape. These tests pin
D-058: test-spec 2.1 carries `kind: red | guard` (absent = red, so 2.0 reads as before); a
guard has no `expected_failure` and must exit 0 at RED (`guard failed on the unchanged tree`)
and at every GREEN (`guard broken by the implementation`), with the same evidence a red
refusal leaves; a spec of only guards is refused; a guard with `expected_failure` is malformed;
the contract may mark a behaviour `kind: guard` and the RED gate holds the author to it; the
proof, the evidence bundle and the holdout summary count and label guards separately; and the
prompts say so.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib.util
import inspect
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.refusal import ToolRefused  # noqa: E402
from factory_kernel.runtime import KernelRuntime, NeedsHuman, RunPaths  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load("factory_proof_guards", "scripts/factory_proof.py")
evidence = _load("factory_evidence_guards", "scripts/factory_evidence.py")
protocol = _load("factory_protocol_guards", "scripts/factory_protocol.py")

_SCRIPTS = Path(tempfile.mkdtemp(prefix="dark-factory-guard-checkpoints-"))
atexit.register(shutil.rmtree, _SCRIPTS, True)
_COUNTER = itertools.count()


def _py(code: str) -> list[str]:
    path = _SCRIPTS / f"checkpoint_{next(_COUNTER)}.py"
    path.write_text(code + "\n", encoding="utf-8")
    return [sys.executable, str(path)]


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


RED_FILE = "tests/red_test.py"
GUARD_FILE = "tests/guard_test.py"


class _RootCase(unittest.TestCase):
    """A temp tree the proof program treats as the checkout, with ARTIFACTS_DIR beside it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-guard-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        (self.root / "tests").mkdir(parents=True)
        for rel in (RED_FILE, GUARD_FILE):
            (self.root / rel).write_text("# acceptance test\n", encoding="utf-8")
        self.artifacts = Path(self.tmp.name) / "artifacts"
        self.artifacts.mkdir()
        for patch in (
            mock.patch.object(proof, "ROOT", self.root),
            mock.patch.dict(os.environ, {"ARTIFACTS_DIR": str(self.artifacts)}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def contract(kinds: dict[str, str] | None = None) -> dict:
        behaviors = []
        for ac in ("AC-1", "AC-2"):
            b = {"id": ac, "given": "g", "when": "w", "then": "t", "seam": "s"}
            if kinds and ac in kinds:
                b["kind"] = kinds[ac]
            behaviors.append(b)
        return {"version": "2.0", "behaviors": behaviors}

    @staticmethod
    def design() -> dict:
        return {
            "ac_mapping": {"AC-1": ["app/x.py#f"], "AC-2": ["app/y.py#g"]},
            "planned_files": ["app/x.py"],
            "allowed_new_files": [],
        }

    def artifacts_patch(self, contract: dict | None = None):
        contract = contract or self.contract()
        ids = [b["id"] for b in contract["behaviors"]]
        return mock.patch.object(proof, "artifacts", return_value=(contract, self.design(), ids))

    @staticmethod
    def red(ac: str = "AC-1", **over) -> dict:
        cp = {"acceptance_id": ac, "cwd": ".", "argv": [sys.executable, "-V"],
              "files": [RED_FILE], "expected_failure": f"{ac} declared symptom"}
        cp.update(over)
        return cp

    @staticmethod
    def guard(ac: str = "AC-2", **over) -> dict:
        cp = {"acceptance_id": ac, "kind": "guard", "cwd": ".",
              "argv": [sys.executable, "-V"], "files": [GUARD_FILE]}
        cp.update(over)
        return cp

    def write_spec(self, checkpoints: list[dict], version: str = "2.1", name: str = "spec") -> str:
        path = self.artifacts / f"{name}.json"
        path.write_text(json.dumps({"version": version, "checkpoints": checkpoints}), encoding="utf-8")
        return str(path)

    def refused(self, fn, *args) -> str:
        err = io.StringIO()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            fn(*args)
        self.assertEqual(ctx.exception.code, 1)
        return err.getvalue()


# --- the spec shape ----------------------------------------------------------------------------


class SpecShapeTests(_RootCase):
    def test_a_2_0_spec_is_still_accepted_and_every_checkpoint_reads_as_red(self):
        path = self.write_spec([self.red("AC-1"), self.red("AC-2")], version="2.0")
        with self.artifacts_patch():
            value = proof.spec(path)
        self.assertEqual(value["version"], "2.0")
        self.assertEqual([cp["kind"] for cp in value["checkpoints"]], ["red", "red"])
        self.assertEqual(value["guards"], 0)
        self.assertEqual(value["checkpoints"][0]["seams"], ["app/x.py#f"])

    def test_a_2_1_red_only_spec_has_the_same_outcome_as_2_0(self):
        old = self.write_spec([self.red("AC-1"), self.red("AC-2")], version="2.0", name="old")
        explicit = self.write_spec(
            [self.red("AC-1", kind="red"), self.red("AC-2", kind="red")], name="explicit"
        )
        implicit = self.write_spec([self.red("AC-1"), self.red("AC-2")], name="implicit")
        with self.artifacts_patch():
            outcomes = [proof.spec(p) for p in (old, explicit, implicit)]
        for value in outcomes:
            value.pop("version")
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[1], outcomes[2])
        self.assertEqual(outcomes[0]["guards"], 0)

    def test_a_guard_beside_a_red_checkpoint_is_accepted_and_counted(self):
        path = self.write_spec([self.red("AC-1"), self.guard("AC-2")])
        with self.artifacts_patch():
            value = proof.spec(path)
        self.assertEqual(value["guards"], 1)
        guard = value["checkpoints"][1]
        self.assertEqual(guard["kind"], "guard")
        self.assertNotIn("expected_failure", guard)
        self.assertEqual(guard["seams"], ["app/y.py#g"])

    def test_a_spec_of_only_guards_is_refused(self):
        path = self.write_spec([self.guard("AC-1"), self.guard("AC-2")])
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("at least one red checkpoint", err)
        self.assertIn("proves no change", err)

    def test_a_guard_with_expected_failure_is_refused_as_malformed(self):
        path = self.write_spec(
            [self.red("AC-1"), self.guard("AC-2", expected_failure="must abort")]
        )
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("AC-2 guard checkpoint must not declare expected_failure", err)

    def test_a_red_checkpoint_without_expected_failure_is_still_refused(self):
        cp = self.red("AC-1")
        del cp["expected_failure"]
        path = self.write_spec([cp, self.red("AC-2")])
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("missing fields", err)

    def test_a_guard_needs_version_2_1(self):
        path = self.write_spec([self.red("AC-1"), self.guard("AC-2")], version="2.0")
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("guard checkpoints require test-spec version 2.1", err)

    def test_an_unknown_kind_is_refused(self):
        path = self.write_spec([self.red("AC-1"), self.red("AC-2", kind="soft")])
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("AC-2 kind must be one of", err)

    def test_an_unknown_version_is_refused(self):
        path = self.write_spec([self.red("AC-1"), self.red("AC-2")], version="2.2")
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("version 2.0 or 2.1", err)

    def test_a_contract_guard_behaviour_must_get_a_guard_checkpoint(self):
        path = self.write_spec([self.red("AC-1"), self.red("AC-2")])
        with self.artifacts_patch(self.contract({"AC-2": "guard"})):
            err = self.refused(proof.spec, path)
        self.assertIn("AC-2 is a guard behaviour in the contract", err)
        self.assertIn("must be kind guard, not red", err)

    def test_a_contract_guard_behaviour_with_a_guard_checkpoint_is_accepted(self):
        path = self.write_spec([self.red("AC-1"), self.guard("AC-2")])
        with self.artifacts_patch(self.contract({"AC-2": "guard"})):
            self.assertEqual(proof.spec(path)["guards"], 1)

    def test_a_guard_checkpoint_for_an_ordinary_behaviour_is_allowed(self):
        """A Then that says "kept" needs no contract kind; the author may still guard it."""
        path = self.write_spec([self.red("AC-1"), self.guard("AC-2")])
        with self.artifacts_patch(self.contract()):
            self.assertEqual(proof.spec(path)["guards"], 1)

    def test_every_ac_still_has_exactly_one_checkpoint(self):
        path = self.write_spec([self.red("AC-1"), self.guard("AC-1")])
        with self.artifacts_patch():
            err = self.refused(proof.spec, path)
        self.assertIn("cover every contract AC exactly once", err)


# --- one checkpoint at RED and at GREEN -----------------------------------------------------------


class GuardCheckpointTests(_RootCase):
    def failure_record(self, kind: str) -> dict:
        path = self.artifacts / f"{kind}-proof-failure.json"
        self.assertTrue(path.is_file(), f"{path.name} was not written")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_guard_that_passes_on_the_unchanged_tree_is_proved_at_red(self):
        cp = self.guard(argv=_py("print('12 tests passed')"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = proof.prove_red(cp)
        self.assertEqual(result["red_exit"], 0)
        self.assertEqual(result["kind"], "guard")
        self.assertNotIn("expected_failure", result)
        self.assertIn("12 tests passed", result["red_output_tail"])
        self.assertEqual(len(result["red_output_sha256"]), 64)
        self.assertIsInstance(result["red_seconds"], float)
        self.assertRegex(out.getvalue(), r"GUARD_CHECKPOINT AC-2 stage=red rc=0 seconds=\d")
        self.assertFalse((self.artifacts / "red-proof-failure.json").exists())

    def test_a_guard_that_fails_on_the_unchanged_tree_is_refused_with_evidence(self):
        cp = self.guard(argv=_py("print('FAIL tests/guard_test.py: abort was dropped'); raise SystemExit(1)"))
        err = self.refused(proof.prove_red, cp)
        self.assertIn("PROOF_FAIL: AC-2 guard failed on the unchanged tree", err)
        self.assertIn(json.dumps(cp["argv"]), err, "the refusal names the argv that ran")
        self.assertIn("cwd: .", err)
        self.assertIn("rc: 1", err)
        self.assertIn("kind: guard", err)
        self.assertNotIn("expected_failure:", err)
        self.assertIn("abort was dropped", err, "the checkpoint's own output")
        record = self.failure_record("red")
        self.assertEqual(record["kind"], "red")
        self.assertEqual(record["stage"], "red")
        self.assertEqual(record["checkpoint_kind"], "guard")
        self.assertEqual(record["acceptance_id"], "AC-2")
        self.assertEqual(record["rc"], 1)
        self.assertIsNone(record["expected_failure"])
        self.assertIn("guard failed on the unchanged tree", record["reason"])
        self.assertIn("abort was dropped", record["output_tail"])

    def test_a_guard_that_passes_at_green_is_proved(self):
        cp = self.guard(argv=_py("print('ok')"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = proof.prove_green(cp)
        self.assertEqual(result, {**result, "acceptance_id": "AC-2", "kind": "guard", "exit": 0})
        self.assertRegex(out.getvalue(), r"GUARD_CHECKPOINT AC-2 stage=green rc=0")

    def test_a_guard_broken_by_the_implementation_is_refused_at_green(self):
        cp = self.guard(argv=_py("import sys; print('FAIL reset no longer aborts', file=sys.stderr); sys.exit(2)"))
        err = self.refused(lambda c: proof.prove_green(c, "final-green"), cp)
        self.assertIn("PROOF_FAIL: AC-2 guard broken by the implementation", err)
        self.assertIn("rc: 2", err)
        self.assertIn("reset no longer aborts", err)
        record = self.failure_record("green")
        self.assertEqual(record["kind"], "green")
        self.assertEqual(record["stage"], "final-green")
        self.assertEqual(record["checkpoint_kind"], "guard")
        self.assertIn("guard broken by the implementation", record["reason"])
        self.assertFalse((self.artifacts / "red-proof-failure.json").exists())

    def test_a_guard_that_cannot_be_launched_is_refused_not_proved(self):
        cp = self.guard(argv=["dark-factory-no-such-command-7b2e"])
        err = self.refused(proof.prove_red, cp)
        self.assertIn("AC-2 guard command could not be launched", err)
        self.assertEqual(self.failure_record("red")["fault"], "launch")

    def test_a_red_checkpoint_keeps_its_semantics_exactly(self):
        passing = self.red(argv=_py("print('all green')"))
        err = self.refused(proof.prove_red, passing)
        self.assertIn("AC-1 RED command unexpectedly passed", err)
        failing = self.red(argv=_py("print('AC-1 DECLARED symptom'); raise SystemExit(1)"))
        with contextlib.redirect_stdout(io.StringIO()):
            result = proof.prove_red(failing)
        self.assertEqual(result["red_exit"], 1)
        self.assertEqual(proof.checkpoint_kind(result), "red")

    def test_the_kind_helper_defaults_an_absent_kind_to_red(self):
        self.assertEqual(proof.checkpoint_kind({"acceptance_id": "AC-1"}), "red")
        self.assertEqual(proof.checkpoint_kind({"kind": "guard"}), "guard")
        self.assertEqual(proof.guards_in([{"kind": "guard"}, {}, {"kind": "red"}]), 1)


# --- red and green end to end, with the proof block and the plan ----------------------------------


class RedGreenEndToEndTests(_RootCase):
    """`factory_proof.py red` then `green` on a real repository with one red and one guard."""

    def setUp(self) -> None:
        super().setUp()
        git(self.root, "init", "-q")
        git(self.root, "config", "core.autocrlf", "false")
        (self.root / "app").mkdir()
        (self.root / "app" / "x.py").write_text("x = 1\n", encoding="utf-8")
        for rel in (RED_FILE, GUARD_FILE):
            (self.root / rel).unlink()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        # The red test fails until app/fixed.txt exists; the guard passes throughout.
        (self.root / RED_FILE).write_text(
            "import pathlib, sys\n"
            "if pathlib.Path('app/fixed.txt').exists():\n"
            "    print('AC-1 satisfied'); sys.exit(0)\n"
            "print('AssertionError: AC-1 the stream was aborted'); sys.exit(1)\n",
            encoding="utf-8",
        )
        (self.root / GUARD_FILE).write_text(
            "print('AC-2 conversation switch still aborts: 3 passed')\n", encoding="utf-8"
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "test(factory): acceptance tests")
        self.test_commit = git(self.root, "rev-parse", "HEAD")
        contract = self.contract({"AC-2": "guard"})
        for name, value in (
            ("task-contract.json", contract),
            ("design.json", self.design()),
            ("context.json", {"version": "1.0"}),
        ):
            (self.artifacts / name).write_text(json.dumps(value), encoding="utf-8")
        self.spec_path = self.write_spec([
            self.red("AC-1", argv=[sys.executable, RED_FILE], expected_failure="the stream was aborted"),
            self.guard("AC-2", argv=[sys.executable, GUARD_FILE]),
        ])
        self.red_output = self.artifacts / "red-proof.json"
        self.green_output = self.artifacts / "green-proof.json"

    def run_red(self) -> tuple[dict, str]:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            proof.red(types.SimpleNamespace(spec=self.spec_path, output=str(self.red_output)))
        return json.loads(self.red_output.read_text(encoding="utf-8")), out.getvalue()

    def run_green(self) -> tuple[dict, str]:
        (self.root / "app" / "fixed.txt").write_text("fixed\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "fix(factory): satisfy issue #1")
        with (
            mock.patch.object(proof, "impact_check", return_value=None),
            mock.patch.object(
                proof, "architecture_guard_check", return_value={"sha256": "0" * 64, "artifact": "a"}
            ),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            proof.green(types.SimpleNamespace(proof=str(self.red_output), output=str(self.green_output)))
        return json.loads(self.green_output.read_text(encoding="utf-8")), out.getvalue()

    def test_red_proves_the_red_checkpoint_red_and_the_guard_green(self):
        red, out = self.run_red()
        self.assertRegex(out, r"RED_PROVED criteria=2 tests=2 commit=[0-9a-f]{40} seconds=[\d.]+ guards=1$")
        self.assertIn("RED_CHECKPOINT AC-1 rc=1", out)
        self.assertIn("GUARD_CHECKPOINT AC-2 stage=red rc=0", out)
        self.assertEqual(red["guards"], 1)
        self.assertEqual(red["version"], "2.0", "the proof format is unchanged")
        self.assertEqual(red["test_commit"], self.test_commit)
        by_id = {cp["acceptance_id"]: cp for cp in red["checkpoints"]}
        self.assertEqual(by_id["AC-1"]["kind"], "red")
        self.assertEqual(by_id["AC-1"]["red_exit"], 1)
        self.assertEqual(by_id["AC-2"]["kind"], "guard")
        self.assertEqual(by_id["AC-2"]["red_exit"], 0)
        self.assertNotIn("expected_failure", by_id["AC-2"])
        self.assertIn("still aborts", by_id["AC-2"]["red_output_tail"])
        self.assertEqual(set(red["files"]), {RED_FILE, GUARD_FILE})

    def test_the_plan_carries_the_kind_and_both_programs_reconstruct_the_same_digest(self):
        red, _ = self.run_red()
        plan = json.loads((self.artifacts / "test-plan.json").read_text(encoding="utf-8"))
        kinds = {cp["acceptance_id"]: cp["kind"] for cp in plan["checkpoints"]}
        self.assertEqual(kinds, {"AC-1": "red", "AC-2": "guard"})
        guard_plan = next(cp for cp in plan["checkpoints"] if cp["kind"] == "guard")
        self.assertNotIn("expected_failure", guard_plan)
        self.assertEqual(proof.digest(plan), red["test_plan_sha256"])
        self.assertEqual(evidence.digest(evidence.plan_from_proof(red)), red["test_plan_sha256"],
                         "the evidence bundle rebuilds the plan the proof program hashed")
        self.assertEqual(evidence.PLAN_KEYS, proof.PLAN_KEYS)

    def test_green_replays_both_and_counts_the_guard(self):
        self.run_red()
        green, out = self.run_green()
        self.assertRegex(out, r"GREEN_PROVED criteria=2 tests=2 commit=[0-9a-f]{40} seconds=[\d.]+ guards=1$")
        self.assertIn("GREEN_CHECKPOINT AC-1 rc=0", out)
        self.assertIn("GUARD_CHECKPOINT AC-2 stage=green rc=0", out)
        results = {g["acceptance_id"]: g for g in green["green_results"]}
        self.assertEqual(results["AC-1"]["exit"], 0)
        self.assertNotIn("kind", results["AC-1"], "a red result reads as it always did")
        self.assertEqual(results["AC-2"]["kind"], "guard")
        self.assertEqual(results["AC-2"]["exit"], 0)
        self.assertEqual(green["guards"], 1)

    def test_the_evidence_bundle_accepts_every_checkpoint_of_the_real_proof(self):
        self.run_red()
        green, _ = self.run_green()
        for cp in green["checkpoints"]:
            self.assertIs(evidence.validate_checkpoint(cp), cp)

    def test_a_guard_that_breaks_at_green_refuses_the_whole_green(self):
        self.run_red()
        (self.root / GUARD_FILE).write_text("raise SystemExit(1)\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_green()
        # The RED-hashed guard file changed: the immutable-test check refuses before any replay.
        self.assertFalse(self.green_output.exists())

    def test_a_deferred_symptom_is_still_closed_against_a_red_checkpoint(self):
        from factory_kernel.repro import verify_deferred_in_red

        red, _ = self.run_red()
        closed = verify_deferred_in_red(
            {"mode": "deferred", "expected_symptom": "the stream was aborted"}, red
        )
        self.assertEqual(closed["checkpoint"], "AC-1")


# --- the evidence bundle -----------------------------------------------------------------------


class EvidenceBundleTests(unittest.TestCase):
    def guard(self, **over) -> dict:
        cp = {"acceptance_id": "AC-2", "kind": "guard", "seams": ["s"], "cwd": ".",
              "argv": ["python", "-V"], "files": ["tests/test_ac-2.py"], "red_exit": 0,
              "red_output_sha256": "2" * 64}
        cp.update(over)
        return cp

    def red(self, **over) -> dict:
        cp = {"acceptance_id": "AC-1", "seams": ["s"], "cwd": ".", "argv": ["python", "-V"],
              "files": ["tests/test_ac-1.py"], "expected_failure": "AC-1 expected behavior",
              "red_exit": 1, "red_output_sha256": "1" * 64}
        cp.update(over)
        return cp

    def refused(self, fn, *args) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            fn(*args)
        return err.getvalue()

    def test_a_guard_checkpoint_is_accepted_with_a_passing_red_exit_and_no_expected_failure(self):
        cp = self.guard()
        self.assertIs(evidence.validate_checkpoint(cp), cp)

    def test_a_guard_that_failed_at_red_is_refused(self):
        err = self.refused(evidence.validate_checkpoint, self.guard(red_exit=1))
        self.assertIn("did not pass on the unchanged tree", err)

    def test_a_guard_with_expected_failure_is_refused(self):
        err = self.refused(evidence.validate_checkpoint, self.guard(expected_failure="x"))
        self.assertIn("carries expected_failure", err)

    def test_an_unknown_kind_is_refused(self):
        err = self.refused(evidence.validate_checkpoint, self.red(kind="soft"))
        self.assertIn("invalid kind", err)

    def test_a_red_checkpoint_is_judged_as_before(self):
        plain = self.red()
        self.assertIs(evidence.validate_checkpoint(plain), plain)
        self.refused(evidence.validate_checkpoint, self.red(red_exit=0))
        cp = self.red()
        del cp["expected_failure"]
        self.refused(evidence.validate_checkpoint, cp)
        explicit = self.red(kind="red")
        self.assertIs(evidence.validate_checkpoint(explicit), explicit)

    def test_a_proof_of_only_guards_is_refused_before_any_replay(self):
        contract = {"version": "2.0", "behaviors": [{"id": "AC-1"}, {"id": "AC-2"}]}
        p = {
            "version": "2.0", "test_commit": "def", "contract_sha256": evidence.digest(contract),
            "design_sha256": "2" * 64,
            "files": {"tests/test_ac-1.py": "3" * 64, "tests/test_ac-2.py": "4" * 64},
            "checkpoints": [self.guard(acceptance_id="AC-1", files=["tests/test_ac-1.py"]), self.guard()],
            "green_commit": "abc",
            "green_results": [{"acceptance_id": "AC-1", "exit": 0}, {"acceptance_id": "AC-2", "exit": 0}],
        }
        p["test_plan_sha256"] = evidence.digest(evidence.plan_from_proof(p))
        err = self.refused(
            evidence.validate_proof_fields, p, "abc", contract, evidence.digest(contract)
        )
        self.assertIn("only guard checkpoints", err)

    def test_a_guard_replay_must_exit_zero_at_both_stages(self):
        evidence.validate_guard_result(0, "RED")
        evidence.validate_guard_result(0, "GREEN")
        self.assertIn("guard failed on the unchanged tree", self.refused(evidence.validate_guard_result, 1, "RED"))
        self.assertIn("guard failed at the head", self.refused(evidence.validate_guard_result, 1, "GREEN"))

    def test_both_replays_route_a_guard_through_the_guard_verdict(self):
        for fn in (evidence.replay_red, evidence.replay_green):
            source = inspect.getsource(fn)
            self.assertIn('checkpoint_kind(cp) == "guard"', source, fn.__name__)
            self.assertIn("validate_guard_result(result.returncode", source, fn.__name__)

    def test_plan_from_proof_leaves_out_a_key_a_checkpoint_lacks(self):
        p = {"contract_sha256": "c", "design_sha256": "d", "test_commit": "t",
             "checkpoints": [self.red(), self.guard(), {k: v for k, v in self.red().items() if k != "kind"}]}
        plan = evidence.plan_from_proof(p)
        self.assertNotIn("kind", plan["checkpoints"][0], "a kind-less checkpoint stays kind-less")
        self.assertEqual(plan["checkpoints"][1]["kind"], "guard")
        self.assertNotIn("expected_failure", plan["checkpoints"][1])
        self.assertEqual(evidence.digest(plan), proof.digest(proof.plan_from(p, "t")))

    def test_the_bundle_counts_guards_beside_criteria(self):
        source = inspect.getsource(evidence.verify_proof)
        self.assertIn('"guards"', source)


# --- the contract --------------------------------------------------------------------------------


class ContractKindTests(unittest.TestCase):
    def contract(self, behaviors) -> dict:
        return {
            "version": "2.0", "issue": {"number": 103, "title": "t"},
            "summary": "a summary long enough to pass",
            "behaviors": behaviors, "invariants": [], "out_of_scope": [], "risks": [],
            "ambiguities": [],
        }

    @staticmethod
    def behavior(n: int, **over) -> dict:
        b = {"id": f"AC-{n}", "given": "g", "when": "w", "then": "t", "seam": "s"}
        b.update(over)
        return b

    def test_a_guard_behaviour_is_accepted_and_kept_in_the_compiled_contract(self):
        c = self.contract([self.behavior(1), self.behavior(2, kind="guard")])
        protocol.validate_contract(c, 103)
        self.assertEqual(c["behaviors"][1]["kind"], "guard")
        self.assertNotIn("kind", c["behaviors"][0], "no default is inserted")

    def test_an_explicit_behaviour_kind_is_accepted(self):
        c = self.contract([self.behavior(1, kind="behaviour")])
        protocol.validate_contract(c, 103)

    def test_an_unknown_kind_is_refused(self):
        c = self.contract([self.behavior(1, kind="invariant")])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            protocol.validate_contract(c, 103)
        self.assertIn("kind must be one of", err.getvalue())

    def test_a_contract_without_kinds_hashes_as_it_did(self):
        c = self.contract([self.behavior(1)])
        h = protocol.validate_contract(c, 103)
        self.assertEqual(h, protocol.hashlib.sha256(protocol.canonical(c)).hexdigest())
        self.assertNotIn("kind", json.dumps(c))

    def test_the_keyed_spelling_carries_the_kind_too(self):
        keyed = self.contract({"AC-1": self.behavior(1) | {}, "AC-2": {"given": "g", "when": "w", "then": "t", "seam": "s", "kind": "guard"}})
        del keyed["behaviors"]["AC-1"]["id"]
        protocol.validate_contract(keyed, 103)
        self.assertEqual(keyed["behaviors"][1]["kind"], "guard")


# --- the kernel's readers -------------------------------------------------------------------------


class KernelReadersTests(unittest.TestCase):
    def rt(self) -> KernelRuntime:
        return object.__new__(KernelRuntime)

    @staticmethod
    def pack_and_proof(checkpoints: list[dict]) -> tuple[dict, dict]:
        files = {"tests/red_test.py": "a" * 64, "tests/guard_test.py": "b" * 64}
        red = {"version": "2.0", "test_commit": "1" * 40, "files": files, "checkpoints": checkpoints}
        pack = {"artifacts": {"red-proof": {"content": red}}}
        proof_ = {**red, "green_commit": "2" * 40, "green_results": []}
        return pack, proof_

    def summary(self, checkpoints: list[dict]) -> dict:
        pack, proof_ = self.pack_and_proof(checkpoints)
        return KernelRuntime._holdout_proof_summary(
            self.rt(), proof_, pack, changed_files=[], worktree=Path("."), base="b", head="h"
        )

    def test_the_holdout_is_shown_a_guard_as_a_guard(self):
        summary = self.summary([
            {"acceptance_id": "AC-1", "red_exit": 1, "expected_failure": "aborted",
             "red_output_tail": "AssertionError: aborted"},
            {"acceptance_id": "AC-2", "kind": "guard", "red_exit": 0,
             "red_output_tail": "3 passed"},
        ])
        by_id = {r["acceptance_id"]: r for r in summary["red_results"]}
        self.assertEqual(by_id["AC-1"]["kind"], "red")
        self.assertTrue(by_id["AC-1"]["matched"])
        self.assertEqual(by_id["AC-2"], {
            "acceptance_id": "AC-2", "kind": "guard", "red_exit": 0, "expected_failure": None,
            "matched": None, "red_output_tail": "3 passed",
        })

    def test_a_guard_that_failed_at_red_refuses_before_the_holdout(self):
        with self.assertRaises(NeedsHuman) as ctx:
            self.summary([{"acceptance_id": "AC-2", "kind": "guard", "red_exit": 1}])
        self.assertIn("did not pass on the unchanged tree", str(ctx.exception))

    def test_a_guard_with_an_expected_failure_refuses_before_the_holdout(self):
        with self.assertRaises(NeedsHuman) as ctx:
            self.summary([{"acceptance_id": "AC-2", "kind": "guard", "red_exit": 0,
                           "expected_failure": "x"}])
        self.assertIn("declares an expected failure", str(ctx.exception))

    def test_a_red_checkpoint_still_needs_a_failing_exit_and_a_reason(self):
        with self.assertRaises(NeedsHuman) as ctx:
            self.summary([{"acceptance_id": "AC-1", "red_exit": 0, "expected_failure": "x"}])
        self.assertIn("did not record a failing exit", str(ctx.exception))
        with self.assertRaises(NeedsHuman) as ctx:
            self.summary([{"acceptance_id": "AC-1", "red_exit": 1}])
        self.assertIn("declares no expected failure", str(ctx.exception))

    def test_the_rehead_spec_replays_a_guard_as_a_guard(self):
        spec = KernelRuntime.rehead_spec_from({"checkpoints": [
            {"acceptance_id": "AC-1", "kind": "red", "seams": ["s"], "cwd": ".", "argv": ["x"],
             "files": ["tests/a_test.py"], "expected_failure": "boom", "red_exit": 1},
            {"acceptance_id": "AC-2", "kind": "guard", "seams": ["s"], "cwd": ".", "argv": ["y"],
             "files": ["tests/b_test.py"], "red_exit": 0},
        ]})
        self.assertEqual(spec["version"], "2.1")
        self.assertEqual(spec["checkpoints"][0]["kind"], "red")
        self.assertEqual(spec["checkpoints"][1]["kind"], "guard")
        self.assertNotIn("expected_failure", spec["checkpoints"][1])
        self.assertNotIn("seams", spec["checkpoints"][0])
        self.assertNotIn("red_exit", spec["checkpoints"][0])

    def test_the_rehead_spec_of_a_pre_guard_proof_is_the_2_0_spec_it_came_from(self):
        spec = KernelRuntime.rehead_spec_from({"checkpoints": [
            {"acceptance_id": "AC-1", "cwd": ".", "argv": ["x"], "files": ["tests/a_test.py"],
             "expected_failure": "boom", "red_exit": 1, "seams": ["s"]},
        ]})
        self.assertEqual(spec["version"], "2.0")
        self.assertEqual(spec["checkpoints"], [{
            "acceptance_id": "AC-1", "cwd": ".", "argv": ["x"], "files": ["tests/a_test.py"],
            "expected_failure": "boom",
        }])

    def test_the_rehead_uses_that_spec(self):
        source = inspect.getsource(KernelRuntime._reissue_red)
        self.assertIn("self.rehead_spec_from(", source)

    def test_the_needs_human_comment_names_a_refused_guard_as_a_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            (paths.artifacts / "green-proof-failure.json").write_text(json.dumps({
                "version": "1.0", "kind": "green", "stage": "final-green", "acceptance_id": "AC-2",
                "checkpoint_kind": "guard", "argv": ["bunx", "vitest", "run", "x.test.ts"],
                "cwd": "app/frontend", "rc": 1, "fault": None, "seconds": 1.2,
                "expected_failure": None, "reason": "AC-2 guard broken by the implementation",
                "output_tail": "FAIL x.test.ts > switching conversations aborts",
            }), encoding="utf-8")
            argv = ["python", "scripts/factory_proof.py", "green", "--proof", "p", "--output", "final-green-proof.json"]
            text = KernelRuntime._proof_failure_evidence(
                paths, ToolRefused(argv, rc=1, output="PROOF_FAIL: AC-2 guard broken")
            )
        self.assertIn("FINAL-GREEN gate evidence", text)
        self.assertIn("AC-2 ran `bunx vitest run x.test.ts`", text)
        self.assertIn("kind=guard (kept behaviour; must pass before and after)", text)
        self.assertNotIn("expected_failure=None", text)
        self.assertIn("switching conversations aborts", text)


# --- the prompts -----------------------------------------------------------------------------------


class PromptTests(unittest.TestCase):
    @staticmethod
    def read(rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_the_test_author_is_told_the_two_kinds_and_when_to_guard(self):
        text = self.read(".factory/prompts/test-author.md")
        self.assertIn("as version `2.1`", text)
        self.assertNotIn("as version `2.0`", text)
        self.assertIn('"kind": "red"', text)
        self.assertIn("`kind` is `red` or `guard`", text)
        self.assertIn(
            "An AC of kind `guard`, or whose Then says the behaviour is kept, preserved or must "
            "stay green, gets a `guard` checkpoint, never a `red` one",
            text,
        )
        self.assertIn("it has no `expected_failure`", text)
        self.assertIn("`guard failed on the unchanged tree`", text)
        self.assertIn("`guard broken by the implementation`", text)
        self.assertIn("At least one checkpoint must be `red`", text)

    def test_the_contract_worker_may_mark_a_kept_behaviour_as_a_guard(self):
        text = self.read(".factory/prompts/contract.md")
        self.assertIn('may carry `"kind": "guard"`', text)
        self.assertIn("must pass before and after the change", text)
        self.assertIn("do not use a guard for the behaviour the issue asks to change", text)

    def test_the_holdout_is_told_a_guard_is_verified_kept_behaviour(self):
        text = self.read(".factory/prompts/holdout.md")
        self.assertIn('A checkpoint with `kind: "guard"` pins behaviour the contract says is kept', text)
        self.assertIn("is verified kept behaviour, not an unverified one", text)

    def test_the_contract_certifier_is_told_a_guard_is_not_an_invented_requirement(self):
        text = KernelRuntime.CERTIFIER_QUESTIONS["contract"]
        self.assertIn("A behaviour of kind `guard` pins behaviour the issue says is kept", text)
        self.assertIn("not an unverified or invented requirement", text)

    def test_the_rules_describe_both_kinds(self):
        rules = self.read("FACTORY_RULES.md")
        self.assertIn("`kind: guard`", rules)
        self.assertIn("guard failed on the unchanged tree", rules)
        self.assertIn("guard broken by the implementation", rules)
        self.assertIn("a spec of only guards is refused", rules)


if __name__ == "__main__":
    unittest.main()
