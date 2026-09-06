"""A refused RED is handed back to its author once, with its evidence (D-069).

Build run 34054922788 (issue #103) is the deepest the factory has reached. The test author
drafted, the static gate's formatter pass fixed the whitespace, the turn cap did not fire, and
the RED gate refused with full evidence:

    PROOF_FAIL: AC-1 RED failed for the wrong reason
      argv: ["npx","vitest","run","src/__tests__/ChatArea-strictmode-first-send.test.tsx"]
      cwd: app/frontend   rc: 1   seconds: 4.527
      expected_failure: 'Unable to find an accessible element with the role "button"'

The test failed exactly as intended. The declared string was simply not what that command
printed: the author declared the message `getByRole` produces while the test's own query is a
label query, which prints another. One mis-declared string ended a build of about $15 and
forty-five minutes with the correct test already on disk.

The static gate has had the answer since D-043: hand the checker's own output back to the role
that wrote the files, once, and end the build on a second failure. Since D-056 a RED refusal
produces exactly the evidence such a hand-back needs. These tests pin the RED hand-back: one
re-run of `test_author` with the refusal in its context, the test commit undone so the draft is
editable again, a re-run of the WHOLE gate from scratch, a second refusal that ends the build
with both attempts' records kept and both named in the needs-human comment, and a deterministic
refusal (`red_handback_weakened_spec`) of a re-draft that answered the gate by proving less.
A guard's refusal and a launch/timeout fault are not declaration errors and are not handed back.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.refusal import ToolRefused  # noqa: E402
from factory_kernel.runtime import (  # noqa: E402
    RED_FAILURE_ARTIFACT,
    RED_FAILURE_ARTIFACT_2,
    RED_HANDBACK_ATTEMPTS,
    RED_HANDBACK_WEAKENED,
    STAGE_TIMINGS,
    KernelRuntime,
    NeedsHuman,
    RunPaths,
)
from test_factory_static_gate import (  # noqa: E402
    TEST_FILE,
    ScriptedStatic,
    git,
    repo,
    runtime,
)

RED_ARGV = ("python", "scripts/factory_proof.py", "red")
AUTHOR_CONTEXT = "VALIDATED CONTRACT (task-contract.json, deterministic-compiled):\n{}"
WRONG_REASON = "AC-1 RED failed for the wrong reason"
TAIL = 'TestingLibraryElementError: Unable to find a label with the text of: /send/i'


def checkpoint(acceptance_id: str = "AC-1", kind: str = "red") -> dict:
    value: dict = {
        "acceptance_id": acceptance_id,
        "kind": kind,
        "cwd": "app/frontend",
        "argv": ["npx", "vitest", "run", TEST_FILE],
        "files": [TEST_FILE],
    }
    if kind == "red":
        value["expected_failure"] = 'Unable to find an accessible element with the role "button"'
    return value


def write_spec(paths: RunPaths, checkpoints: list[dict]) -> None:
    (paths.artifacts / "test-spec.json").write_text(
        json.dumps({"version": "2.1", "checkpoints": checkpoints}), encoding="utf-8"
    )


def failure_record(**overrides) -> dict:
    """The shape `scripts/factory_proof.refuse` writes to `red-proof-failure.json` (D-056)."""
    value = {
        "version": "1.0",
        "kind": "red",
        "stage": "red",
        "acceptance_id": "AC-1",
        "checkpoint_kind": "red",
        "argv": ["npx", "vitest", "run", TEST_FILE],
        "cwd": "app/frontend",
        "rc": 1,
        "fault": None,
        "seconds": 4.527,
        "expected_failure": 'Unable to find an accessible element with the role "button"',
        "reason": WRONG_REASON,
        "output_tail": TAIL,
    }
    value.update(overrides)
    return value


class ScriptedRed:
    """Stands in for `scripts/factory_proof.py red`: the Nth call takes the Nth verdict.

    A verdict is `None` for a proof (writes `red-proof.json`, returns the RED_PROVED line) or a
    record dict for a refusal (writes `red-proof-failure.json` exactly as the real gate does,
    then raises the same `ToolRefused` the kernel's `_exec` raises). The sentinel
    `NO_RECORD` refuses without writing a record, which is what the gate does when it refuses
    before any checkpoint runs.
    """

    NO_RECORD = "no-record"

    def __init__(self, verdicts: list, paths: RunPaths, inner) -> None:
        self.verdicts = list(verdicts)
        self.paths = paths
        self.inner = inner
        self.calls: list[list[str]] = []
        self.transcripts: list[str] = []

    def __call__(self, argv, **kwargs):
        if tuple(argv[:3]) != RED_ARGV:
            return self.inner(argv, **kwargs)
        self.calls.append(list(argv))
        verdict = self.verdicts.pop(0)
        transcript = kwargs.get("transcript")
        if transcript is not None:
            Path(transcript).parent.mkdir(parents=True, exist_ok=True)
            Path(transcript).write_text(f"call {len(self.calls)}\n", encoding="utf-8")
            self.transcripts.append(Path(transcript).name)
        if verdict is None:
            (self.paths.artifacts / "red-proof.json").write_text("{}", encoding="utf-8")
            return "RED_PROVED criteria=1\n"
        if verdict != self.NO_RECORD:
            (self.paths.artifacts / RED_FAILURE_ARTIFACT).write_text(
                json.dumps(verdict), encoding="utf-8"
            )
        raise ToolRefused(list(argv), rc=1, output=f"PROOF_FAIL: {WRONG_REASON}")


class Author:
    """A scripted `test_author`: each run writes the test file and, optionally, a new spec.

    It also records what the checkout looked like when it was asked to work, because the whole
    point of the undo is that the second run finds its own draft uncommitted and editable.
    """

    def __init__(self, runs: list[tuple[str, list[dict] | None]], root: Path, paths: RunPaths):
        self.runs = list(runs)
        self.root = root
        self.paths = paths
        self.requests: list = []
        self.seen: list[tuple[str, str]] = []

    def run(self, request, before_retry=None, **_kwargs):
        self.requests.append(request)
        self.seen.append(
            (
                git(self.root, "log", "--format=%s", "-1"),
                git(self.root, "status", "--porcelain"),
            )
        )
        text, spec = self.runs.pop(0)
        target = Path(request.cwd) / TEST_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if spec is not None:
            write_spec(self.paths, spec)
        return AgentResult(
            provider_id="fake", model="fake", content="ok", num_turns=3, duration_ms=1
        )


class RedHandbackTests(unittest.TestCase):
    """The real `_red_gate` over a real temporary Git repo, with the gate itself scripted."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-red-handback-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = repo(self.home)

    def drive(
        self,
        *,
        verdicts: list,
        author_runs: list[tuple[str, list[dict] | None]] | None = None,
        spec_before: list[dict] | None = None,
        expect=None,
    ):
        runs = author_runs or [("it('a', () => {});\n", None), ("it('b', () => {});\n", None)]
        rt, paths = runtime(self.home, self.root, None, ScriptedStatic([True] * (len(runs) + 2)))
        author = Author(runs, self.root, paths)
        rt.provider = author
        write_spec(paths, spec_before or [checkpoint()])
        env = {"ARTIFACTS_DIR": str(paths.artifacts)}
        red = ScriptedRed(verdicts, paths, rt._exec)
        pre = rt._git("rev-parse", "HEAD", cwd=self.root)
        out = io.StringIO()
        raised = None
        with (
            mock.patch("factory_kernel.worker_runtime.method_block", return_value=""),
            contextlib.redirect_stdout(out),
        ):
            rt._agent("test_author", self.root, paths, context=AUTHOR_CONTEXT, env=env)
            rt._exec = red  # type: ignore[method-assign]
            try:
                rt._red_gate(
                    self.root, paths, env, author_context=AUTHOR_CONTEXT, pre_test_head=pre
                )
            except BaseException as exc:  # the test asserts the class that came out
                raised = exc
        if expect is None:
            self.assertIsNone(raised, f"unexpected refusal: {raised!r}")
        else:
            self.assertIsInstance(raised, expect, f"expected {expect}, got {raised!r}")
        return rt, paths, author, red, raised, out.getvalue()

    # ---------- one hand-back, then the gate runs again from scratch ----------

    def test_a_wrong_reason_refusal_triggers_exactly_one_hand_back_and_a_second_red_run(self):
        _, _, author, red, _, _ = self.drive(verdicts=[failure_record(), None])
        self.assertEqual(len(author.requests), 2, "the author ran once more, not twice more")
        self.assertEqual(len(red.calls), 2, "the whole gate re-ran from scratch")

    def test_the_hand_back_carries_the_refusal_the_author_has_to_answer(self):
        _, _, author, _, _, _ = self.drive(verdicts=[failure_record(), None])
        brief = author.requests[1].prompt
        self.assertIn("RED GATE REFUSAL", brief)
        self.assertIn(WRONG_REASON, brief, "the refusal reason")
        self.assertIn("AC-1", brief, "the failing acceptance id")
        self.assertIn('["npx", "vitest", "run"', brief, "the argv that ran")
        self.assertIn("cwd: app/frontend", brief)
        self.assertIn("rc: 1", brief)
        self.assertIn("seconds: 4.527", brief)
        self.assertIn('role "button"', brief, "the expected_failure it declared")
        self.assertIn(TAIL, brief, "the output tail of that command")

    def test_the_hand_back_keeps_the_author_its_original_context(self):
        """The contract, the design and any DEFERRED REPRO SYMPTOM still reach the re-run."""
        _, _, author, _, _, _ = self.drive(verdicts=[failure_record(), None])
        self.assertIn(AUTHOR_CONTEXT.split("\n")[0], author.requests[1].prompt)

    def test_the_hand_back_forbids_weakening_and_says_the_gate_re_runs(self):
        _, _, author, _, _, _ = self.drive(verdicts=[failure_record(), None])
        brief = author.requests[1].prompt
        self.assertIn("expected_failure", brief)
        self.assertIn("query", brief, "correcting the test's own query is the other option")
        self.assertIn("strength of the test is not open to you", brief)
        self.assertIn("exactly one checkpoint", brief)
        self.assertIn("must still pass on the unchanged tree", brief, "guards still hold")
        self.assertIn("wrote or changed", brief, "a red checkpoint's file rule (D-064)")
        self.assertIn("DEFERRED REPRO SYMPTOM", brief)
        self.assertIn(RED_HANDBACK_WEAKENED, brief)
        self.assertIn("from scratch", brief)
        self.assertIn("only hand-back", brief)

    def test_the_undo_gives_the_author_back_its_own_uncommitted_draft(self):
        _, _, author, _, _, _ = self.drive(verdicts=[failure_record(), None])
        first_subject, first_status = author.seen[0]
        second_subject, second_status = author.seen[1]
        self.assertEqual(first_subject, "base")
        self.assertEqual(first_status, "")
        self.assertEqual(
            second_subject, "base", "the test-author commit was undone before the re-run"
        )
        self.assertIn(TEST_FILE, second_status, "the draft is back in the checkout, uncommitted")

    def test_a_hand_back_that_fixes_the_string_leaves_a_proved_red_and_one_commit(self):
        _, paths, _, _, _, _ = self.drive(verdicts=[failure_record(), None])
        self.assertTrue((paths.artifacts / "red-proof.json").is_file())
        self.assertEqual(
            git(self.root, "log", "--format=%s"),
            "test(factory): prove acceptance contract red\nbase",
            "the re-drafted tests are one commit on the base, not two stacked ones",
        )
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(
            (self.root / TEST_FILE).read_text(encoding="utf-8"), "it('b', () => {});\n"
        )

    def test_a_successful_hand_back_still_leaves_the_refusal_it_answered(self):
        """The record the author was shown is not the price of showing it to them."""
        _, paths, _, _, _, _ = self.drive(verdicts=[failure_record(), None])
        kept = json.loads((paths.artifacts / RED_FAILURE_ARTIFACT).read_text(encoding="utf-8"))
        self.assertEqual(kept["reason"], WRONG_REASON)
        self.assertFalse(
            (paths.artifacts / RED_FAILURE_ARTIFACT_2).exists(), "there was no second refusal"
        )

    def test_a_weakened_hand_back_still_leaves_the_refusal_it_answered(self):
        _, paths, _, _, _, _ = self.drive(
            verdicts=[failure_record(), None],
            spec_before=[checkpoint("AC-1"), checkpoint("AC-2")],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", [checkpoint("AC-1")]),
            ],
            expect=NeedsHuman,
        )
        kept = json.loads((paths.artifacts / RED_FAILURE_ARTIFACT).read_text(encoding="utf-8"))
        self.assertEqual(kept["reason"], WRONG_REASON)

    def test_both_gate_runs_keep_their_own_transcript(self):
        _, _, _, red, _, _ = self.drive(verdicts=[failure_record(), None])
        self.assertEqual(red.transcripts, ["red-gate.log", "red-gate.2.log"])

    def test_the_hand_back_is_announced_on_stdout(self):
        _, _, _, _, _, out = self.drive(verdicts=[failure_record(), None])
        self.assertIn("FACTORY_RED_HANDBACK", out)
        self.assertIn("acceptance_id=AC-1", out)

    # ---------- per-attempt records and stage-run numbering (D-058) ----------

    def test_each_test_author_run_keeps_its_own_record_and_stage_run_number(self):
        _, paths, _, _, _, out = self.drive(verdicts=[failure_record(), None])
        self.assertTrue((paths.transcripts / "agent-test_author.json").is_file())
        self.assertTrue(
            (paths.transcripts / "agent-test_author.2.json").is_file(),
            "the hand-back's run does not overwrite the first run's record",
        )
        rows = [
            json.loads(line)
            for line in (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8").splitlines()
        ]
        author_rows = [row for row in rows if row.get("name") == "test_author"]
        self.assertEqual([row["stage_run"] for row in author_rows], [1, 2])
        self.assertIn("stage_run=2", out)

    # ---------- a second refusal ends the build, with both attempts kept ----------

    def test_a_second_refusal_ends_the_build(self):
        second = failure_record(reason="AC-1 RED command unexpectedly passed", rc=0)
        _, _, author, red, _, _ = self.drive(
            verdicts=[failure_record(), second], expect=ToolRefused
        )
        self.assertEqual(len(author.requests), 2)
        self.assertEqual(len(red.calls), 2, "no third gate run")

    def test_both_attempts_records_survive_under_their_own_names(self):
        second = failure_record(reason="AC-1 RED command unexpectedly passed", rc=0)
        _, paths, _, _, _, _ = self.drive(
            verdicts=[failure_record(), second], expect=ToolRefused
        )
        first_kept = json.loads(
            (paths.artifacts / RED_FAILURE_ARTIFACT).read_text(encoding="utf-8")
        )
        second_kept = json.loads(
            (paths.artifacts / RED_FAILURE_ARTIFACT_2).read_text(encoding="utf-8")
        )
        self.assertEqual(first_kept["reason"], WRONG_REASON, "the first attempt is not overwritten")
        self.assertEqual(first_kept["rc"], 1)
        self.assertEqual(second_kept["reason"], "AC-1 RED command unexpectedly passed")
        self.assertEqual(second_kept["rc"], 0)

    def test_the_needs_human_comment_names_both_attempts(self):
        second = failure_record(
            reason="AC-1 RED command unexpectedly passed", rc=0, output_tail="all 4 tests passed"
        )
        rt, paths, _, _, raised, _ = self.drive(
            verdicts=[failure_record(), second], expect=ToolRefused
        )
        text = rt._failure_evidence(paths, raised)
        self.assertIn("RED gate evidence", text, "the first refusal, quoted as always (D-056)")
        self.assertIn(TAIL, text)
        self.assertIn("RED hand-back: the gate refused twice", text)
        self.assertIn(RED_FAILURE_ARTIFACT, text)
        self.assertIn(RED_FAILURE_ARTIFACT_2, text)
        self.assertIn("red-gate.2.log", text)
        self.assertIn("AC-1 RED command unexpectedly passed", text)
        self.assertIn("all 4 tests passed", text, "the second attempt's own output tail")

    def test_a_build_with_no_hand_back_adds_no_hand_back_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            self.assertEqual(KernelRuntime._red_handback_evidence(paths), "")

    # ---------- what is NOT a declaration error ----------

    def test_a_guard_refusal_is_not_handed_back(self):
        """A guard that fails on the unchanged tree is a wrong contract or a broken tree."""
        record = failure_record(
            checkpoint_kind="guard",
            reason="AC-2 guard failed on the unchanged tree",
            acceptance_id="AC-2",
        )
        del record["expected_failure"]
        _, paths, author, red, _, _ = self.drive(verdicts=[record], expect=ToolRefused)
        self.assertEqual(len(author.requests), 1, "the author was not run again")
        self.assertEqual(len(red.calls), 1)
        self.assertTrue((paths.artifacts / RED_FAILURE_ARTIFACT).is_file())
        self.assertFalse((paths.artifacts / RED_FAILURE_ARTIFACT_2).exists())

    def test_a_launch_fault_is_not_handed_back(self):
        """A command that never ran printed nothing the author could have mis-declared."""
        record = failure_record(
            fault="launch", rc=None, reason="AC-1 RED command could not be launched"
        )
        _, _, author, red, _, _ = self.drive(verdicts=[record], expect=ToolRefused)
        self.assertEqual(len(author.requests), 1)
        self.assertEqual(len(red.calls), 1)

    def test_a_timeout_fault_is_not_handed_back(self):
        record = failure_record(fault="timeout", rc=None, reason="AC-1 RED command timed out")
        _, _, author, red, _, _ = self.drive(verdicts=[record], expect=ToolRefused)
        self.assertEqual(len(author.requests), 1)
        self.assertEqual(len(red.calls), 1)

    def test_a_refusal_before_any_checkpoint_ran_is_not_handed_back(self):
        """No record means the spec or the commit was refused, not a declaration."""
        _, _, author, red, _, _ = self.drive(
            verdicts=[ScriptedRed.NO_RECORD], expect=ToolRefused
        )
        self.assertEqual(len(author.requests), 1)
        self.assertEqual(len(red.calls), 1)

    def test_the_hand_back_is_bounded_at_one(self):
        self.assertEqual(RED_HANDBACK_ATTEMPTS, 1)
        _, _, author, red, _, _ = self.drive(
            verdicts=[failure_record(), failure_record(), failure_record()],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", None),
                ("it('c', () => {});\n", None),
            ],
            expect=ToolRefused,
        )
        self.assertEqual(len(author.requests), 2, "one hand-back, never two")
        self.assertEqual(len(red.calls), 2)

    # ---------- the hand-back is not a licence to weaken the test ----------

    def test_a_hand_back_that_drops_a_checkpoint_is_refused_by_name(self):
        _, _, _, red, raised, _ = self.drive(
            verdicts=[failure_record(), None],
            spec_before=[checkpoint("AC-1"), checkpoint("AC-2")],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", [checkpoint("AC-1")]),
            ],
            expect=NeedsHuman,
        )
        self.assertIn(RED_HANDBACK_WEAKENED, str(raised))
        self.assertIn("from 2 to 1", str(raised))
        self.assertIn("AC-2", str(raised), "the refusal names the change")
        self.assertEqual(len(red.calls), 1, "the weakened spec never reaches the gate")

    def test_a_hand_back_that_downgrades_a_red_to_a_guard_is_refused_by_name(self):
        _, _, _, red, raised, _ = self.drive(
            verdicts=[failure_record(), None],
            spec_before=[checkpoint("AC-1"), checkpoint("AC-2")],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", [checkpoint("AC-1"), checkpoint("AC-2", kind="guard")]),
            ],
            expect=NeedsHuman,
        )
        self.assertIn(RED_HANDBACK_WEAKENED, str(raised))
        self.assertIn("AC-2", str(raised))
        self.assertIn("guard", str(raised))
        self.assertEqual(len(red.calls), 1)

    def test_a_hand_back_that_keeps_the_spec_is_not_refused(self):
        _, _, _, red, raised, _ = self.drive(
            verdicts=[failure_record(), None],
            spec_before=[checkpoint("AC-1"), checkpoint("AC-2")],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", [checkpoint("AC-1"), checkpoint("AC-2")]),
            ],
        )
        self.assertIsNone(raised)
        self.assertEqual(len(red.calls), 2)

    def test_a_hand_back_that_adds_a_checkpoint_is_not_refused(self):
        """Only weakening is refused here; the gate judges everything else about the spec."""
        _, _, _, red, raised, _ = self.drive(
            verdicts=[failure_record(), None],
            spec_before=[checkpoint("AC-1")],
            author_runs=[
                ("it('a', () => {});\n", None),
                ("it('b', () => {});\n", [checkpoint("AC-1"), checkpoint("AC-2")]),
            ],
        )
        self.assertIsNone(raised)
        self.assertEqual(len(red.calls), 2)


