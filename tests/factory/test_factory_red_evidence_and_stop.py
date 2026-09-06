"""A refused RED or GREEN gate names its output, and an operator stop is not a build failure.

Build run 33997386843 (issue #103) ended `red-gate ... outcome=refused` after 0.448 s with
`PROOF_FAIL: AC-1 RED failed for the wrong reason` and nothing else: the checkpoint's argv,
exit code and output were discarded by the gate, so what failed before any vitest ran could not
be read. Build run 33989911383 (issue #49) re-read the stop between stages, correctly, and then
labelled the issue `factory:needs-human` with "builder failed closed: STOPPED", as if the
operator's button were the builder's defect. These tests pin D-056: every proof refusal carries
the checkpoint's argv, cwd, rc, seconds and output tail on stderr and in
`<red|green>-proof-failure.json`; the kernel quotes that record in its needs-human comment; a
stop between stages returns the issue to `factory:accepted` with a comment naming the stop
issue, adds no `needs-human`, and charges no attempt.
"""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import importlib.util
import inspect
import io
import itertools
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.config import load_config  # noqa: E402
from factory_kernel.refusal import ToolRefused  # noqa: E402
from factory_kernel.runtime import (  # noqa: E402
    FactoryStopped,
    KernelRuntime,
    NeedsHuman,
    RunPaths,
)

SCRIPT = ROOT / "scripts" / "factory_proof.py"
_spec = importlib.util.spec_from_file_location("factory_proof_evidence", SCRIPT)
assert _spec and _spec.loader
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

STOP_DETAIL = "STOPPED: an open issue carries factory:stop\n  #120 Stop the factory: canary review"

# Checkpoint commands are small scripts on disk, not `python -c <code>`: the refusal names the
# argv, so a message carried in the argv would satisfy an "output is quoted" assertion even
# when the output was discarded.
_SCRIPTS = Path(tempfile.mkdtemp(prefix="dark-factory-proof-checkpoints-"))
atexit.register(shutil.rmtree, _SCRIPTS, True)
_COUNTER = itertools.count()


def _py(code: str) -> list[str]:
    path = _SCRIPTS / f"checkpoint_{next(_COUNTER)}.py"
    path.write_text(code + "\n", encoding="utf-8")
    return [sys.executable, str(path)]


