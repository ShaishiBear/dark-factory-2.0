"""The runner that produces 428 catches is itself executed here (DFE-024).

A boundary trace of the factory suite on 2026-09-10 found that eight of the twenty-one
functions in the two mutation runners were never ENTERED by any test. Among them `apply`,
which injects a defect; `run_channels`, which decides one was caught; and `baseline_is_green`,
the guard that refuses to mutate a tree that is already red.

THE RUNNER'S FAILURE MODE IS NOT ONE WRONG NUMBER, IT IS EVERY NUMBER. A defanged catalogue
entry costs one detector (DFE-023). A runner that silently no-ops costs the whole family at
once: if `apply` returned True without writing, every defect would report injected-and-caught,
`MUTATIONS_NOT_INJECTED` would stay 0, `MUTATIONS_CAUGHT` would equal `MUTATIONS_TOTAL`, and
nothing anywhere in the repository would contradict it. The ratchet would hold. The floors
would pass. The evidence bundle would say 428/428.

These tests run the real functions against real temporary trees. They are deliberately the
harshest thing available on a runner whose output nothing else audits.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APPLICATION_RUNNER = ROOT / "harness" / "mutations" / "run.py"
FACTORY_RUNNER = ROOT / "harness" / "factory_mutations" / "run.py"


def load(path: Path, name: str):
    """Import a runner by path. They are scripts, not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ApplyActuallyChangesTheTree(unittest.TestCase):
    """`apply` returning True must mean the file on disk is different.

    The test that would have caught a no-op. It asserts the change landed AT THE LINE THE
    ANCHOR NAMES -- DFE-023's lesson applied to the mechanism rather than the catalogue,
    because an injection that writes somewhere else is as useless as one that writes nowhere.
    """

    def setUp(self):
        self.runner = load(APPLICATION_RUNNER, "_mutation_runner_under_test")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._root = mock.patch.object(self.runner, "ROOT", self.tmp)
        self._root.start()
        self.addCleanup(self._root.stop)

    def _write(self, rel: str, text: str) -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_successful_apply_leaves_the_file_different(self):
        before = "alpha\nCAP = 25\nomega\n"
        target = self._write("app/x.py", before)
        ok = self.runner.apply({"file": "app/x.py", "find": "CAP = 25", "replace": "CAP = 100"})
        after = target.read_text(encoding="utf-8")
        self.assertTrue(ok)
        self.assertNotEqual(before, after, "apply returned True without changing the tree")
        self.assertIn("CAP = 100", after)
        self.assertNotIn("CAP = 25", after)

    def test_the_change_lands_at_the_line_the_anchor_names(self):
        """Not merely 'the file differs' -- the RIGHT line differs and the others do not."""
        target = self._write("app/x.py", "keep-me\nCAP = 25\nkeep-me-too\n")
        self.runner.apply({"file": "app/x.py", "find": "CAP = 25", "replace": "CAP = 100"})
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "keep-me")
        self.assertEqual(lines[1], "CAP = 100", "the mutated line is not the anchored one")
        self.assertEqual(lines[2], "keep-me-too")

    def test_only_the_first_occurrence_is_mutated(self):
        """`replace(..., 1)`. A defect whose anchor appears twice mutates the first, which is
        what mutation_anchors.py's ANCHOR_NOTE tells the maintainer to expect."""
        target = self._write("app/x.py", "CAP = 25\nCAP = 25\n")
        self.runner.apply({"file": "app/x.py", "find": "CAP = 25", "replace": "CAP = 100"})
        self.assertEqual(target.read_text(encoding="utf-8"), "CAP = 100\nCAP = 25\n")

    def test_a_missing_anchor_returns_false_and_writes_nothing(self):
        before = "nothing to find here\n"
        target = self._write("app/x.py", before)
        ok = self.runner.apply({"file": "app/x.py", "find": "ABSENT", "replace": "X"})
        self.assertFalse(ok, "a defect that cannot inject must not report success")
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_a_missing_file_returns_false(self):
        self.assertFalse(self.runner.apply({"file": "app/gone.py", "find": "a", "replace": "b"}))


