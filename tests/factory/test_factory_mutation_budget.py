"""The mutation rung's budget comes from a measurement, and its drift is visible.

`harness/ci.py` ran the mutation rung with `timeout=900`. Nothing recorded where 900 came
from, and nothing noticed that the application catalogue had grown from four defects to nine,
the factory trust-root catalogue past three hundred, and the suites each defect re-runs to
2184 tests. The first validation run that reached the rung (PR #134, worker run 34066724127)
passed security, provenance, all five judges, static, unit, the holdout and -- for the first
time -- the browser journey, and then died on:

    TIMEOUT after 900s
    GATE_FAILED: mutations

These tests pin the three properties that make that failure mode non-recurring: the budget is
derived from recorded measurements rather than written as a literal, every defect is still
evaluated however long that takes, and the runners say how much of the budget they used while
they still pass (D-073).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import budget as mutation_budget  # noqa: E402

RECORD = HARNESS / "budgets.json"
TIMING = re.compile(r"^MUTATION_TIMING id=(\S+) seconds=(\d+\.\d) outcome=(caught|escaped|not_injected)$")
OK_LINE = re.compile(r"^MUTATIONS_OK defects=(\d+) seconds=(\d+\.\d) budget=(\d+)$", re.M)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNTHETIC_SCOPES = {
    "mutation-rung": {"what": "the rung", "applied_by": ["a test"]},
    "factory-family": {"what": "the family", "applied_by": ["a test"]},
}


def synthetic(**over) -> dict:
    record = {
        "headroom": 1.5,
        "warn_fraction": 0.75,
        "scopes": {name: dict(entry) for name, entry in SYNTHETIC_SCOPES.items()},
        "measurements": [
            {"id": "a", "date": "2026-09-07", "scope": "mutation-rung", "kind": "measured",
             "environment": "test", "source": "test", "total_seconds": 1000},
            {"id": "b", "date": "2026-09-07", "scope": "mutation-rung", "kind": "projected",
             "environment": "test", "source": "test", "total_seconds": 400},
            {"id": "c", "date": "2026-09-07", "scope": "factory-family", "kind": "measured",
             "environment": "test", "source": "test", "total_seconds": 200},
        ],
    }
    record.update(over)
    return record


class DerivationTests(unittest.TestCase):
    def test_the_budget_is_the_worst_observation_plus_headroom(self):
        record = synthetic()
        self.assertEqual(mutation_budget.measured_seconds(record, "mutation-rung"), 1000)
        # 1000 * 1.5 = 1500, already a whole number of minutes.
        self.assertEqual(mutation_budget.budget_seconds(record, "mutation-rung"), 1500)

    def test_p100_and_not_the_mean(self):
        """A rung has to finish on its worst run, not on its average one."""
        record = synthetic()
        mean = sum(e["total_seconds"] for e in record["measurements"][:2]) / 2
        self.assertGreater(mutation_budget.measured_seconds(record, "mutation-rung"), mean)

    def test_the_budget_rounds_up_to_a_whole_minute(self):
        record = synthetic(measurements=[
            {"id": "a", "date": "d", "scope": "mutation-rung", "kind": "measured",
             "environment": "e", "source": "s", "total_seconds": 1001},
            {"id": "c", "date": "d", "scope": "factory-family", "kind": "measured",
             "environment": "e", "source": "s", "total_seconds": 10},
        ])
        # 1001 * 1.5 = 1501.5 -> 1560, never down.
        self.assertEqual(mutation_budget.budget_seconds(record, "mutation-rung"), 1560)
        self.assertGreaterEqual(mutation_budget.budget_seconds(record, "mutation-rung"), 1501.5)

    def test_each_scope_is_derived_from_its_own_measurements(self):
        record = synthetic()
        self.assertEqual(mutation_budget.budget_seconds(record, "factory-family"), 300)

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(ValueError):
            mutation_budget.budget_seconds(synthetic(), "whatever")


class RecordedMeasurementTests(unittest.TestCase):
    """The relation between the shipped budget and the shipped measurement."""

    def setUp(self) -> None:
        self.record = mutation_budget.load()

    def test_the_record_is_valid_and_covers_every_scope(self):
        for scope in mutation_budget.scope_names(self.record):
            self.assertTrue(mutation_budget.measurements(self.record, scope),
                            f"no measurement records the {scope} scope")

    def test_the_budget_covers_the_measurement_with_headroom(self):
        headroom = float(self.record["headroom"])
        self.assertGreaterEqual(headroom, mutation_budget.MINIMUM_HEADROOM)
        for scope in mutation_budget.scope_names(self.record):
            measured = mutation_budget.measured_seconds(self.record, scope)
            self.assertGreaterEqual(
                mutation_budget.budget_seconds(self.record, scope), measured * headroom,
                f"the {scope} budget does not cover its worst measurement with headroom",
            )

    def test_every_measurement_says_where_it_came_from(self):
        for entry in self.record["measurements"]:
            for field in mutation_budget.REQUIRED_FIELDS:
                self.assertTrue(entry.get(field), f"{entry.get('id')} is missing {field}")
            self.assertIn(entry["kind"], mutation_budget.KINDS)
            self.assertGreater(len(entry["source"]), 40,
                               "a source that does not name the runs behind the number is not "
                               "a source")

    def test_a_measurement_without_provenance_is_refused(self):
        record = synthetic()
        del record["measurements"][0]["source"]
        with self.assertRaises(ValueError):
            mutation_budget.validate(record)

    def test_a_headroom_below_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            mutation_budget.validate(synthetic(headroom=1.1))

    def test_a_scope_with_no_measurement_is_refused(self):
        record = synthetic()
        record["measurements"] = [e for e in record["measurements"]
                                  if e["scope"] != "factory-family"]
        with self.assertRaises(ValueError):
            mutation_budget.validate(record)

    def test_a_measurement_for_an_undeclared_scope_is_refused(self):
        record = synthetic()
        record["measurements"][0]["scope"] = "no-such-rung"
        with self.assertRaises(ValueError):
            mutation_budget.validate(record)

    def test_the_record_on_disk_parses_as_the_module_reads_it(self):
        raw = json.loads(RECORD.read_text(encoding="utf-8"))
        self.assertEqual(raw["measurements"], self.record["measurements"])


class WarningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = mutation_budget.load()

    def test_the_warning_fires_above_the_threshold(self):
        threshold = mutation_budget.warn_seconds(self.record, "mutation-rung")
        warning = mutation_budget.drift_warning(
            threshold + 1, self.record, scope="mutation-rung", marker="MUTATIONS_BUDGET_WARNING"
        )
        self.assertIsNotNone(warning)
        self.assertTrue(warning.startswith("MUTATIONS_BUDGET_WARNING "))
        self.assertIn(f"budget={mutation_budget.budget_seconds(self.record, 'mutation-rung')}",
                      warning)

    def test_the_warning_is_silent_below_the_threshold(self):
        threshold = mutation_budget.warn_seconds(self.record, "mutation-rung")
        self.assertIsNone(mutation_budget.drift_warning(
            threshold - 1, self.record, scope="mutation-rung", marker="M"))
        self.assertIsNone(mutation_budget.drift_warning(
            threshold, self.record, scope="mutation-rung", marker="M"))

    def test_the_threshold_is_the_stated_fraction_of_the_budget(self):
        for scope in mutation_budget.scope_names(self.record):
            self.assertAlmostEqual(
                mutation_budget.warn_seconds(self.record, scope),
                mutation_budget.budget_seconds(self.record, scope)
                * float(self.record["warn_fraction"]),
            )


class LadderTests(unittest.TestCase):
    """harness/ci.py takes the rung's clock from the record, and reports slow rungs."""

    def setUp(self) -> None:
        self.ci = load_module("ci_under_test", HARNESS / "ci.py")
        self.source = (HARNESS / "ci.py").read_text(encoding="utf-8")

    def test_the_mutation_rung_uses_the_derived_budget(self):
        record = mutation_budget.load()
        self.assertEqual(self.ci.MUTATIONS_TIMEOUT,
                         mutation_budget.budget_seconds(record, "mutation-rung"))

    def test_the_rung_is_not_given_a_literal(self):
        """Prose may quote the old number; the call may not carry one."""
        code = "\n".join(line for line in self.source.splitlines()
                         if not line.strip().startswith("#"))
        self.assertNotIn("timeout=900", code)
        self.assertIn("timeout=MUTATIONS_TIMEOUT", code)

    def test_a_rung_close_to_its_deadline_says_so(self):
        """The threshold is moved rather than the clock raced: a test that sleeps to just
        inside a real deadline is a flaky gate on a loaded runner."""
        out = io.StringIO()
        with mock.patch.object(self.ci, "SLOW_RUNG_FRACTION", 0.0), \
                contextlib.redirect_stdout(out):
            rc, _ = self.ci.run("demo", [sys.executable, "-c", "pass"], timeout=60)
        self.assertEqual(rc, 0, "a slow rung is still a rung that ran")
        self.assertIn("RUNG_SLOW step=demo", out.getvalue())
        self.assertIn("timeout=60", out.getvalue())

    def test_the_slow_rung_threshold_is_the_records_fraction(self):
        self.assertEqual(self.ci.SLOW_RUNG_FRACTION,
                         float(mutation_budget.load()["warn_fraction"]))

    def test_a_fast_rung_is_quiet(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ci.run("demo", [sys.executable, "-c", "pass"], timeout=60)
        self.assertNotIn("RUNG_SLOW", out.getvalue())

    def test_a_timeout_keeps_what_the_rung_managed_to_say(self):
        """A timeout that discards the partial output is a failure nobody can diagnose."""
        program = "import sys, time; print('MUTATION_TIMING id=x seconds=1.0 outcome=caught'); sys.stdout.flush(); time.sleep(30)"
        rc, out = self.ci.run("demo", [sys.executable, "-u", "-c", program], timeout=2)
        self.assertEqual(rc, 124)
        self.assertIn("TIMEOUT after 2s", out)
        self.assertIn("MUTATION_TIMING id=x", out)


class ApplicationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_module("app_mutations_budget", HARNESS / "mutations" / "run.py")

    def test_the_timing_line_format(self):
        line = self.runner.timing_line("cap-raised-to-100", 12.345, "caught")
        self.assertEqual(line, "MUTATION_TIMING id=cap-raised-to-100 seconds=12.3 outcome=caught")
        match = TIMING.match(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "cap-raised-to-100")
        self.assertEqual(match.group(3), "caught")

    def test_every_outcome_has_a_timing_line(self):
        for outcome in ("caught", "escaped", "not_injected"):
            self.assertIsNotNone(TIMING.match(self.runner.timing_line("d", 1.0, outcome)))

    def test_the_closing_line_reports_the_clock_against_the_budget(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.runner.report_clock(9, time.monotonic() - 3, True, "")
        match = OK_LINE.search(out.getvalue())
        self.assertIsNotNone(match, out.getvalue())
        self.assertEqual(int(match.group(1)), 9)
        self.assertGreaterEqual(float(match.group(2)), 3.0)
        self.assertEqual(int(match.group(3)), self.runner.RUNG_BUDGET_SECONDS)

    def test_a_run_near_the_budget_warns_while_it_still_passes(self):
        record = mutation_budget.load()
        threshold = mutation_budget.warn_seconds(record, "mutation-rung")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.runner.report_clock(9, time.monotonic() - (threshold + 5), True, "")
        text = out.getvalue()
        self.assertIn("MUTATIONS_BUDGET_WARNING", text)
        self.assertIn("MUTATIONS_OK", text, "the warning must not turn a green run red")

    def test_the_nested_factory_call_uses_the_factory_budget(self):
        record = mutation_budget.load()
        self.assertEqual(self.runner.FACTORY_BUDGET_SECONDS,
                         mutation_budget.budget_seconds(record, "factory-family"))
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "FACTORY_MUTATIONS_OK", "")

        with mock.patch.object(self.runner.subprocess, "run", fake_run),                 contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.runner.run_factory_mutations())
        self.assertEqual(seen.get("timeout"), self.runner.FACTORY_BUDGET_SECONDS)


class FactoryRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_module("factory_mutations_budget",
                                  HARNESS / "factory_mutations" / "run.py")

    def result(self, mutant_id: str, state: str):
        """A `MutationResult` in the runner's own vocabulary, so tests cannot invent a state."""
        detector = "tests/factory/test_claims.py" if state == "caught" else ""
        return self.runner.MutationResult(mutant_id, "a" * 64, state, detector, "b" * 64, 1_000, "")

    def test_only_test_modules_are_run_as_tests(self):
        """`startswith('tests/')` also selected recorded fixtures, which are not programs."""
        for rel in self.runner.TEST_FILES:
            self.assertTrue(rel.endswith(".py"), rel)
            self.assertTrue(Path(rel).name.startswith("test_"), rel)
        fixtures = [rel for rel in self.runner.COPY_FILES if "/fixtures/" in rel]
        self.assertTrue(fixtures, "the copy list no longer carries fixtures; re-check this rule")
        for rel in fixtures:
            self.assertNotIn(rel, self.runner.TEST_FILES)

    def test_every_test_file_exists_in_the_tree(self):
        for rel in self.runner.TEST_FILES:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_the_suite_stops_at_the_first_red_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a_ok.py").write_text("pass\n", encoding="utf-8")
            (root / "b_red.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            (root / "c_marker.py").write_text(
                "open('ran-c.txt','w').close()\n", encoding="utf-8")
            with mock.patch.object(self.runner, "TEST_FILES",
                                   ("a_ok.py", "b_red.py", "c_marker.py")):
                result, durations = self.runner.run_tests(root)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sorted(durations), ["a_ok.py", "b_red.py"])
            self.assertFalse((root / "ran-c.txt").exists(),
                             "a file after the first red still ran")

    def test_a_green_suite_still_runs_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a_ok.py", "b_ok.py", "c_ok.py"):
                (root / name).write_text("pass\n", encoding="utf-8")
            with mock.patch.object(self.runner, "TEST_FILES",
                                   ("a_ok.py", "b_ok.py", "c_ok.py")):
                result, durations = self.runner.run_tests(root)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(sorted(durations), ["a_ok.py", "b_ok.py", "c_ok.py"])

    def test_the_order_is_every_file_cheapest_first(self):
        durations = {rel: float(index) for index, rel in
                     enumerate(reversed(self.runner.TEST_FILES))}
        order = self.runner.cheapest_first(durations)
        self.assertEqual(sorted(order), sorted(self.runner.TEST_FILES),
                         "ordering must be a permutation of the whole suite, never a subset")
        self.assertEqual(order, tuple(reversed(self.runner.TEST_FILES)))

    def test_an_unmeasured_file_sorts_last_rather_than_cheapest(self):
        durations = {rel: 1.0 for rel in self.runner.TEST_FILES[1:]}
        order = self.runner.cheapest_first(durations)
        self.assertEqual(order[-1], self.runner.TEST_FILES[0])

    def test_every_defect_is_evaluated_and_reported_in_manifest_order(self):
        """Concurrency may not drop a result, reorder the report, or lose a survivor."""
        defects = [{"id": f"d{n}", "why": "why"} for n in range(12)]
        states = {"d3": "survived", "d7": "infra_error"}
        seen: list[str] = []

        def fake_evaluate(defect, order, baseline=None, causal=None, target=None):
            seen.append(defect["id"])
            state = states.get(defect["id"], "caught")
            return self.result(defect["id"], state)

        for workers in (1, 4):
            seen.clear()
            with mock.patch.object(self.runner, "evaluate", fake_evaluate):
                results = self.runner.evaluate_all(defects, (), workers)
            self.assertEqual([r.mutant_id for r in results], [d["id"] for d in defects])
            self.assertEqual(sorted(seen), sorted(d["id"] for d in defects))
            self.assertEqual(
                [r.state for r in results],
                [states.get(d["id"], "caught") for d in defects],
                f"an outcome was lost with workers={workers}",
            )

    def test_the_whole_catalogue_is_evaluated_however_long_it_takes(self):
        """The clock is a budget, not a filter: no defect is skipped for being late.

        A catalogue the size of the real one (391 defects across the eight manifests on
        2026-09-07), so a runner that trims the tail to what it thinks it can afford is
        caught here rather than by a smaller sample that happens to fit.
        """
        defects = [{"id": f"d{n:04d}", "why": "why"} for n in range(420)]

        def fake_evaluate(defect, order, baseline=None, causal=None, target=None):
            return self.result(defect["id"], "caught")

        for workers in (1, 4):
            with mock.patch.object(self.runner, "evaluate", fake_evaluate):
                results = self.runner.evaluate_all(defects, (), workers)
            self.assertEqual([r.mutant_id for r in results], [d["id"] for d in defects],
                             f"a defect was skipped with workers={workers}")

    def test_a_worker_failure_is_not_a_silently_dropped_defect(self):
        """A raising worker used to unwind the pool and discard every other verdict.

        It now becomes that mutant's own `infra_error`: nothing was observed about it, so it
        is neither a kill nor an escape, and the 902 defects beside it keep their results.
        """
        def boom(defect, order, baseline=None, causal=None, target=None):
            if defect["id"] == "d":
                raise RuntimeError("copy failed")
            return self.result(defect["id"], "caught")

        defects = [{"id": "c", "why": "w"}, {"id": "d", "why": "w"}, {"id": "e", "why": "w"}]
        with mock.patch.object(self.runner, "evaluate", boom):
            results = self.runner.evaluate_all(defects, (), 4)
        self.assertEqual([r.mutant_id for r in results], ["c", "d", "e"])
        self.assertEqual([r.state for r in results], ["caught", "infra_error", "caught"])

    def test_a_defect_copy_is_removed_however_it_ends(self):
        trees: list[Path] = []

        def fake_detector(argv, *, cwd, env, seconds):
            trees.append(Path(cwd))
            return "passed", 0, "Ran 1 test\n\nOK\n", 1_000

        defect = {"id": "probe", "file": "harness/harness.config.json",
                  "find": "\"driver\"", "replace": "\"driver\"", "why": "probe"}
        with mock.patch.object(self.runner, "run_detector", fake_detector), \
                mock.patch.object(self.runner, "TEST_FILES", ("tests/factory/test_claims.py",)):
            result = self.runner.evaluate(defect, ("tests/factory/test_claims.py",))
        self.assertEqual(result.state, "survived")
        self.assertGreater(result.elapsed_ns, 0)
        self.assertEqual(len(trees), 1)
        self.assertFalse(trees[0].exists(), "the copied tree outlived its defect")

    def run_main(self, results):
        """`main()` over a stubbed catalogue: real reporting, no 167-file copies.

        The catalogue is derived from the results, because `main` builds its report from the
        defects it loaded: a result for a defect the catalogue does not contain would be
        dropped, and a test that did not notice would be asserting on a shorter report.
        """
        green = {"baseline_ref": "b" * 64, "source_ref": "s", "environment_ref": "e",
                 "detectors": {rel: {"status": "passed", "exit_code": 0, "seconds": 0.1,
                                     "tail": ""} for rel in self.runner.TEST_FILES}}
        catalogue = [{"id": result.mutant_id, "why": "w"} for result in results]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"FACTORY_MUTATION_RESULTS": tmp}), \
             mock.patch.object(self.runner, "load_defects", lambda: catalogue), \
             mock.patch.object(self.runner, "immunity_is_green", lambda: True), \
             mock.patch.object(self.runner, "build_copy", lambda parent: Path(parent)), \
             mock.patch.object(self.runner, "run_baseline", lambda root: green), \
             mock.patch.object(self.runner, "evaluate_all", lambda *a, **k: results):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = self.runner.main()
        return rc, out.getvalue()

    def test_an_escaped_defect_fails_the_run(self):
        rc, text = self.run_main([self.result("d", "survived")])
        self.assertEqual(rc, 1)
        self.assertIn("FACTORY_MUTATIONS_FAILED", text)
        self.assertIn("FACTORY_MUTATIONS_ESCAPED=d", text)
        self.assertIn("FACTORY_MUTATIONS_SECONDS=", text)

    def test_an_unobserved_defect_is_incomplete_rather_than_a_bypass(self):
        """A timeout is an absence of evidence: not green, and not a reported escape."""
        for state, marker in (("timeout", "FACTORY_MUTATIONS_TIMED_OUT=d"),
                              ("infra_error", "FACTORY_MUTATIONS_UNINJECTED=d")):
            rc, text = self.run_main([self.result("d", state)])
            self.assertEqual(rc, 1, state)
            self.assertIn("FACTORY_MUTATIONS_INCOMPLETE", text)
            self.assertNotIn("FACTORY_MUTATIONS_FAILED", text)
            self.assertNotIn("FACTORY_MUTATIONS_OK", text)
            self.assertIn(marker, text)

    def test_both_bad_outcomes_are_reported_when_both_happened(self):
        """A survivor is a claim about the trust root; an unobserved mutant is a claim about
        this run. Printing only one of them hides the other."""
        rc, text = self.run_main([self.result("d", "survived"), self.result("e", "timeout")])
        self.assertEqual(rc, 1)
        self.assertIn("FACTORY_MUTATIONS_FAILED", text)
        self.assertIn("FACTORY_MUTATIONS_INCOMPLETE", text)
        self.assertIn("FACTORY_MUTATIONS_ESCAPED=d", text)
        self.assertIn("FACTORY_MUTATIONS_TIMED_OUT=e", text)

    def test_a_shard_names_its_own_members_before_claiming_it_is_fine(self):
        with mock.patch.dict(os.environ, {"FACTORY_MUTATION_SHARDS": "2",
                                          "FACTORY_MUTATION_SHARD": "0"}):
            rc, text = self.run_main([self.result("d", "survived")])
        self.assertEqual(rc, 1)
        self.assertIn("FACTORY_MUTATIONS_ESCAPED=d", text)
        self.assertNotIn("FACTORY_MUTATIONS_SHARD_OK", text)

    def test_a_caught_run_reports_its_clock_against_the_budget(self):
        rc, text = self.run_main([self.result("d", "caught")])
        self.assertEqual(rc, 0)
        self.assertRegex(text, r"FACTORY_MUTATIONS_OK defects=1 seconds=\d+\.\d budget=\d+")
        self.assertIn(f"budget={self.runner.FAMILY_BUDGET_SECONDS}", text)

    def test_worker_count_is_bounded_and_overridable(self):
        with mock.patch.dict(os.environ, {"FACTORY_MUTATION_WORKERS": "3"}):
            self.assertEqual(self.runner.worker_count(), 3)
        with mock.patch.dict(os.environ, {"FACTORY_MUTATION_WORKERS": "999"}):
            self.assertEqual(self.runner.worker_count(), 32)
        with mock.patch.dict(os.environ, {"FACTORY_MUTATION_WORKERS": "nonsense"}):
            self.assertGreaterEqual(self.runner.worker_count(), 1)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_MUTATION_WORKERS", None)
            self.assertGreaterEqual(self.runner.worker_count(), 1)
            self.assertLessEqual(self.runner.worker_count(), 8)


class SpineTests(unittest.TestCase):
    """The same literal, one authority further out."""

    def test_the_spine_bounds_the_factory_family_by_the_derived_budget(self):
        spine = load_module("spine_under_test", ROOT / "scripts" / "factory_evidence_spine.py")
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 1, "", "")

        with mock.patch.object(spine.subprocess, "run", fake_run),                 contextlib.redirect_stderr(io.StringIO()),                 self.assertRaises(SystemExit):
            spine.observe_factory_authority({"observed": {}})
        self.assertEqual(
            seen.get("timeout"),
            mutation_budget.budget_seconds(mutation_budget.load(), scope="factory-family"),
        )

    def test_the_spine_carries_no_literal_for_that_call(self):
        source = (ROOT / "scripts" / "factory_evidence_spine.py").read_text(encoding="utf-8")
        self.assertNotIn("timeout=1200", source)


if __name__ == "__main__":
    unittest.main()
