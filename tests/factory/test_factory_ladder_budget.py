"""A wrapper bounds what it contains, and no clock in the ladder is a literal.

THE SAME DEFECT, THREE TIMES IN TWO DAYS. `harness/ci.py` gave the mutation rung
`timeout=900` and the first validation run that reached it died on `TIMEOUT after 900s` with
every other gate green (PR #134, run 34066724127). The spine carried the same unmeasured
`timeout=1200` for the factory family, one authority further out. D-073 derived both from
measurement -- and left the wrapper that CONTAINS them a literal: `scripts/factory_evidence.py`
ran the whole ladder with `timeout=1800` while the rung inside it had just been given 8460 s,
so run 34081507222 of the same PR passed security, provenance and all five judges and then
died on `TimeoutExpired: harness/ci.py timed out after 1800 seconds`.

The audit that followed found the shape everywhere it had not yet fired:

  * `harness/static.py` bounded each of five checks at 600 s -- a 3000 s rung -- inside a
    `harness/ci.py` rung bounded at 300 s. The inner number was five times the outer one.
  * `harness/unit.py` did the same with three suites at 900 s each.
  * `harness/mutations/run.py` bounded one channel at 900 s with nothing bounding the forty
    channel runs of an application family whose own budget is 4020 s.
  * `factory_kernel/runtime.py` bounded the spine at 2400 s while the spine allowed the
    Evidence Bundle it runs first 3000 s -- a wrapper below its own child.
  * `harness/observe.py` and `harness/post_merge.py` both ran the ladder at 3600 s.
  * `.github/workflows/dark-factory-main-regression.yml` capped the job at 150 minutes while
    the mutation rung alone had 141.

These tests pin the two rules that end it: every duration comes from `harness/budgets.json`,
and every wrapper's budget is at least the sum of the budgets it contains plus a stated
margin for the contained work that carries no budget of its own (D-075).
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import budget  # noqa: E402

# Files whose EVERY timeout bounds a rung or the ladder: none of them may carry a number.
NO_LITERAL_ANYWHERE = (
    "harness/ci.py",
    "harness/static.py",
    "harness/unit.py",
    "harness/mutations/run.py",
    "harness/factory_mutations/run.py",
    "scripts/factory_evidence_spine.py",
)
# Files that also bound git and gh plumbing, where a literal is correct. Only the call that
# runs the named program is a ladder clock, so only that one is checked.
NO_LITERAL_FOR_PROGRAM = (
    ("scripts/factory_evidence.py", "harness/ci.py"),
    ("harness/observe.py", "harness/ci.py"),
    ("harness/post_merge.py", "harness/ci.py"),
    ("factory_kernel/runtime.py", "scripts/factory_evidence.py"),
    ("factory_kernel/worker_runtime.py", "harness/post_merge.py"),
)
WORKFLOW_LIMITS = (
    (".github/workflows/dark-factory-main-regression.yml", "main-regression-job",
     "python harness/ci.py"),
    (".github/workflows/dark-factory-ci.yml", "quick-gate-job", "python harness/ci.py --quick"),
)
REGRESSION_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-main-regression.yml"
TIMEOUT_MINUTES = re.compile(r"^\s*timeout-minutes:\s*(\d+)\s*$", re.M)


def parse(rel: str) -> tuple[ast.Module, str]:
    source = (ROOT / rel).read_text(encoding="utf-8")
    return ast.parse(source), source


def literal_timeouts(rel: str) -> list[tuple[int, object]]:
    """Every `timeout=<number>` in the file, by line. AST and not a substring search: a rule
    that greps for `timeout=900` is satisfied by writing `timeout=901`."""
    tree, _ = parse(rel)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "timeout":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(
                keyword.value.value, (int, float)
            ) and not isinstance(keyword.value.value, bool):
                found.append((keyword.value.lineno, keyword.value.value))
    return found


def calls_mentioning(rel: str, needle: str) -> list[ast.Call]:
    tree, source = parse(rel)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        segment = ast.get_source_segment(source, node) or ""
        if needle in segment and any(k.arg == "timeout" for k in node.keywords):
            hits.append(node)
    return hits


def containment_report(record: dict) -> list[tuple[str, int, int]]:
    """(scope, its budget, what it contains) for EVERY declared scope.

    Returned rather than asserted inline so a separate test can check that this walk covers
    the whole record. A containment rule that quietly stops examining scopes is a containment
    rule that passes for the same reason an empty test suite passes.
    """
    return [
        (scope, budget.budget_seconds(record, scope), budget.containment_floor(record, scope))
        for scope in budget.scope_names(record)
    ]


class ContainmentTests(unittest.TestCase):
    """Every wrapper's budget is at least the sum of the budgets it contains."""

    def setUp(self) -> None:
        self.record = budget.load()

    def test_every_wrapper_bounds_what_it_contains(self):
        for scope, allowed, contains in containment_report(self.record):
            self.assertGreaterEqual(
                allowed, contains,
                f"the {scope!r} budget is {allowed}s but it contains {contains}s: "
                + budget.describe_containment(self.record, scope),
            )

    def test_the_containment_walk_covers_every_declared_scope(self):
        """The guard against a vacuous rule: narrowing the walk fails here, not silently."""
        rows = containment_report(self.record)
        self.assertEqual([row[0] for row in rows], list(budget.scope_names(self.record)))
        wrappers = [row for row in rows if row[2] > 0]
        self.assertGreaterEqual(
            len(wrappers), 8,
            "the ladder has at least eight nested clocks; a report that finds fewer than "
            "eight wrappers is walking a subset of the record",
        )

    def test_the_whole_chain_from_the_job_down_to_one_test_file_is_connected(self):
        """Naming a scope is not containing it: the chain that timed out must be one graph."""
        chain = (
            ("main-regression-job", "ladder"),
            ("ladder", "mutation-rung"),
            ("mutation-rung", "factory-family"),
            ("mutation-rung", "application-family"),
            ("application-family", "mutation-channel"),
            ("factory-family", "factory-focused-file"),
            ("evidence-spine", "evidence-bundle"),
            ("evidence-bundle", "ladder"),
            ("post-merge", "ladder"),
        )
        for outer, inner in chain:
            names = [name for name, _count in budget.contained(self.record, outer)]
            self.assertIn(inner, names, f"{outer!r} no longer contains {inner!r}")
            self.assertGreaterEqual(
                budget.budget_seconds(self.record, outer),
                budget.budget_seconds(self.record, inner),
                f"{outer!r} is smaller than the {inner!r} it contains",
            )

    def test_a_raised_inner_budget_breaks_the_record_that_holds_it(self):
        """The property the whole change exists for: raising an inner budget past its wrapper
        must fail HERE, in the change that raises it, not on a future validation run."""
        record = budget.load()
        for entry in record["measurements"]:
            if entry["scope"] == "factory-family":
                entry["total_seconds"] = float(entry["total_seconds"]) * 10
        with self.assertRaises(ValueError) as raised:
            budget.validate(record)
        message = str(raised.exception)
        self.assertIn("A wrapper must bound what it contains", message)
        self.assertIn("factory-family", message,
                      "the refusal must name the inner scope that outgrew its wrapper")

    def test_the_gate_itself_refuses_an_inconsistent_record(self):
        """load() and not only a test, so a record whose wrappers stopped containing their
        parts stops every runner that reads it rather than being caught after the fact."""
        record = budget.load()
        record["scopes"]["ladder"]["unbudgeted_seconds"] = 10 ** 6
        record["scopes"]["ladder"]["margin_source"] = "x" * 60
        with self.assertRaises(ValueError):
            budget.validate(record)

    def test_a_containment_cycle_is_refused_rather_than_recursed_into(self):
        record = budget.load()
        record["scopes"]["mutation-channel"]["contains"] = [
            {"scope": "mutation-rung", "count": 1}
        ]
        with self.assertRaises(ValueError) as raised:
            budget.validate(record)
        self.assertIn("cycle", str(raised.exception))

    def test_an_unknown_inner_scope_is_refused(self):
        record = budget.load()
        record["scopes"]["ladder"]["contains"].append({"scope": "no-such-rung", "count": 1})
        with self.assertRaises(ValueError):
            budget.validate(record)

    def test_a_margin_without_provenance_is_refused(self):
        record = budget.load()
        record["scopes"]["ladder"]["margin_source"] = "because"
        with self.assertRaises(ValueError):
            budget.validate(record)

    def test_a_scope_that_names_no_caller_is_refused(self):
        record = budget.load()
        record["scopes"]["ladder"]["applied_by"] = []
        with self.assertRaises(ValueError):
            budget.validate(record)