class WeakeningCheckUnitTests(unittest.TestCase):
    """`_spec_shape` / `_refuse_weakened_spec` on their own, including the shapes never seen."""

    def shape(self, checkpoints) -> tuple[int, dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test-spec.json"
            path.write_text(json.dumps({"checkpoints": checkpoints}), encoding="utf-8")
            return KernelRuntime._spec_shape(path)

    def test_an_absent_kind_reads_as_red(self):
        self.assertEqual(
            self.shape([{"acceptance_id": "AC-1"}, {"acceptance_id": "AC-2", "kind": "guard"}]),
            (2, {"AC-1": "red", "AC-2": "guard"}),
        )

    def test_an_unreadable_spec_is_permissive_never_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertEqual(KernelRuntime._spec_shape(missing), (0, {}))
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(KernelRuntime._spec_shape(broken), (0, {}))
        KernelRuntime._refuse_weakened_spec((0, {}), (0, {}))

    def test_an_equal_or_larger_spec_passes(self):
        KernelRuntime._refuse_weakened_spec((2, {"AC-1": "red"}), (2, {"AC-1": "red"}))
        KernelRuntime._refuse_weakened_spec((1, {"AC-1": "red"}), (3, {"AC-1": "red"}))

    def test_a_guard_that_stays_a_guard_is_not_a_downgrade(self):
        KernelRuntime._refuse_weakened_spec((1, {"AC-1": "guard"}), (1, {"AC-1": "guard"}))

    def test_a_guard_promoted_to_red_is_not_a_downgrade(self):
        KernelRuntime._refuse_weakened_spec((1, {"AC-1": "guard"}), (1, {"AC-1": "red"}))

    def test_a_smaller_count_is_refused_even_when_no_id_was_dropped(self):
        with self.assertRaises(NeedsHuman) as ctx:
            KernelRuntime._refuse_weakened_spec((3, {"AC-1": "red"}), (2, {"AC-1": "red"}))
        self.assertIn(RED_HANDBACK_WEAKENED, str(ctx.exception))
        self.assertIn("from 3 to 2", str(ctx.exception))

    def test_a_downgrade_is_refused(self):
        with self.assertRaises(NeedsHuman) as ctx:
            KernelRuntime._refuse_weakened_spec(
                (2, {"AC-1": "red", "AC-2": "red"}), (2, {"AC-1": "red", "AC-2": "guard"})
            )
        self.assertIn(RED_HANDBACK_WEAKENED, str(ctx.exception))
        self.assertIn("AC-2", str(ctx.exception))


class HandbackClassificationUnitTests(unittest.TestCase):
    """`_red_handback_record`: only a red checkpoint's fault-free refusal is handed back."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-red-classify-")
        self.addCleanup(self.tmp.cleanup)
        self.paths = RunPaths.create(Path(self.tmp.name), "run")
        self.rt = object.__new__(KernelRuntime)

    def write(self, record: dict) -> None:
        (self.paths.artifacts / RED_FAILURE_ARTIFACT).write_text(
            json.dumps(record), encoding="utf-8"
        )

    def refused(self, phase: str = "red", tool: str = "scripts/factory_proof.py") -> ToolRefused:
        return ToolRefused(["python", tool, phase, "--spec", "s"], rc=1, output="PROOF_FAIL")

    def test_a_red_checkpoint_refusal_is_handed_back(self):
        self.write(failure_record())
        record = self.rt._red_handback_record(self.paths, self.refused())
        self.assertIsNotNone(record)
        self.assertEqual(record["reason"], WRONG_REASON)
        self.assertTrue(
            (self.paths.artifacts / RED_FAILURE_ARTIFACT).is_file(),
            "classifying reads the record; only the gate itself takes it off disk",
        )

    def test_a_green_refusal_is_never_handed_back(self):
        self.write(failure_record())
        self.assertIsNone(self.rt._red_handback_record(self.paths, self.refused("green")))

    def test_another_tool_is_never_handed_back(self):
        self.write(failure_record())
        other = self.refused("contract", tool="scripts/factory_protocol.py")
        self.assertIsNone(self.rt._red_handback_record(self.paths, other))
        self.assertIsNone(self.rt._red_handback_record(self.paths, NeedsHuman("x")))

    def test_a_missing_or_broken_record_is_never_handed_back(self):
        self.assertIsNone(self.rt._red_handback_record(self.paths, self.refused()))
        (self.paths.artifacts / RED_FAILURE_ARTIFACT).write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.rt._red_handback_record(self.paths, self.refused()))

    def test_an_absent_checkpoint_kind_reads_as_red(self):
        record = failure_record()
        del record["checkpoint_kind"]
        self.write(record)
        self.assertIsNotNone(self.rt._red_handback_record(self.paths, self.refused()))


class WiringTests(unittest.TestCase):
    """The build path runs the gate through the hand-back, not around it."""

    def test_build_issue_calls_the_red_gate_and_not_the_bare_exec(self):
        source = inspect.getsource(KernelRuntime.build_issue)
        self.assertIn("self._red_gate(", source)
        red = source.index("self._red_gate(")
        self.assertIn("pre_test_head=pre_test_head", source[red : red + 400])
        self.assertNotIn('"python", "scripts/factory_proof.py", "red"', source)

    def test_the_author_context_is_built_once_and_reused_by_the_hand_back(self):
        source = inspect.getsource(KernelRuntime.build_issue)
        self.assertIn("test_author_context = self._worker_brief(", source)
        self.assertIn("_deferred_symptom_brief", source)
        self.assertIn("author_context=test_author_context", source)

    def test_the_failure_evidence_includes_the_hand_back_block(self):
        source = inspect.getsource(KernelRuntime._failure_evidence)
        self.assertIn("_red_handback_evidence(paths)", source)

    def test_the_gate_undoes_the_commit_before_it_re_runs_the_author(self):
        source = inspect.getsource(KernelRuntime._red_handback)
        reset = source.index('"reset", "--mixed"')
        agent = source.index("self._agent(")
        weaken = source.index("_refuse_weakened_spec")
        self.assertLess(reset, agent, "the undo precedes the re-run")
        self.assertLess(agent, weaken, "the weakening check judges what the re-run wrote")


if __name__ == "__main__":
    unittest.main()