class ProofRefusalEvidenceTests(unittest.TestCase):
    """scripts/factory_proof.py: what a refused checkpoint leaves behind."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-proof-evidence-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        patch_root = mock.patch.object(proof, "ROOT", self.root)
        patch_env = mock.patch.dict(os.environ, {"ARTIFACTS_DIR": str(self.artifacts)})
        patch_root.start()
        patch_env.start()
        self.addCleanup(patch_root.stop)
        self.addCleanup(patch_env.stop)

    def checkpoint(self, argv: list[str], expected: str = "the declared symptom") -> dict:
        return {
            "acceptance_id": "AC-1",
            "cwd": ".",
            "argv": argv,
            "files": ["tests/x.test.ts"],
            "expected_failure": expected,
            "seams": ["a.b"],
        }

    def refuse(self, fn, cp) -> str:
        err = io.StringIO()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            fn(cp)
        self.assertEqual(ctx.exception.code, 1)
        return err.getvalue()

    def failure_record(self, kind: str) -> dict:
        path = self.artifacts / f"{kind}-proof-failure.json"
        self.assertTrue(path.is_file(), f"{path.name} was not written")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_failure_artifact_names_are_the_ones_the_kernel_reads(self):
        self.assertEqual(proof.failure_artifact("red"), "red-proof-failure.json")
        self.assertEqual(proof.failure_artifact("green"), "green-proof-failure.json")

    def test_a_wrong_reason_refusal_carries_argv_cwd_rc_seconds_and_the_output(self):
        cp = self.checkpoint(
            _py("print('vitest: cannot find module ./ChatArea'); raise SystemExit(1)")
        )
        err = self.refuse(proof.prove_red, cp)
        self.assertIn("PROOF_FAIL: AC-1 RED failed for the wrong reason", err)
        self.assertIn(json.dumps(cp["argv"]), err, "the refusal names the argv that ran")
        self.assertIn("cwd: .", err)
        self.assertIn("rc: 1", err)
        self.assertRegex(err, r"seconds: \d+\.\d+")
        self.assertIn("expected_failure: 'the declared symptom'", err)
        self.assertIn("vitest: cannot find module ./ChatArea", err, "the checkpoint's own output")

    def test_a_wrong_reason_refusal_writes_red_proof_failure_json(self):
        cp = self.checkpoint(_py("print('something else'); raise SystemExit(3)"))
        self.refuse(proof.prove_red, cp)
        record = self.failure_record("red")
        self.assertEqual(record["kind"], "red")
        self.assertEqual(record["acceptance_id"], "AC-1")
        self.assertEqual(record["argv"], cp["argv"])
        self.assertEqual(record["cwd"], ".")
        self.assertEqual(record["rc"], 3)
        self.assertIsNone(record["fault"])
        self.assertIsInstance(record["seconds"], float)
        self.assertGreaterEqual(record["seconds"], 0.0)
        self.assertEqual(record["expected_failure"], "the declared symptom")
        self.assertIn("something else", record["output_tail"])
        self.assertIn("wrong reason", record["reason"])

    def test_a_missing_command_is_refused_with_the_launch_error_as_evidence(self):
        cp = self.checkpoint(["dark-factory-no-such-command-3f9a1c", "run", "test"])
        err = self.refuse(proof.prove_red, cp)
        self.assertIn("AC-1 RED command could not be launched", err)
        self.assertIn("dark-factory-no-such-command-3f9a1c", err)
        self.assertIn("rc: none (never exited)", err)
        record = self.failure_record("red")
        self.assertIsNone(record["rc"])
        self.assertEqual(record["fault"], "launch")
        self.assertEqual(record["argv"], cp["argv"])
        self.assertIn("dark-factory-no-such-command-3f9a1c", record["output_tail"])

    def test_a_missing_command_is_never_red_even_if_the_error_matches_the_symptom(self):
        """A checkpoint that never ran cannot have shown the symptom; the verdict is unchanged."""
        cp = self.checkpoint(["dark-factory-no-such-command-3f9a1c"], expected="no such")
        err = self.refuse(proof.prove_red, cp)
        self.assertIn("could not be launched", err)

    def test_an_unexpected_pass_carries_the_same_evidence(self):
        cp = self.checkpoint(_py("print('all 4 tests passed')"))
        err = self.refuse(proof.prove_red, cp)
        self.assertIn("AC-1 RED command unexpectedly passed", err)
        self.assertIn("all 4 tests passed", err)
        self.assertEqual(self.failure_record("red")["rc"], 0)

    def test_a_green_refusal_writes_green_proof_failure_json_with_the_stage(self):
        cp = self.checkpoint(
            _py("import sys; print('FAIL src/x.test.ts', file=sys.stderr); sys.exit(1)")
        )
        err = self.refuse(lambda c: proof.prove_green(c, "final-green"), cp)
        self.assertIn("AC-1 GREEN command failed", err)
        self.assertIn("FAIL src/x.test.ts", err)
        record = self.failure_record("green")
        self.assertEqual(record["kind"], "green")
        self.assertEqual(record["stage"], "final-green")
        self.assertEqual(record["rc"], 1)
        self.assertIn("FAIL src/x.test.ts", record["output_tail"])
        self.assertFalse((self.artifacts / "red-proof-failure.json").exists())

    def test_a_green_launch_failure_is_refused_with_evidence(self):
        cp = self.checkpoint(["dark-factory-no-such-command-3f9a1c"])
        err = self.refuse(proof.prove_green, cp)
        self.assertIn("AC-1 GREEN command could not be launched", err)
        self.assertEqual(self.failure_record("green")["fault"], "launch")

    def test_the_output_tail_is_the_last_lines_and_capped(self):
        many = "\n".join(f"line {i}" for i in range(300))
        tail = proof.output_tail(many)
        self.assertEqual(len(tail.splitlines()), proof.OUTPUT_TAIL_LINES)
        self.assertTrue(tail.endswith("line 299"))
        self.assertNotIn("line 200\n", tail)
        huge = "x" * 50_000
        self.assertEqual(len(proof.output_tail(huge)), proof.OUTPUT_TAIL_CHARS)
        self.assertEqual(proof.OUTPUT_TAIL_LINES, 80)
        self.assertEqual(proof.OUTPUT_TAIL_CHARS, 6000)

    def test_a_refusal_quotes_the_tail_not_the_whole_output(self):
        code = (
            "print('\\n'.join('noise %d' % i for i in range(500)))\n"
            "print('final line')\n"
            "raise SystemExit(1)"
        )
        err = self.refuse(proof.prove_red, self.checkpoint(_py(code)))
        self.assertIn("final line", err)
        self.assertNotIn("noise 0\n", err)
        self.assertLessEqual(len(err), proof.OUTPUT_TAIL_CHARS + 1500)

    def test_a_proved_red_checkpoint_records_its_seconds(self):
        cp = self.checkpoint(_py("print('THE DECLARED symptom here'); raise SystemExit(1)"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = proof.prove_red(cp)
        self.assertEqual(result["red_exit"], 1)
        self.assertIsInstance(result["red_seconds"], float)
        self.assertGreaterEqual(result["red_seconds"], 0.0)
        self.assertIn("THE DECLARED symptom here", result["red_output_tail"])
        self.assertEqual(len(result["red_output_sha256"]), 64)
        self.assertRegex(out.getvalue(), r"RED_CHECKPOINT AC-1 rc=1 seconds=\d")
        self.assertFalse(
            (self.artifacts / "red-proof-failure.json").exists(), "no refusal, no record"
        )

    def test_a_proved_green_checkpoint_records_its_seconds(self):
        cp = self.checkpoint(_py("print('ok')"))
        with contextlib.redirect_stdout(io.StringIO()):
            result = proof.prove_green(cp)
        self.assertEqual(result["exit"], 0)
        self.assertIsInstance(result["seconds"], float)
        self.assertEqual(result["acceptance_id"], "AC-1")

    def test_red_and_green_run_every_checkpoint_through_the_evidence_path(self):
        red = inspect.getsource(proof.red)
        green = inspect.getsource(proof.green)
        self.assertIn("prove_red(cp)", red)
        self.assertIn("prove_green(cp", green)
        self.assertNotIn("die(f\"{cp['acceptance_id']} RED", red, "no bare RED refusal remains")
        self.assertNotIn(
            "die(f\"{cp['acceptance_id']} GREEN", green, "no bare GREEN refusal remains"
        )
        self.assertIn("seconds=", red)
        self.assertIn("seconds=", green)

    def test_the_launch_failure_runs_in_the_kernel_copy_too(self):
        """`run` reports a fault rather than raising; a traceback was the old evidence-free path."""
        rc, out, seconds, fault = proof.run(["dark-factory-no-such-command-3f9a1c"], ".")
        self.assertIsNone(rc)
        self.assertEqual(fault, "launch")
        self.assertIn("dark-factory-no-such-command-3f9a1c", out)
        self.assertGreaterEqual(seconds, 0.0)


class FakeGitHub:
    """What build_issue touches up to its first model stage, recording every call."""

    def __init__(self, *, labels=("factory:accepted",), comments=()):
        self.cwd = "."
        self._labels = set(labels)
        self.added: list[tuple[int, str]] = []
        self.removed: list[tuple[int, str]] = []
        self.comments: list[tuple[int, str]] = []
        self._existing = [{"body": c} for c in comments]
        self.pushed: list[str] = []
        self.created_prs: list[dict] = []

    def issue(self, number):
        return {
            "number": number,
            "title": "canary",
            "body": "please",
            "labels": [{"name": n} for n in sorted(self._labels)],
        }

    @staticmethod
    def labels(value):
        return {item["name"] for item in value.get("labels", [])}

    def json(self, argv):
        return {"comments": list(self._existing) + [{"body": b} for _, b in self.comments]}

    def add_issue_label(self, number, label):
        self.added.append((number, label))
        self._labels.add(label)

    def remove_issue_label(self, number, label):
        self.removed.append((number, label))
        self._labels.discard(label)

    def comment_issue(self, number, body):
        self.comments.append((number, body))

    def push_branch(self, branch, **kw):
        self.pushed.append(branch)

    def create_pr(self, **kw):
        self.created_prs.append(kw)
        return {"number": 77}

    def add_pr_label(self, number, label):
        pass

    def remove_pr_label(self, number, label):
        pass


@dataclasses.dataclass
class FakeWorktree:
    path: Path


class BuildStopTests(unittest.TestCase):
    """build_issue driven to its first model stage against fakes; the stage raises the stop."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-build-stop-")
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.work_root = home / "work"
        self.work_root.mkdir()
        self.worktree = home / "worktree"
        self.worktree.mkdir()
        config = load_config(ROOT / ".factory" / "kernel.json")
        config = dataclasses.replace(
            config, runtime=dataclasses.replace(config.runtime, work_root=self.work_root)
        )
        self.config = config

    def runtime(self, github: FakeGitHub, *, stage) -> KernelRuntime:
        rt = KernelRuntime(repo_root=ROOT, config=self.config)
        rt.github = github  # type: ignore[assignment]
        rt.check_stop = lambda: None  # type: ignore[method-assign]
        rt._fetch_main = lambda: None  # type: ignore[method-assign]
        rt._git = lambda *args, cwd=None: "a" * 40  # type: ignore[method-assign]
        rt._prepare_worktree = lambda cwd, paths: None  # type: ignore[method-assign]
        rt._issue_frontier = lambda issue: {"version": "1.0", "issue": dict(issue), "blockers": []}  # type: ignore[method-assign]
        self.leases: list[tuple[str, str]] = []
        rt._lease_heartbeat = lambda action, issue, stage_name, paths, *, cwd, pr=None: (
            self.leases.append((action, stage_name))
        )  # type: ignore[method-assign]
        self.paths_seen: list[RunPaths] = []

        def agent(role, cwd, paths, *, context="", env):
            self.paths_seen.append(paths)
            stage(role, paths)

        rt._agent = agent  # type: ignore[method-assign]
        return rt

    def build(self, github: FakeGitHub, *, stage, expect=FactoryStopped):
        rt = self.runtime(github, stage=stage)
        removed: list[Path] = []
        with (
            mock.patch(
                "factory_kernel.runtime.create_detached", return_value=FakeWorktree(self.worktree)
            ),
            mock.patch(
                "factory_kernel.runtime.remove",
                side_effect=lambda repo, wt: removed.append(wt.path),
            ),
            contextlib.redirect_stdout(io.StringIO()) as out,
            self.assertRaises(expect) as ctx,
        ):
            rt.build_issue(49)
        return rt, ctx.exception, out.getvalue()

    @staticmethod
    def stop(role, paths):
        raise FactoryStopped(STOP_DETAIL)

    def test_a_stop_between_stages_returns_the_issue_to_accepted(self):
        gh = FakeGitHub()
        self.build(gh, stage=self.stop)
        self.assertIn((49, "factory:in-progress"), gh.added, "the build claimed the issue")
        self.assertIn((49, "factory:in-progress"), gh.removed, "and released the claim")
        self.assertNotIn((49, "factory:accepted"), gh.removed, "accepted was never taken away")
        self.assertIn("factory:accepted", gh._labels)
        self.assertNotIn("factory:in-progress", gh._labels)

    def test_a_stop_is_not_needs_human(self):
        gh = FakeGitHub()
        _, exc, out = self.build(gh, stage=self.stop)
        self.assertNotIn((49, "factory:needs-human"), gh.added)
        self.assertNotIn("factory:needs-human", gh._labels)
        for _, body in gh.comments:
            self.assertNotIn("without merging", body, "the failure wording is not used for a stop")
            self.assertNotIn("builder failed closed", body)
        self.assertIn("FACTORY_BUILD_STOPPED issue=#49 stop=120", out)
        self.assertEqual(str(exc), STOP_DETAIL, "the stop propagates unchanged to the CLI")

    def test_the_stop_comment_names_the_stop_issue(self):
        gh = FakeGitHub()
        self.build(gh, stage=self.stop)
        self.assertEqual(len(gh.comments), 1, gh.comments)
        number, body = gh.comments[0]
        self.assertEqual(number, 49)
        self.assertIn("stop issue #120", body)
        self.assertIn("operator stop", body)
        self.assertIn("factory:accepted", body)
        self.assertIn("No PR was opened", body)
        self.assertIn("Stop the factory: canary review", body, "the stop check output is quoted")

    def test_a_stop_is_not_counted_as_an_attempt(self):
        gh = FakeGitHub()
        rt, _, _ = self.build(gh, stage=self.stop)
        for _, body in gh.comments:
            self.assertNotIn(KernelRuntime.VALIDATION_FAILURE_MARKER, body)
        self.assertEqual(rt._next_build_attempt(49), 1, "the next build is still attempt 1")

    def test_a_stop_after_the_lease_was_taken_finishes_the_lease(self):
        def stop_after_lease(role, paths):
            (paths.artifacts / "factory-lease.json").write_text("{}", encoding="utf-8")
            raise FactoryStopped(STOP_DETAIL)

        gh = FakeGitHub()
        self.build(gh, stage=stop_after_lease)
        self.assertIn(("finish", "stopped"), self.leases)

    def test_a_stop_before_any_lease_touches_no_lease(self):
        gh = FakeGitHub()
        self.build(gh, stage=self.stop)
        self.assertEqual(self.leases, [])

    def test_a_stop_pushes_nothing_and_opens_no_pr(self):
        gh = FakeGitHub()
        self.build(gh, stage=self.stop)
        self.assertEqual(gh.pushed, [])
        self.assertEqual(gh.created_prs, [])

    def test_a_kill_file_stop_without_an_issue_number_is_still_not_a_failure(self):
        def kill_file(role, paths):
            raise FactoryStopped(
                "STOPPED: /work/.factory-stop present. Remove it to resume.\nreason: pause"
            )

        gh = FakeGitHub()
        _, _, out = self.build(gh, stage=kill_file)
        self.assertNotIn((49, "factory:needs-human"), gh.added)
        body = gh.comments[0][1]
        self.assertIn("no stop issue number was reported", body)
        self.assertIn(".factory-stop present", body)
        self.assertIn("stop=-", out)

    def test_a_build_failure_still_escalates_exactly_as_before(self):
        """The stop handler narrows nothing: every other failure is still needs-human."""

        def fail(role, paths):
            raise NeedsHuman("architecture governor returned reject: no seam")

        gh = FakeGitHub()
        self.build(gh, stage=fail, expect=NeedsHuman)
        self.assertIn((49, "factory:needs-human"), gh.added)
        self.assertIn((49, "factory:accepted"), gh.removed)
        self.assertIn((49, "factory:in-progress"), gh.removed)
        body = gh.comments[0][1]
        self.assertIn("Dark Factory stopped this run without merging.", body)
        self.assertIn("architecture governor returned reject", body)

    def test_a_generic_exception_still_reads_builder_failed_closed(self):
        def boom(role, paths):
            raise RuntimeError("factory worker left the worktree dirty")

        gh = FakeGitHub()
        self.build(gh, stage=boom, expect=RuntimeError)
        self.assertIn((49, "factory:needs-human"), gh.added)
        self.assertIn(
            "builder failed closed: factory worker left the worktree dirty", gh.comments[0][1]
        )

    def test_the_stop_handler_precedes_the_failure_handlers_in_build_issue(self):
        source = inspect.getsource(KernelRuntime.build_issue)
        stop = source.index("except FactoryStopped as exc:")
        human = source.index("except NeedsHuman as exc:")
        generic = source.index("except Exception as exc:")
        self.assertLess(stop, human)
        self.assertLess(human, generic)
        self.assertIn("_release_stopped_build(", source[stop:human])
        self.assertNotIn("_mark_issue_human", source[stop:human])