class ProvenanceTests(unittest.TestCase):
    """A number nobody observed says so, in the record, in the field meant for it."""

    def setUp(self) -> None:
        self.record = budget.load()

    def test_a_composed_budget_is_labelled_projected(self):
        for scope in budget.scope_names(self.record):
            entry = self.record["scopes"][scope]
            composed = bool(entry.get("contains")) or bool(
                entry.get("bounded_seconds", 0) or entry.get("unbudgeted_seconds", 0)
            )
            if not composed:
                continue
            for measurement in budget.measurements(self.record, scope):
                self.assertEqual(
                    measurement["kind"], "projected",
                    f"{scope!r} is composed from other scopes, so its total is a projection "
                    f"until somebody times the wrapper end to end; {measurement['id']} claims "
                    f"kind={measurement['kind']!r}",
                )

    def test_a_projected_measurement_shows_its_arithmetic(self):
        for measurement in self.record["measurements"]:
            if measurement["kind"] != "projected":
                continue
            source = measurement["source"]
            self.assertGreater(len(source), 80, measurement["id"])
            self.assertRegex(source, r"\d", measurement["id"])

    def test_a_measured_measurement_names_the_run_or_the_host_it_was_read_off(self):
        measured = [m for m in self.record["measurements"] if m["kind"] == "measured"]
        self.assertTrue(measured, "no scope is backed by an observation at all")
        for measurement in measured:
            provenance = measurement["source"] + " " + measurement["environment"]
            self.assertRegex(
                provenance, r"(run \d{8,}|host)",
                f"{measurement['id']} claims to be measured but names neither the run nor the "
                f"host the wall clock was read off",
            )

    def test_every_scope_names_what_it_bounds_and_who_applies_it(self):
        for scope in budget.scope_names(self.record):
            entry = self.record["scopes"][scope]
            self.assertGreater(len(entry["what"]), 20, scope)
            self.assertTrue(entry["applied_by"], scope)
            for caller in entry["applied_by"]:
                path = caller.split()[0].split(":")[0]
                if "/" in path and path.endswith((".py", ".yml", ".json")):
                    self.assertTrue((ROOT / path).is_file(),
                                    f"{scope!r} names a caller that does not exist: {path}")


