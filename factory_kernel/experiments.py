"""The experiment registry (SPECIFICATION 8.2, WP08): reviewed data schemas for the experiment
families a Preflight session may run, with their runner ids, metrics, cost bounds and admissible
claim scopes. A model may propose a spec of a registered kind; it can never register a runner,
supply argv, an image or an import path. Everything executable here is reviewed code.

Families:
- `lookup-workload-v1`: the existing data-only lookup probe (`exploration_probe`), kept as a
  small control with its recorded limitations.
- `repository-boundary-v1`: deterministic import, layer, cycle and affected-test analysis over
  the session's frozen repository context. No candidate code is executed; the analysis reads
  only the observations `exploration_repository` already recorded (lexical imports, with the
  coverage gaps those carry), and every touched path the context does not hold is counted as
  unanalysed rather than assumed.
- `public-contract-probe-v1`, `migration-rehearsal-v1`: registered schemas only. They need an
  isolated probe view (WP06 worker view) and a disposable database; until those exist the
  registry refuses to run them and says why. A registration is not a capability.

A receipt describes one spec over one context; it is UNPROVEN for qualification, its scope is
the family's admissible claim scope, and its limitations travel with it.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Mapping

from .canonical import sha256_value
from .exploration_probe import METRICS as LOOKUP_METRICS, VERSION as LOOKUP_VERSION, run_probe, validate_probe
from .frontdoor_intent import IntentRefused, _shape, _text, _texts

BOUNDARY_VERSION = "repository-boundary-v1"
BOUNDARY_METRICS = frozenset({"touched_files", "unanalysed_files", "import_edges", "layer_violations", "unknown_layer_files",
                              "import_cycles", "affected_tests"})
MAX_STRATEGIES = 8
MAX_TOUCHED_PATHS = 200
MAX_LAYERS = 12

FAMILIES: Mapping[str, Mapping[str, Any]] = {
    LOOKUP_VERSION: {
        "runner": "exploration_probe.run_probe", "executes_candidate_code": False, "runnable": True,
        "metrics": sorted(LOOKUP_METRICS), "max_units": 10_000_000, "max_wall_seconds": 10,
        "claim_scope": "finite-declared-workload", "environment": "in-process data-only",
        "strategy_binding": "candidate.probe_strategy",
        "limitations": ["not-production-latency", "hash-counts-are-logical-lookups", "build-items-does-not-measure-sort-cost",
                        "synthetic-data-may-not-represent-production"],
    },
    BOUNDARY_VERSION: {
        "runner": "experiments.run_boundary_analysis", "executes_candidate_code": False, "runnable": True,
        "metrics": sorted(BOUNDARY_METRICS), "max_units": 1_000_000, "max_wall_seconds": 10,
        "claim_scope": "selected-committed-source-only", "environment": "in-process analysis of the frozen repository context",
        "strategy_binding": "spec.strategies[candidate.id]",
        "limitations": ["lexical-imports-only-not-a-semantic-call-graph", "dynamic-imports-and-runtime-dispatch-not-resolved",
                        "touched-paths-are-declared-by-the-candidate-not-measured", "files-outside-the-context-are-unanalysed",
                        "no-candidate-code-is-executed"],
    },
    "public-contract-probe-v1": {
        "runner": None, "executes_candidate_code": True, "runnable": False,
        "not_runnable_because": "needs an isolated probe view for candidate code (WP06 worker view) and a frozen public contract fixture set",
        "metrics": ["contract_cases_passed", "contract_cases_failed", "contract_cases_errored"], "max_units": 0, "max_wall_seconds": 0,
        "claim_scope": "explicit-public-contract-and-workload-only", "environment": "isolated workspace (not built)",
        "strategy_binding": "spec.strategies[candidate.id]", "limitations": ["no-blind-holdout-is-exposed", "results-describe-only-the-named-contract"],
    },
    "migration-rehearsal-v1": {
        "runner": None, "executes_candidate_code": True, "runnable": False,
        "not_runnable_because": "needs reviewed migration adapters and a disposable synthetic database; no production DB or secret access",
        "metrics": ["invariants_held", "invariants_broken", "rollback_succeeded"], "max_units": 0, "max_wall_seconds": 0,
        "claim_scope": "synthetic-disposable-data-only", "environment": "disposable database (not built)",
        "strategy_binding": "spec.strategies[candidate.id]", "limitations": ["synthetic-data-only", "declared-invariants-only"],
    },
}


def family(kind: Any) -> Mapping[str, Any]:
    if not isinstance(kind, str) or kind not in FAMILIES:
        raise IntentRefused("unknown experiment family")
    return FAMILIES[kind]


def metrics_for(kind: Any) -> frozenset:
    return frozenset(family(kind)["metrics"])


# ---- repository-boundary-v1 --------------------------------------------------------------------

_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,299}")


def _paths(value: Any, *, bound: int) -> list[str]:
    values = _texts(value)
    if not values or len(values) > bound or len(set(values)) != len(values):
        raise IntentRefused("touched paths must be a bounded, distinct, nonempty list")
    for path in values:
        if not _PATH.fullmatch(path) or path.startswith("/") or ".." in path.split("/"):
            raise IntentRefused("touched path is not a repository-relative path")
    return values


def validate_boundary(spec: Mapping[str, Any]) -> int:
    """The declared layers (ordered, top to bottom) and one touched-path set per strategy. Returns
    the reserved units: strategies times touched paths, a bound on the deterministic work."""
    _shape(spec, {"kind", "layers", "strategies"})
    if spec["kind"] != BOUNDARY_VERSION:
        raise IntentRefused("unknown experiment family")
    layers = spec["layers"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        raise IntentRefused("layers must be an ordered nonempty list")
    names = []
    for layer in layers:
        _shape(layer, {"name", "prefixes"})
        _text(layer["name"], 100)
        names.append(layer["name"])
        prefixes = _texts(layer["prefixes"])
        if not prefixes or any(not _PATH.fullmatch(p) or ".." in p.split("/") for p in prefixes):
            raise IntentRefused("layer prefixes must be repository-relative")
    if len(set(names)) != len(names):
        raise IntentRefused("layer names must be distinct")
    strategies = spec["strategies"]
    if not isinstance(strategies, Mapping) or not 1 <= len(strategies) <= MAX_STRATEGIES:
        raise IntentRefused("strategies must map one to eight candidate ids to their touched paths")
    units = 0
    for name, strategy in strategies.items():
        _text(name, 100)
        _shape(strategy, {"touched_paths"})
        units += len(_paths(strategy["touched_paths"], bound=MAX_TOUCHED_PATHS))
    return units


def _module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    stem = path[:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _resolve_import(spec: str, importer: str, modules: Mapping[str, str]) -> str | None:
    """Map a recorded import string to a context file, or None when it names nothing in the
    context (a stdlib, third-party or unselected module: a coverage gap, never an edge)."""
    if spec.startswith("."):
        level = len(spec) - len(spec.lstrip("."))
        base = _module_name(importer) or ""
        parts = base.split(".")
        # a package's __init__ resolves relative to itself; a module relative to its package
        anchor = parts[:-1] if not importer.endswith("/__init__.py") else parts
        anchor = anchor[: len(anchor) - (level - 1)] if level > 1 else anchor
        rest = spec.lstrip(".")
        target = ".".join(anchor + ([rest] if rest else []))
    else:
        target = spec
    # The context's module names start at the repository root (`app.backend.db.repository`), while
    # the code imports them from the package it runs in (`backend.db.repository`), so a dotted name
    # matches a module whose name equals it or ends with it; an ambiguous suffix names nothing.
    while target:
        if target in modules:
            return modules[target]
        matches = [path for name, path in modules.items() if name.endswith("." + target)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
        target = target.rpartition(".")[0]
    return None


def _js_resolve(spec: str, importer: str, files: Mapping[str, Any]) -> str | None:
    if not spec.startswith("."):
        return None
    base = importer.rsplit("/", 1)[0] if "/" in importer else ""
    parts = (base.split("/") if base else [])
    for piece in spec.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece not in (".", ""):
            parts.append(piece)
    joined = "/".join(parts)
    for candidate in (joined, joined + ".ts", joined + ".tsx", joined + ".js", joined + ".jsx", joined + "/index.ts", joined + "/index.tsx"):
        if candidate in files:
            return candidate
    return None


def _layer_of(path: str, layers: list[Mapping[str, Any]]) -> int | None:
    for index, layer in enumerate(layers):
        if any(path.startswith(prefix) for prefix in layer["prefixes"]):
            return index
    return None


def _cycles(edges: Mapping[str, set]) -> int:
    """Strongly connected components with more than one node, or a self edge (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    count = [0]
    cycles = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = count[0]
        count[0] += 1
        stack.append(node)
        on_stack.add(node)
        for other in sorted(edges.get(node, ())):
            if other not in index:
                visit(other)
                low[node] = min(low[node], low[other])
            elif other in on_stack:
                low[node] = min(low[node], index[other])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in edges.get(node, ()):
                cycles[0] += 1

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return cycles[0]


def run_boundary_analysis(spec: Mapping[str, Any], *, context: Mapping[str, Any], check_stop=lambda: None) -> dict:
    """Deterministic. For each strategy: the touched files the context holds, the import edges
    among context files that start at a touched file, the edges that cross the declared layer
    order upward (an inner layer importing an outer one) or land in no declared layer, the
    import cycles among touched files, and the test files in the context that import a touched
    file. Files the context does not hold are counted, never guessed."""
    units = validate_boundary(spec)
    files = context.get("files") if isinstance(context, Mapping) else None
    if not isinstance(files, Mapping) or not isinstance(context.get("identity"), str):
        raise IntentRefused("boundary analysis needs the session's repository context")
    modules = {name: path for path in files if (name := _module_name(path)) is not None}
    graph: dict[str, set] = defaultdict(set)
    for path, row in files.items():
        for spec_text in row.get("imports", ()):
            check_stop()
            target = _resolve_import(spec_text, path, modules) if path.endswith(".py") else _js_resolve(spec_text, path, files)
            if target is not None and target != path:
                graph[path].add(target)
    layers = list(spec["layers"])
    results = {}
    for name, strategy in spec["strategies"].items():
        check_stop()
        touched = [p for p in strategy["touched_paths"] if p in files]
        unanalysed = [p for p in strategy["touched_paths"] if p not in files]
        edges = [(src, dst) for src in touched for dst in sorted(graph.get(src, ()))]
        unknown_layer = [p for p in touched if _layer_of(p, layers) is None]
        violations = 0
        for src, dst in edges:
            src_layer, dst_layer = _layer_of(src, layers), _layer_of(dst, layers)
            if src_layer is None or dst_layer is None or dst_layer < src_layer:
                violations += 1
        touched_set = set(touched)
        cycles = _cycles({src: {d for d in graph.get(src, ()) if d in touched_set} for src in touched})
        affected = sorted({path for path, targets in graph.items() if (path.startswith("tests/") or "/tests/" in path or "/__tests__/" in path
                                                                        or path.endswith((".test.ts", ".test.tsx", "_test.py")) or path.rsplit("/", 1)[-1].startswith("test_"))
                           and targets & touched_set})
        results[name] = {"touched_files": len(touched), "unanalysed_files": len(unanalysed), "import_edges": len(edges),
                         "layer_violations": violations, "unknown_layer_files": len(unknown_layer), "import_cycles": cycles,
                         "affected_tests": len(affected),
                         "detail": {"unanalysed_paths": unanalysed, "affected_test_files": affected,
                                    "gaps": sorted({gap for p in touched for gap in files[p].get("gaps", ())})}}
    return {"kind": "measured", "runner": BOUNDARY_VERSION, "input_sha256": sha256_value(spec), "context_identity": context["identity"],
            "results": results, "reserved_units": units, "scope": FAMILIES[BOUNDARY_VERSION]["claim_scope"],
            "qualification_status": "UNPROVEN", "limitations": list(FAMILIES[BOUNDARY_VERSION]["limitations"])}


# ---- the registry's dispatch ------------------------------------------------------------------

def validate_experiment(spec: Any) -> int:
    """Validate a spec against its family's schema and return the reserved units (the family's
    own work bound). A family that is registered but not runnable refuses here, before any
    budget is reserved, and says why."""
    if not isinstance(spec, Mapping) or "kind" not in spec:
        raise IntentRefused("experiment spec must name a registered family")
    row = family(spec["kind"])
    if not row["runnable"]:
        raise IntentRefused(f"experiment family {spec['kind']} is registered but not runnable: {row['not_runnable_because']}")
    if spec["kind"] == LOOKUP_VERSION:
        units = validate_probe(spec)
    else:
        units = validate_boundary(spec)
    if units > row["max_units"]:
        raise IntentRefused("experiment exceeds its family's work bound")
    return units


def strategy_of(spec: Mapping[str, Any], candidate: Mapping[str, Any]) -> str | None:
    """The strategy key a candidate is bound to under this spec, or None when it is not bound."""
    kind = spec.get("kind") if isinstance(spec, Mapping) else None
    if kind == LOOKUP_VERSION:
        name = candidate.get("probe_strategy")
        return name if isinstance(name, str) and name in spec.get("strategies", ()) else None
    if kind == BOUNDARY_VERSION:
        name = candidate.get("id")
        return name if isinstance(name, str) and isinstance(spec.get("strategies"), Mapping) and name in spec["strategies"] else None
    return None


def run_experiment(spec: Mapping[str, Any], *, context: Mapping[str, Any], check_stop=lambda: None) -> dict:
    """Run a validated spec through its family's reviewed runner. Never a model-supplied one."""
    validate_experiment(spec)
    if spec["kind"] == LOOKUP_VERSION:
        return run_probe(spec, check_stop=check_stop)
    return run_boundary_analysis(spec, context=context, check_stop=check_stop)


def registry_record() -> dict:
    """The registry as data, for the reasoner's context and for records."""
    return {"schema": "dark-factory/experiment-registry", "schema_version": "1.0",
            "families": {kind: {k: v for k, v in row.items()} for kind, row in FAMILIES.items()}}


__all__ = ["BOUNDARY_METRICS", "BOUNDARY_VERSION", "FAMILIES", "family", "metrics_for", "registry_record", "run_boundary_analysis",
           "run_experiment", "strategy_of", "validate_boundary", "validate_experiment"]
