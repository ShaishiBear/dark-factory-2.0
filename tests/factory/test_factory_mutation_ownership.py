"""Stage ownership of the two mutation families.

Fanning genesis validation out to one disposable runner per stage exposed a mismatch that the
sequential ladder had hidden: the recipe gives the application and factory families their own
stages, while the integrated application runner still invoked the factory suite inside itself.
The factory suite therefore ran twice, and the nested run timed out. The fix is ownership, not a
larger timeout -- each family is measured once, by the stage that owns it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[2]
RUNNER = ROOT / "harness" / "mutations" / "run.py"
RECIPE = ROOT / "harness" / "genesis-recipe.json"


class ApplicationOnlyModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = RUNNER.read_text(encoding="utf-8")
        self.recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
        self.stages = {s["name"]: s for s in self.recipe["stages"]}

    def test_runner_offers_an_application_only_mode(self):
        self.assertIn("--application-only", self.source)

    def test_the_flag_is_actually_read(self):
        """Behaviour, not shape: asserting the guard exists does not prove it is ever true."""
        spec = importlib.util.spec_from_file_location("app_mutations", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module.application_only_requested(["--application-only"]))
        self.assertTrue(module.application_only_requested(["-x", "--application-only"]))
        self.assertFalse(module.application_only_requested([]))
        self.assertFalse(module.application_only_requested(["--other"]))

    def test_application_only_does_not_invoke_the_factory_suite(self):
        """The nested call must be unreachable in that mode, not merely discouraged."""
        body = self.source[self.source.index("def main("):]
        gate = body.index("if application_only:")
        nested = body.index("factory_ok = run_factory_mutations()")
        self.assertLess(gate, nested, "the application-only return must precede the nested call")
        between = body[gate:nested]
        self.assertIn("return 0", between)
        self.assertIn("return 1", between)

    def test_default_behaviour_still_runs_both_families(self):
        """Ordinary canonical use is unchanged; only genesis splits the families."""
        self.assertIn("factory_ok = run_factory_mutations()", self.source)
        self.assertIn("an application or factory defect can currently escape", self.source)

    def test_genesis_application_stage_uses_application_only(self):
        argv = self.stages["application-mutations"]["argv"]
        self.assertIn("--application-only", argv)
        self.assertIn("harness/mutations/run.py", argv)

    def test_genesis_factory_stage_owns_the_factory_family(self):
        argv = self.stages["factory-mutations"]["argv"]
        self.assertIn("harness/factory_mutations/run.py", argv)
        self.assertNotIn("--application-only", argv)

    def test_factory_family_is_represented_exactly_once_in_the_stage_set(self):
        owners = [
            s["name"] for s in self.recipe["stages"]
            if any("factory_mutations" in part for part in s["argv"])
        ]
        self.assertEqual(owners, ["factory-mutations"])

    def test_each_mutation_family_is_measured_by_exactly_one_stage(self):
        measured: dict[str, str] = {}
        for stage in self.recipe["stages"]:
            for key in stage.get("measures") or {}:
                self.assertNotIn(key, measured, f"{key} measured by two stages")
                measured[key] = stage["name"]
        self.assertEqual(measured.get("application_mutations_total"), "application-mutations")
        self.assertEqual(measured.get("factory_mutations_total"), "factory-mutations")


if __name__ == "__main__":
    unittest.main()