class RunChannelsDistinguishesACatchFromAnAccident(unittest.TestCase):
    """A channel going red is only a catch if it went red FOR THE DEFECT.

    DFV-009 -- mutation testing becomes detector-specific -- turned on the code that implements
    it. `run_channels` reports (went_red, detail) per channel and the caller credits a catch;
    the risk is a channel that is red for an unrelated reason being counted as a detection.
    The runner's own answer to that is `baseline_is_green`, which refuses the whole family when
    any channel is red BEFORE anything is injected. These tests hold both halves.
    """

    def setUp(self):
        self.runner = load(APPLICATION_RUNNER, "_mutation_runner_channels")

    def _channels(self, exits: dict[str, int], stdout: dict[str, str] | None = None):
        stdout = stdout or {}
        names = list(exits)
        calls = []

        def fake_run(command, **kwargs):
            name = names[len(calls)]
            calls.append(name)
            return subprocess.CompletedProcess(
                command, exits[name], stdout=stdout.get(name, ""), stderr="")

        chans = tuple((n, ["true"]) for n in names)
        with mock.patch.object(self.runner, "CHANNELS", chans), \
             mock.patch.object(self.runner.subprocess, "run", fake_run), \
             mock.patch.object(self.runner, "remaining", lambda _d: 60.0):
            return self.runner.run_channels()

    def test_every_channel_is_evaluated_and_none_short_circuits(self):
        """Which channel noticed is the measurement, so an early red must not stop the rest."""
        out = self._channels({"quick": 1, "holdout": 1, "citation": 0, "security": 1})
        self.assertEqual(set(out), {"quick", "holdout", "citation", "security"})
        self.assertEqual([r for r, _ in out.values()], [True, True, False, True])

    def test_a_green_channel_is_not_reported_as_a_catch(self):
        out = self._channels({"quick": 0, "holdout": 0})
        self.assertEqual([r for r, _ in out.values()], [False, False],
                         "an exit code of 0 is not a detection")

    def test_an_unrelated_failure_is_still_only_one_channel(self):
        """The incidental-failure case. A channel red for its own reasons reports red -- the
        runner cannot know why -- so the guard against crediting it is that the SAME channel
        must have been green at baseline. baseline_is_green is that guard and is tested below;
        here the point is that one accidental red does not contaminate the others."""
        out = self._channels({"quick": 1, "holdout": 0, "citation": 0, "security": 0})
        self.assertTrue(out["quick"][0])
        self.assertEqual([r for n, (r, _) in out.items() if n != "quick"], [False, False, False])

    def test_a_timeout_is_red_and_named_as_a_timeout(self):
        """Never mistakable for a real catch when the detail is read."""
        def boom(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 1)
        with mock.patch.object(self.runner, "CHANNELS", (("quick", ["true"]),)), \
             mock.patch.object(self.runner.subprocess, "run", boom), \
             mock.patch.object(self.runner, "remaining", lambda _d: 5.0):
            out = self.runner.run_channels()
        red, detail = out["quick"]
        self.assertTrue(red)
        self.assertIn("timeout", detail)

    def test_the_quick_channel_reports_which_rung_failed(self):
        out = self._channels({"quick": 1}, stdout={"quick": "x\nGATE_FAILED: e2e\ny\n"})
        self.assertEqual(out["quick"][1], "e2e")
        self.assertEqual(self._channels({"quick": 1})["quick"][1], "quick")


class TheBaselineGuardRefusesARedTree(unittest.TestCase):
    """Mutating a tree that is already red proves nothing, and every catch would be spurious."""

    def setUp(self):
        self.runner = load(APPLICATION_RUNNER, "_mutation_runner_baseline")

    def test_a_red_channel_before_injection_refuses_the_family(self):
        with mock.patch.object(self.runner, "run_channels",
                               lambda: {"quick": (True, "quick"), "holdout": (False, "holdout")}):
            self.assertFalse(self.runner.baseline_is_green(),
                             "a broken gate must not be credited with catching mutations")

    def test_an_all_green_baseline_permits_the_family(self):
        with mock.patch.object(self.runner, "run_channels",
                               lambda: {"quick": (False, "quick"), "holdout": (False, "holdout")}):
            self.assertTrue(self.runner.baseline_is_green())


class TheTreeMustBeCleanBeforeMutating(unittest.TestCase):
    """`apply` writes in place, so a dirty tree means the runner cannot restore what it found."""

    def setUp(self):
        self.runner = load(APPLICATION_RUNNER, "_mutation_runner_tree")

    def test_a_dirty_tree_is_not_clean(self):
        with mock.patch.object(self.runner, "git", lambda *a: subprocess.CompletedProcess(
                a, 0, stdout=" M harness/mutations/run.py\n", stderr="")):
            self.assertFalse(self.runner.tree_is_clean())

    def test_an_empty_status_is_clean(self):
        with mock.patch.object(self.runner, "git", lambda *a: subprocess.CompletedProcess(
                a, 0, stdout="  \n", stderr="")):
            self.assertTrue(self.runner.tree_is_clean())

    def test_git_runs_from_the_repository_root(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, cwd=kwargs.get("cwd"))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch.object(self.runner.subprocess, "run", fake_run):
            self.runner.git("status", "--porcelain")
        self.assertEqual(seen["argv"], ["git", "status", "--porcelain"])
        self.assertEqual(seen["cwd"], self.runner.ROOT)


class TheFactoryFamilyRefusesWithoutAGreenImmunity(unittest.TestCase):
    def setUp(self):
        self.runner = load(FACTORY_RUNNER, "_factory_mutation_runner")

    def _immunity(self, rc: int, stdout: str):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, rc, stdout=stdout, stderr="")
        with mock.patch.object(self.runner.subprocess, "run", fake_run), \
             mock.patch.object(self.runner, "remaining", lambda _d: 60.0):
            return self.runner.immunity_is_green()

    def test_a_nonzero_exit_refuses(self):
        self.assertFalse(self._immunity(1, "IMMUNITY_FAIL: something"))

    def test_a_zero_exit_without_the_positive_marker_refuses(self):
        """Exit 0 and silence is not a pass. A checker that ran nothing looks identical to one
        that passed, which is the same reasoning as ci.py's zero-is-not-a-pass guard."""
        self.assertFalse(self._immunity(0, ""))

    def test_a_zero_exit_with_the_marker_passes(self):
        self.assertTrue(self._immunity(0, "IMMUNITY_OK entries=18 assertions=65"))


if __name__ == "__main__":
    unittest.main()
