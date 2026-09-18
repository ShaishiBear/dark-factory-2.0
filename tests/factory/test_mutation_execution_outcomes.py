"""What the factory mutation family is allowed to conclude from what it observed.

On 2026-09-18 the daily main regression at `73d557c` passed static, 3,503 unit tests, the
product E2E journey and three holdouts, then died inside the factory mutation family with a
`subprocess.TimeoutExpired` raised while running `tests/factory/test_probe_bundle.py`
(https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/35324653207). The exception
travelled out of `ThreadPoolExecutor.map`, so the run printed a traceback instead of 903
verdicts and every other defect's evidence went with it.

Reproducing it locally established what it was NOT: each of the 21 catalogue mutants in
`execution_probe.py`, `validation_meter.py` and `factory_test_author_probe.py` was injected
into its own copy and that test file was run alone; the slowest took 2.5 seconds and none
exceeded the 120-second file bound. No source defect in the modules that file covers makes it
slow, and the ordinary suite passes, so the runner -- not `test_probe_bundle.py` -- is what
had to change.

These tests pin the properties that change:

  * a detector that was red on the unmutated baseline cannot prove a kill;
  * an assertion failure in a baseline-green detector can;
  * a detector that fails to import observed nothing (`infra_error`), and a detector that ran
    out of time observed nothing either (`timeout`) -- neither is a kill and neither is an
    escape;
  * a survivor still runs every usable detector;
  * one worker raising does not discard the rest of the catalogue's results;
  * a shard split is only aggregable when the shards share a baseline and between them
    measured every expected mutant exactly once.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
for entry in (str(HARNESS), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


outcomes = load_module("factory_mutation_outcomes", HARNESS / "factory_mutations" / "outcomes.py")
runner = load_module("factory_mutation_runner", HARNESS / "factory_mutations" / "run.py")


PASSING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n\n\nunittest.main()\n"
FAILING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_no(self):\n        self.assertEqual(1, 2)\n\n\nunittest.main()\n"
UNIMPORTABLE = "import a_module_that_is_not_installed_anywhere\n"
SLOW = "import time\n\ntime.sleep(90)\n"
SPAWNING = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])\n"
    "print('SPAWNED', child.pid, flush=True)\n"
    "time.sleep(90)\n"
)


class ClassificationTests(unittest.TestCase):
    """Three different things exit non-zero; only one of them is about the defect."""

    def test_an_assertion_failure_in_a_detector_that_ran_is_a_behavioural_observation(self):
        output = "F\n" + "=" * 20 + "\nFAIL: test_no\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n"
        self.assertEqual(outcomes.classify_detector(1, output), outcomes.FAILED)

    def test_a_detector_that_never_ran_its_tests_observed_nothing(self):
        traceback = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'x'\n"
        self.assertEqual(outcomes.classify_detector(1, traceback), outcomes.INFRA_ERROR)

    def test_a_detector_killed_by_a_signal_observed_nothing(self):
        self.assertEqual(outcomes.classify_detector(-9, "Ran 4 tests"), outcomes.INFRA_ERROR)

    def test_a_timeout_is_its_own_state_and_never_a_kill(self):
        self.assertEqual(
            outcomes.classify_detector(None, "", timed_out=True), outcomes.TIMEOUT)
        self.assertNotIn(outcomes.TIMEOUT, (outcomes.CAUGHT, outcomes.PASSED))

    def test_a_green_detector_passed(self):
        self.assertEqual(outcomes.classify_detector(0, "Ran 2 tests\n\nOK\n"), outcomes.PASSED)

    def test_a_caught_result_must_name_the_detector_that_caught_it(self):
        with self.assertRaises(ValueError):
            outcomes.MutationResult("m", "d", outcomes.CAUGHT, "", "b", 1, "")
        with self.assertRaises(ValueError):
            outcomes.MutationResult("m", "d", "invented_state", "t", "b", 1, "")


class DetectorProcessTests(unittest.TestCase):
    """The bound is enforced here, so a slow detector is a status rather than an exception."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name: str, body: str) -> str:
        (self.tmp / name).write_text(body, encoding="utf-8")
        return name

    def test_a_detector_that_exceeds_its_bound_is_recorded_not_raised(self):
        rel = self.write("slow.py", SLOW)
        status, _, _, elapsed_ns = outcomes.run_detector(
            [sys.executable, rel], cwd=self.tmp, env=dict(os.environ), seconds=2)
        self.assertEqual(status, outcomes.TIMEOUT)
        self.assertLess(elapsed_ns, 60 * 1_000_000_000, "the bound was not enforced")

    def test_a_timed_out_detectors_descendants_are_reaped(self):
        rel = self.write("spawn.py", SPAWNING)
        status, _, output, _ = outcomes.run_detector(
            [sys.executable, rel], cwd=self.tmp, env=dict(os.environ), seconds=4)
        self.assertEqual(status, outcomes.TIMEOUT)
        pids = [int(line.split()[1]) for line in output.splitlines() if line.startswith("SPAWNED")]
        for pid in pids:
            self.assertFalse(_alive(pid), f"child {pid} outlived the detector that started it")

    def test_a_failing_and_a_passing_detector_are_told_apart_by_running_them(self):
        self.assertEqual(outcomes.run_detector([sys.executable, self.write("ok.py", PASSING)],
                                               cwd=self.tmp, env=dict(os.environ), seconds=60)[0],
                         outcomes.PASSED)
        self.assertEqual(outcomes.run_detector([sys.executable, self.write("no.py", FAILING)],
                                               cwd=self.tmp, env=dict(os.environ), seconds=60)[0],
                         outcomes.FAILED)
        self.assertEqual(outcomes.run_detector([sys.executable, self.write("bad.py", UNIMPORTABLE)],
                                               cwd=self.tmp, env=dict(os.environ), seconds=60)[0],
                         outcomes.INFRA_ERROR)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                               text=True, check=False)
        return str(pid) in (found.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class EvaluationTests(unittest.TestCase):
    """`evaluate` over a tiny synthetic suite: the real control flow, no 167-file copy."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.suite = self.tmp / "suite"
        self.suite.mkdir()
        self.files: list[str] = []
        self.enterContext(mock.patch.object(runner, "build_copy", self.fake_copy))

    def fake_copy(self, parent: Path) -> Path:
        target = Path(parent) / "root"
        target.mkdir(parents=True, exist_ok=True)
        for name in self.files:
            (target / name).write_text((self.suite / name).read_text(encoding="utf-8"),
                                       encoding="utf-8")
        (target / "subject.py").write_text("VALUE = 1\n", encoding="utf-8")
        return target

    def detector(self, name: str, body: str) -> str:
        (self.suite / name).write_text(body, encoding="utf-8")
        self.files.append(name)
        return name

    def defect(self, **extra) -> dict:
        return {"id": "probe-defect", "file": "subject.py", "find": "VALUE = 1",
                "replace": "VALUE = 2", "why": "a probe", **extra}

    def baseline(self, statuses: dict[str, str]) -> dict:
        return {"baseline_ref": "b" * 64,
                "detectors": {name: {"status": status, "seconds": 0.1}
                              for name, status in statuses.items()}}

    def evaluate(self, baseline, order=None, target=None):
        with mock.patch.object(runner, "TEST_FILES", tuple(self.files)):
            return runner.evaluate(self.defect(), tuple(order or self.files), baseline,
                                   None, target)

    def test_a_detector_that_was_red_on_the_baseline_cannot_prove_a_kill(self):
        red = self.detector("test_red.py", FAILING)
        green = self.detector("test_green.py", PASSING)
        result = self.evaluate(self.baseline({red: outcomes.FAILED, green: outcomes.PASSED}))
        self.assertEqual(result.state, outcomes.SURVIVED,
                         "a detector red without the mutant proves nothing with it")
        self.assertEqual(result.detector, "")

    def test_an_assertion_failure_in_a_baseline_green_detector_is_a_kill(self):
        green = self.detector("test_green.py", PASSING)
        killer = self.detector("test_killer.py", FAILING)
        result = self.evaluate(self.baseline({green: outcomes.PASSED, killer: outcomes.PASSED}))
        self.assertEqual((result.state, result.detector), (outcomes.CAUGHT, killer))
        self.assertEqual(len(result.injected_digest), 64, "the mutated bytes are identified")

    def test_a_detector_that_cannot_be_collected_is_an_infrastructure_error(self):
        broken = self.detector("test_broken.py", UNIMPORTABLE)
        result = self.evaluate(self.baseline({broken: outcomes.PASSED}))
        self.assertEqual(result.state, outcomes.INFRA_ERROR)
        self.assertNotEqual(result.state, outcomes.CAUGHT)

    def test_a_detector_that_runs_out_of_time_leaves_the_mutant_unobserved(self):
        slow = self.detector("test_slow.py", SLOW)
        with mock.patch.object(runner, "detector_seconds", lambda: 2):
            result = self.evaluate(self.baseline({slow: outcomes.PASSED}))
        self.assertEqual(result.state, outcomes.TIMEOUT)
        self.assertNotIn(result.state, (outcomes.CAUGHT, outcomes.SURVIVED))

    def test_a_survivor_runs_every_usable_detector_before_saying_so(self):
        names = [self.detector(f"test_p{n}.py", PASSING) for n in range(4)]
        skipped = self.detector("test_skipped.py", FAILING)
        statuses = {name: outcomes.PASSED for name in names}
        statuses[skipped] = outcomes.FAILED
        seen: list[str] = []
        real = outcomes.run_detector

        def watched(argv, **kwargs):
            seen.append(Path(argv[-1]).name)
            return real(argv, **kwargs)

        with mock.patch.object(runner, "run_detector", watched):
            result = self.evaluate(self.baseline(statuses))
        self.assertEqual(result.state, outcomes.SURVIVED)
        self.assertEqual(sorted(seen), sorted(names),
                         "a survivor must exhaust the usable suite, and only the usable suite")

    def test_the_order_is_a_permutation_so_no_detector_is_dropped_from_the_fallback(self):
        names = [self.detector(f"test_p{n}.py", PASSING) for n in range(5)]
        with mock.patch.object(runner, "TEST_FILES", tuple(names)):
            order = runner.detector_order(self.defect(why=f"named {names[3]}"),
                                          tuple(names), {"probe-defect": names[4]})
        self.assertEqual(sorted(order), sorted(names))
        self.assertEqual(order[0], names[4], "the remembered causal detector is asked first")

    def test_each_mutant_publishes_its_own_immutable_result_file(self):
        killer = self.detector("test_killer.py", FAILING)
        target = self.tmp / "results"
        result = self.evaluate(self.baseline({killer: outcomes.PASSED}), target=target)
        record = json.loads((target / "probe-defect.json").read_text(encoding="utf-8"))
        self.assertEqual(record["state"], outcomes.CAUGHT)
        self.assertEqual(record["detectors"][0]["detector"], killer)
        self.assertEqual(result.diagnostic_ref, "probe-defect.json")
        self.assertEqual(list(target.glob("*.partial")), [], "a half-written result is not a verdict")


class PoolTests(unittest.TestCase):
    """One worker's failure is that mutant's missing observation, not the run's."""

    def test_a_worker_exception_becomes_that_mutants_infra_error_and_keeps_the_rest(self):
        defects = [{"id": f"d{n}", "why": "w"} for n in range(6)]

        def flaky(defect, order, baseline=None, causal=None, target=None):
            if defect["id"] == "d3":
                raise RuntimeError("copy failed")
            return outcomes.MutationResult(defect["id"], "", outcomes.CAUGHT, "t", "b", 1, "")

        for workers in (1, 4):
            with mock.patch.object(runner, "evaluate", flaky):
                results = runner.evaluate_all(defects, (), workers, {"baseline_ref": "b"})
            self.assertEqual([r.mutant_id for r in results], [d["id"] for d in defects],
                             f"a defect was dropped with workers={workers}")
            failed = [r for r in results if r.mutant_id == "d3"]
            self.assertEqual(failed[0].state, outcomes.INFRA_ERROR)
            self.assertNotEqual(failed[0].state, outcomes.CAUGHT)


class ShardTests(unittest.TestCase):
    """A split of one catalogue, or an incomplete aggregate that says what is missing."""

    def test_the_split_is_deterministic_disjoint_and_complete(self):
        ids = [f"m{n:03d}" for n in range(37)]
        shards = [outcomes.shard_members(ids, 4, index) for index in range(4)]
        flat = [mutant for shard in shards for mutant in shard]
        self.assertEqual(sorted(flat), sorted(ids))
        self.assertEqual(len(flat), len(set(flat)), "a mutant belongs to exactly one shard")
        self.assertEqual(shards, [outcomes.shard_members(list(reversed(ids)), 4, index)
                                  for index in range(4)], "input order cannot move the split")

    def manifest(self, index, count, expected, measured, baseline="b" * 64):
        results = [outcomes.MutationResult(mutant, "d", outcomes.CAUGHT, "t", baseline, 1, "").as_record()
                   for mutant in measured]
        return {"shard_index": index, "shard_count": count, "baseline_ref": baseline,
                "expected_ids": expected, "results": results}

    def test_a_missing_shard_is_incomplete_rather_than_green(self):
        expected = ["a", "b", "c", "d"]
        aggregate = outcomes.aggregate_manifests([self.manifest(0, 2, expected, ["a", "c"])])
        self.assertEqual(aggregate["status"], "incomplete")
        self.assertEqual(sorted(aggregate["incomplete_ids"]), ["b", "d"])
        self.assertTrue(any("never run" in reason for reason in aggregate["reasons"]))

    def test_a_duplicated_mutant_means_the_split_was_not_a_split(self):
        expected = ["a", "b"]
        aggregate = outcomes.aggregate_manifests([self.manifest(0, 2, expected, ["a", "b"]),
                                                  self.manifest(1, 2, expected, ["b"])])
        self.assertEqual(aggregate["status"], "incomplete")
        self.assertTrue(any("more than once" in reason for reason in aggregate["reasons"]))

    def test_shards_measuring_different_baselines_cannot_be_aggregated(self):
        expected = ["a", "b"]
        aggregate = outcomes.aggregate_manifests([self.manifest(0, 2, expected, ["a"]),
                                                  self.manifest(1, 2, expected, ["b"], "c" * 64)])
        self.assertEqual(aggregate["status"], "incomplete")
        self.assertTrue(any("different baselines" in reason for reason in aggregate["reasons"]))

    def test_complete_disjoint_shards_on_one_baseline_aggregate_to_green(self):
        expected = ["a", "b", "c", "d"]
        aggregate = outcomes.aggregate_manifests([self.manifest(0, 2, expected, ["a", "c"]),
                                                  self.manifest(1, 2, expected, ["b", "d"])])
        self.assertEqual((aggregate["status"], aggregate["reasons"]), ("green", []))
        self.assertEqual(aggregate["expected"], 4)

    def test_a_survivor_in_one_shard_fails_the_aggregate(self):
        expected = ["a", "b"]
        left = self.manifest(0, 2, expected, ["a"])
        right = self.manifest(1, 2, expected, [])
        right["results"] = [outcomes.MutationResult("b", "d", outcomes.SURVIVED, "", "b" * 64, 1, "").as_record()]
        aggregate = outcomes.aggregate_manifests([left, right])
        self.assertEqual((aggregate["status"], aggregate["survived_ids"]), ("failed", ["b"]))


class SelectionTests(unittest.TestCase):
    """The environment decides which shard this process owns, and nothing else."""

    def test_without_shard_variables_the_whole_catalogue_is_owned(self):
        defects = [{"id": f"d{n}"} for n in range(9)]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_MUTATION_SHARDS", None)
            os.environ.pop("FACTORY_MUTATION_SHARD", None)
            selected, index, count = runner.shard_selection(defects)
        self.assertEqual((len(selected), index, count), (9, 0, 1))

    def test_an_out_of_range_shard_refuses_rather_than_measuring_nothing(self):
        with mock.patch.dict(os.environ, {"FACTORY_MUTATION_SHARDS": "4",
                                          "FACTORY_MUTATION_SHARD": "4"}):
            with self.assertRaises(RuntimeError):
                runner.shard_selection([{"id": "d"}])


if __name__ == "__main__":
    unittest.main()
