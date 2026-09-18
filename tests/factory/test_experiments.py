"""The experiment registry (SPECIFICATION 8.2, WP08): registered families, the deterministic
repository-boundary analysis over a frozen context, and the refusals that keep a registration
from being mistaken for a capability."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.canonical import sha256_value  # noqa: E402
from factory_kernel.experiments import (BOUNDARY_METRICS, BOUNDARY_VERSION, FAMILIES, metrics_for, registry_record,  # noqa: E402
                                        run_boundary_analysis, run_experiment, strategy_of, validate_experiment)
from factory_kernel.frontdoor_intent import IntentRefused  # noqa: E402


def context(files: dict) -> dict:
    rows = {}
    for path, imports in files.items():
        rows[path] = {"sha256": "0" * 64, "bytes": 10, "lines": 1, "imports": sorted(imports), "definitions": [],
                      "gaps": ["dynamic-imports-and-runtime-dispatch-not-resolved"] if path.endswith(".py")
                      else ["javascript-imports-are-lexical-not-a-complete-module-graph"]}
    value = {"commit": "a" * 40, "files": rows, "policies": {}, "coverage": "selected-committed-source-only",
             "proof_status": "not-established"}
    return {**value, "identity": sha256_value(value)}


LAYERS = [{"name": "routes", "prefixes": ["app/backend/routes/"]}, {"name": "services", "prefixes": ["app/backend/services/"]},
          {"name": "db", "prefixes": ["app/backend/db/"]}]

REPO = context({
    "app/backend/routes/messages.py": ["backend.services.chat", "backend.db.repository", "fastapi"],
    "app/backend/services/chat.py": ["backend.db.repository", "openai"],
    "app/backend/db/repository.py": ["asyncpg"],
    "app/backend/services/loop_a.py": ["backend.services.loop_b"],
    "app/backend/services/loop_b.py": ["backend.services.loop_a", "backend.routes.messages"],
    "app/backend/tests/test_messages.py": ["backend.routes.messages"],
    "app/frontend/src/lib/api.ts": ["./authApi"],
    "app/frontend/src/lib/authApi.ts": [],
    "app/frontend/src/__tests__/api.test.ts": ["../lib/api"],
})
# `backend.x` imports resolve because the context's module names start at `app/`: the analysis
# maps `backend.services.chat` to app/backend/services/chat.py through the dotted-name suffix walk.


def boundary(strategies: dict) -> dict:
    return {"kind": BOUNDARY_VERSION, "layers": LAYERS, "strategies": strategies}


class RegistryTests(unittest.TestCase):
    def test_the_registry_names_four_families_and_only_two_run(self) -> None:
        self.assertEqual(set(FAMILIES), {"lookup-workload-v1", BOUNDARY_VERSION, "public-contract-probe-v1", "migration-rehearsal-v1"})
        self.assertEqual({k for k, v in FAMILIES.items() if v["runnable"]}, {"lookup-workload-v1", BOUNDARY_VERSION})
        for kind, row in FAMILIES.items():
            with self.subTest(kind):
                self.assertEqual(set(row) >= {"runner", "executes_candidate_code", "runnable", "metrics", "max_units", "claim_scope", "limitations"}, True)
                if not row["runnable"]:
                    self.assertIsNone(row["runner"])
                    self.assertIn("not_runnable_because", row)
        self.assertEqual(metrics_for(BOUNDARY_VERSION), BOUNDARY_METRICS)
        self.assertEqual(registry_record()["schema"], "dark-factory/experiment-registry")

    def test_a_registered_but_unrunnable_family_refuses_before_any_reservation(self) -> None:
        for kind in ("public-contract-probe-v1", "migration-rehearsal-v1"):
            with self.subTest(kind), self.assertRaises(IntentRefused) as ctx:
                validate_experiment({"kind": kind, "strategies": {"x": {}}})
            self.assertIn("not runnable", str(ctx.exception))
        with self.assertRaises(IntentRefused):
            validate_experiment({"kind": "shell-v1", "argv": ["rm", "-rf"]})
        with self.assertRaises(IntentRefused):
            validate_experiment("lookup-workload-v1")

    def test_the_lookup_family_still_runs_through_its_own_runner(self) -> None:
        spec = {"kind": "lookup-workload-v1", "strategies": ["linear", "hash"], "keys": [1, 2, 3], "queries": [3, 4]}
        self.assertGreater(validate_experiment(spec), 0)
        receipt = run_experiment(spec, context=REPO)
        self.assertEqual((receipt["runner"], receipt["results"]["linear"]["matches"], receipt["results"]["hash"]["matches"]), ("lookup-workload-v1", 1, 1))
        self.assertEqual(strategy_of(spec, {"probe_strategy": "hash"}), "hash")
        self.assertIsNone(strategy_of(spec, {"probe_strategy": "binary"}))
        self.assertIsNone(strategy_of(spec, {"probe_strategy": None}))


class BoundaryTests(unittest.TestCase):
    def test_the_analysis_is_deterministic_and_reads_only_the_context(self) -> None:
        spec = boundary({"touch-route": {"touched_paths": ["app/backend/routes/messages.py"]},
                         "touch-loop": {"touched_paths": ["app/backend/services/loop_a.py", "app/backend/services/loop_b.py"]},
                         "touch-missing": {"touched_paths": ["app/backend/routes/messages.py", "app/backend/routes/not_selected.py"]}})
        self.assertEqual(validate_experiment(spec), 5)
        one = run_boundary_analysis(spec, context=REPO)
        two = run_experiment(spec, context=REPO)
        self.assertEqual(one, two)
        route = one["results"]["touch-route"]
        self.assertEqual({k: route[k] for k in BOUNDARY_METRICS}, {"touched_files": 1, "unanalysed_files": 0, "import_edges": 2,
                                                                     "layer_violations": 0, "unknown_layer_files": 0, "import_cycles": 0,
                                                                     "affected_tests": 1})
        self.assertEqual(route["detail"]["affected_test_files"], ["app/backend/tests/test_messages.py"])
        loop = one["results"]["touch-loop"]
        self.assertEqual((loop["import_edges"], loop["import_cycles"], loop["layer_violations"]), (3, 1, 1))  # services -> routes is upward
        missing = one["results"]["touch-missing"]
        self.assertEqual((missing["touched_files"], missing["unanalysed_files"], missing["detail"]["unanalysed_paths"]),
                         (1, 1, ["app/backend/routes/not_selected.py"]))
        self.assertEqual((one["qualification_status"], one["scope"], one["context_identity"]), ("UNPROVEN", "selected-committed-source-only", REPO["identity"]))
        self.assertIn("no-candidate-code-is-executed", one["limitations"])
        self.assertIn("dynamic-imports-and-runtime-dispatch-not-resolved", route["detail"]["gaps"])

    def test_javascript_relative_imports_resolve_lexically(self) -> None:
        spec = {"kind": BOUNDARY_VERSION, "layers": [{"name": "lib", "prefixes": ["app/frontend/src/lib/"]}],
                "strategies": {"api": {"touched_paths": ["app/frontend/src/lib/api.ts"]}}}
        row = run_boundary_analysis(spec, context=REPO)["results"]["api"]
        self.assertEqual((row["import_edges"], row["affected_tests"], row["layer_violations"]), (1, 1, 0))

    def test_an_ambiguous_dotted_import_names_nothing(self) -> None:
        # Two context modules end in `db.repository`; `backend.db.repository` still resolves (one
        # match), `db.repository` alone would match both and so names nothing.
        repo = context({"app/backend/db/repository.py": [], "tools/db/repository.py": [],
                        "app/backend/services/chat.py": ["db.repository"],
                        "app/backend/routes/messages.py": ["backend.db.repository"]})
        spec = boundary({"svc": {"touched_paths": ["app/backend/services/chat.py"]}, "route": {"touched_paths": ["app/backend/routes/messages.py"]}})
        rows = run_boundary_analysis(spec, context=repo)["results"]
        self.assertEqual((rows["svc"]["import_edges"], rows["route"]["import_edges"]), (0, 1))

    def test_a_file_in_no_declared_layer_is_counted_not_guessed(self) -> None:
        spec = {"kind": BOUNDARY_VERSION, "layers": [{"name": "routes", "prefixes": ["app/backend/routes/"]}],
                "strategies": {"svc": {"touched_paths": ["app/backend/services/chat.py"]}}}
        row = run_boundary_analysis(spec, context=REPO)["results"]["svc"]
        self.assertEqual((row["unknown_layer_files"], row["layer_violations"], row["import_edges"]), (1, 1, 1))

    def test_a_layer_prefix_claims_whole_segments_only(self) -> None:
        repo = context({"app/backend/routes/messages.py": [], "app/backend/routes_v2/messages.py": [], "app/backend/routes": []})
        spec = {"kind": BOUNDARY_VERSION, "layers": [{"name": "routes", "prefixes": ["app/backend/routes"]}],
                "strategies": {"in": {"touched_paths": ["app/backend/routes/messages.py", "app/backend/routes"]},
                               "out": {"touched_paths": ["app/backend/routes_v2/messages.py"]}}}
        rows = run_boundary_analysis(spec, context=repo)["results"]
        self.assertEqual((rows["in"]["unknown_layer_files"], rows["out"]["unknown_layer_files"]), (0, 1))
        self.assertIn("imports-are-those-of-the-frozen-base-not-the-proposed-change", FAMILIES[BOUNDARY_VERSION]["limitations"])

    def test_spec_refusals(self) -> None:
        cases = {
            "no layers": {"kind": BOUNDARY_VERSION, "layers": [], "strategies": {"a": {"touched_paths": ["x.py"]}}},
            "duplicate layer": {"kind": BOUNDARY_VERSION, "layers": [LAYERS[0], LAYERS[0]], "strategies": {"a": {"touched_paths": ["x.py"]}}},
            "no strategies": boundary({}),
            "too many strategies": boundary({f"s{i}": {"touched_paths": ["x.py"]} for i in range(9)}),
            "empty paths": boundary({"a": {"touched_paths": []}}),
            "absolute path": boundary({"a": {"touched_paths": ["/etc/passwd"]}}),
            "parent path": boundary({"a": {"touched_paths": ["app/../.factory/kernel.json"]}}),
            "duplicate path": boundary({"a": {"touched_paths": ["x.py", "x.py"]}}),
            "extra field": {**boundary({"a": {"touched_paths": ["x.py"]}}), "argv": ["sh"]},
            "strategy extra field": boundary({"a": {"touched_paths": ["x.py"], "command": "sh"}}),
        }
        for name, spec in cases.items():
            with self.subTest(name), self.assertRaises(IntentRefused):
                validate_experiment(spec)
        with self.assertRaises(IntentRefused):
            run_boundary_analysis(boundary({"a": {"touched_paths": ["x.py"]}}), context={"files": {}})

    def test_the_stop_check_is_consulted_and_the_candidate_binding_is_by_id(self) -> None:
        spec = boundary({"scan": {"touched_paths": ["app/backend/db/repository.py"]}})
        stop = Mock()
        run_boundary_analysis(spec, context=REPO, check_stop=stop)
        self.assertGreater(stop.call_count, 0)
        self.assertEqual(strategy_of(spec, {"id": "scan", "probe_strategy": "linear"}), "scan")
        self.assertIsNone(strategy_of(spec, {"id": "index", "probe_strategy": "linear"}))


if __name__ == "__main__":
    unittest.main()