class NoLiteralTests(unittest.TestCase):
    """No literal seconds where a literal is what broke."""

    def test_the_ladder_files_carry_no_literal_timeout(self):
        for rel in NO_LITERAL_ANYWHERE:
            found = literal_timeouts(rel)
            self.assertEqual(
                found, [],
                f"{rel} carries a literal timeout at line(s) "
                f"{[line for line, _value in found]}: {found}. Every clock in the ladder "
                f"comes from harness/budgets.json.",
            )

    def test_the_program_that_runs_the_ladder_is_never_given_a_literal(self):
        for rel, program in NO_LITERAL_FOR_PROGRAM:
            hits = calls_mentioning(rel, program)
            self.assertTrue(hits, f"{rel} no longer runs {program}; re-check this rule")
            for call in hits:
                for keyword in call.keywords:
                    if keyword.arg != "timeout":
                        continue
                    self.assertNotIsInstance(
                        keyword.value, ast.Constant,
                        f"{rel}:{keyword.value.lineno} bounds {program} with a literal. That "
                        f"is the defect that ended run 34081507222.",
                    )

    def test_the_rule_is_not_satisfied_by_renaming_the_number(self):
        """The detector reads the syntax tree, so `timeout=901` is caught exactly as
        `timeout=900` was; a substring rule would not have been."""
        tree = ast.parse("subprocess.run(argv, timeout=901)\n")
        found = [
            k.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for k in node.keywords
            if k.arg == "timeout" and isinstance(k.value, ast.Constant)
        ]
        self.assertEqual(found, [901])