class ProofEvidenceInTheCommentTests(unittest.TestCase):
    """The needs-human comment quotes the refused checkpoint's rc and output tail."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-proof-comment-")
        self.addCleanup(self.tmp.cleanup)
        self.paths = RunPaths.create(Path(self.tmp.name), "issue-103-a1-deadbeef00")
        config = load_config(ROOT / ".factory" / "kernel.json")
        self.rt = KernelRuntime(repo_root=ROOT, config=config)
        self.gh = FakeGitHub()
        self.rt.github = self.gh  # type: ignore[assignment]

    def record(self, kind="red", **overrides):
        value = {
            "version": "1.0",
            "kind": kind,
            "stage": kind,
            "acceptance_id": "AC-1",
            "argv": ["bun", "x", "vitest", "run", "src/components/ChatArea.test.tsx"],
            "cwd": "app/frontend",
            "rc": 1,
            "fault": None,
            "seconds": 0.412,
            "expected_failure": "timestamp link unavailable",
            "reason": "AC-1 RED failed for the wrong reason",
            "output_tail": (
                "error: script \"vitest\" exited with code 1\nCannot find module 'jsdom'"
            ),
        }
        value.update(overrides)
        (self.paths.artifacts / f"{kind}-proof-failure.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        return value

    def refused(self, phase="red"):
        argv = ["python", "scripts/factory_proof.py", phase, "--spec", "x", "--output", "y"]
        return ToolRefused(argv, rc=1, output="PROOF_FAIL: AC-1 RED failed for the wrong reason")

    def test_the_evidence_carries_rc_argv_cwd_seconds_and_the_tail(self):
        self.record()
        text = self.rt._proof_failure_evidence(self.paths, self.refused())
        self.assertIn("RED gate evidence", text)
        self.assertIn("red-proof-failure.json", text)
        self.assertIn("rc=1", text)
        self.assertIn("seconds=0.412", text)
        self.assertIn("bun x vitest run src/components/ChatArea.test.tsx", text)
        self.assertIn("app/frontend", text)
        self.assertIn("Cannot find module 'jsdom'", text)

    def test_the_needs_human_comment_includes_the_evidence(self):
        self.record()
        exc = self.refused()
        self.rt._mark_issue_human(
            103,
            f"builder failed closed: {exc}",
            evidence=self.rt._proof_failure_evidence(self.paths, exc),
        )
        body = self.gh.comments[0][1]
        self.assertIn("Dark Factory stopped this run without merging.", body)
        self.assertIn("rc=1", body)
        self.assertIn("Cannot find module 'jsdom'", body)
        self.assertIn((103, "factory:needs-human"), self.gh.added)

    def test_the_green_record_is_read_for_a_green_refusal(self):
        self.record(
            "green",
            stage="final-green",
            reason="AC-1 GREEN command failed",
            output_tail="FAIL src/components/ChatArea.test.tsx > renders",
        )
        text = self.rt._proof_failure_evidence(self.paths, self.refused("green"))
        self.assertIn("FINAL-GREEN gate evidence", text)
        self.assertIn("FAIL src/components/ChatArea.test.tsx > renders", text)

    def test_the_tail_in_the_comment_is_capped_and_scrubbed(self):
        # Assembled at runtime so no added line of this file is shaped like a real key; the
        # security guard refuses a high-confidence key pattern wherever it appears (PR #102).
        fake_key = "sk-" + "notakey0" * 5
        self.record(output_tail="x" * 10_000 + f"\nOPENROUTER_API_KEY = '{fake_key}'")
        text = self.rt._proof_failure_evidence(self.paths, self.refused())
        self.assertNotIn(fake_key, text)
        self.assertIn("[REDACTED]", text)
        self.assertLessEqual(len(text), 3000 + 600)

    def test_no_record_means_no_evidence(self):
        text = self.rt._proof_failure_evidence(self.paths, self.refused())
        self.assertEqual(text, "")

    def test_a_refusal_from_another_tool_adds_nothing(self):
        self.record()
        other = ToolRefused(["python", "scripts/factory_protocol.py", "contract"], rc=1, output="x")
        self.assertEqual(self.rt._proof_failure_evidence(self.paths, other), "")
        self.assertEqual(self.rt._proof_failure_evidence(self.paths, NeedsHuman("x")), "")

    def test_build_issue_hands_the_evidence_to_both_failure_handlers(self):
        source = inspect.getsource(KernelRuntime.build_issue)
        human = source.index("except NeedsHuman as exc:")
        generic = source.index("except Exception as exc:")
        finally_ = source.index("finally:", generic)
        # `_failure_evidence` is the proof record (D-056) plus the draft-deadline reads (D-057).
        self.assertIn("evidence=self._failure_evidence(paths, exc)", source[human:generic])
        self.assertIn("evidence=self._failure_evidence(paths, exc)", source[generic:finally_])


if __name__ == "__main__":
    unittest.main()