class LadderCallerTests(unittest.TestCase):
    """Each caller takes its clock from the scope that names it."""

    def setUp(self) -> None:
        self.record = budget.load()

    def test_every_rung_of_ci_takes_its_own_scope(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("ci_budget_under_test", HARNESS / "ci.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attribute, scope in (
            ("STATIC_TIMEOUT", "static-rung"),
            ("UNIT_TIMEOUT", "unit-rung"),
            ("E2E_TIMEOUT", "e2e-rung"),
            ("HOLDOUT_TIMEOUT", "holdout-rung"),
            ("MUTATIONS_TIMEOUT", "mutation-rung"),
        ):
            self.assertEqual(getattr(module, attribute),
                             budget.budget_seconds(self.record, scope), attribute)

    def test_the_rung_runners_share_one_deadline_instead_of_one_per_step(self):
        """Five checks at 600 s each is a 3000 s rung wearing a 600 s label."""
        for rel, scope in (("harness/static.py", "static-rung"), ("harness/unit.py", "unit-rung")):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(f'budget.deadline(BUDGET, "{scope}")', source, rel)
            self.assertIn("budget.remaining(", source, rel)

    def test_the_kernel_bounds_the_quick_gate_by_the_scope_that_contains_its_rungs(self):
        """`ci.py --quick` is the static rung and the unit rung; 900 s was above their sum by
        luck rather than by construction."""
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        self.assertEqual(
            source.count('scope="quick-gate"'), 2,
            "both quick-gate calls in the kernel must take the derived budget",
        )
        self.assertGreaterEqual(
            budget.budget_seconds(self.record, "quick-gate"),
            budget.containment_floor(self.record, "quick-gate"),
        )

    def test_the_e2e_watchdog_is_no_longer_a_config_literal(self):
        config = (HARNESS / "harness.config.json").read_text(encoding="utf-8")
        self.assertNotIn('"e2e_timeout_s"', config)


class WorkflowTests(unittest.TestCase):
    """The outermost wrapper anybody can actually set is checked like any other."""

    def setUp(self) -> None:
        self.record = budget.load()

    def test_every_job_limit_bounds_the_gate_it_runs(self):
        for rel, scope, _command in WORKFLOW_LIMITS:
            text = (ROOT / rel).read_text(encoding="utf-8")
            minutes = TIMEOUT_MINUTES.search(text)
            self.assertIsNotNone(minutes, f"{rel} declares no timeout-minutes")
            required = budget.budget_seconds(self.record, scope)
            self.assertGreaterEqual(
                int(minutes.group(1)) * 60, required,
                f"{rel} allows {minutes.group(1)} minutes for a job whose budget is "
                f"{required}s ({required / 60:.0f} minutes). A job limit is the outermost "
                f"wrapper anybody can set; it has to bound what it runs.",
            )

    def test_every_job_actually_runs_the_gate_it_is_budgeted_for(self):
        for rel, _scope, command in WORKFLOW_LIMITS:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(command, text, rel)

    def test_the_worker_job_records_why_no_limit_bounds_it(self):
        """The one wrapper the record cannot close is written down, not quietly left short."""
        note = self.record.get("_the_one_wrapper_this_record_cannot_close", "")
        self.assertIn("dark-factory-worker.yml", note)
        self.assertIn("360", note, "the note must say what the platform's own cap is")


if __name__ == "__main__":
    unittest.main()
