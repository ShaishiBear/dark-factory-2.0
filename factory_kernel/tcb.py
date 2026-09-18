"""The kernel's trusted computing base record and its verifier (WP06, SPECIFICATION 5 and 15.1).

`.factory/tcb.json` classifies every module of `factory_kernel` as proof policy, privilege
enforcement, independently trusted observer, orchestration, proposal or ui, names the
privileged roots (the modules that hold or use credentials and perform mutations), the modules
permitted to import those roots, and the import closure this record was computed from. This
module derives the same facts from the actual source and compares them, so the record can
only change by a reviewed edit that describes the change.

Two things this does and does not establish. It DOES establish that a proposal or ui module
cannot reach a privileged root through Python imports without the record naming the path as a
known violation, and that the set of privileged entrypoints is what the record says. It does
NOT establish process isolation: an import test supplements the OS/network boundary of the
broker (SPEC 5), it never replaces it. Until that boundary exists, orchestration modules that
hold credentials are classified `orchestration` with `trusted_until_boundary: true`, and no
TCB reduction is claimed (SPEC 5, last sentence of the process-boundary paragraph).

Scope of the record. Granularity is the module (WORK_PACKAGES WP06 speaks of "actual
functions"; a function-level record is future work). `permitted_privileged_entrypoints` is
DESCRIPTIVE: the set of modules that import a root today, so that a new importer is a reviewed
change; it is not a statement that those modules should hold credentials (frontdoor_http, for
one, is both permitted and a recorded violation for exactly that reason). Known violations are
keyed by module and shortest path; a second, longer path of an already-violating module is
caught only by the graph digest. Per-path accounting belongs to the day a reduction is claimed.
"""
from __future__ import annotations

import ast
from collections import deque
from pathlib import Path
from typing import Any, Mapping

from .canonical import sha256_value
from .programme import parse_json

TCB_PATH = ".factory/tcb.json"
SCHEMA = "dark-factory/tcb"
SCHEMA_VERSION = "1.0"
CLASSES = ("proof-policy", "privilege-enforcement", "trusted-observer", "orchestration", "proposal", "ui")
# Classes that must never reach a privileged root by import.
UNPRIVILEGED_CLASSES = frozenset({"proposal", "ui"})


class TcbRefused(ValueError):
    """The record is malformed or the source no longer matches it."""


def _module_deps(path: Path, known: set[str]) -> set[str]:
    tree = ast.parse(path.read_bytes().decode("utf-8", errors="replace"))
    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module:
                    deps.add(node.module.split(".")[0])
                else:
                    deps.update(alias.name for alias in node.names)
            elif node.module and node.module.startswith("factory_kernel."):
                deps.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("factory_kernel."):
                    deps.add(alias.name.split(".")[1])
    return {dep for dep in deps if dep in known}


def import_graph(kernel_root: str | Path) -> dict[str, list[str]]:
    """Intra-package imports of every module in factory_kernel, from the AST (no execution).

    The package `__init__` is a node of its own AND its imports are imports of every other
    module: importing any kernel module executes `__init__` first, so a root imported there is
    reached by everything. Granularity is the module, not the function (WORK_PACKAGES WP06 says
    "actual functions"; this record classifies modules and says so)."""
    root = Path(kernel_root)
    modules = sorted(p.stem for p in root.glob("*.py") if p.stem != "__init__")
    known = set(modules)
    init_path = root / "__init__.py"
    init_deps = _module_deps(init_path, known) if init_path.is_file() else set()
    graph: dict[str, list[str]] = {"__init__": sorted(init_deps)}
    for name in modules:
        deps = _module_deps(root / f"{name}.py", known) | init_deps
        graph[name] = sorted(dep for dep in deps if dep != name)
    return graph


def closure(graph: Mapping[str, list[str]], module: str) -> set[str]:
    seen: set[str] = set()
    queue = deque(graph.get(module, ()))
    while queue:
        dep = queue.popleft()
        if dep not in seen:
            seen.add(dep)
            queue.extend(graph.get(dep, ()))
    return seen


def first_path(graph: Mapping[str, list[str]], module: str, targets: set[str]) -> list[str] | None:
    """Shortest import path from `module` to any target, as a list of modules, or None."""
    previous: dict[str, str | None] = {module: None}
    queue = deque([module])
    while queue:
        current = queue.popleft()
        for dep in graph.get(current, ()):
            if dep in previous:
                continue
            previous[dep] = current
            if dep in targets:
                path = [dep]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])  # type: ignore[arg-type]
                return list(reversed(path))
            queue.append(dep)
    return None


def load_record(path: str | Path) -> dict:
    raw = Path(path).read_bytes()
    try:
        value = parse_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TcbRefused(f"tcb record is not strict JSON: {exc}") from exc
    return validate_record(value)


def validate_record(value: Any) -> dict:
    fields = {"schema", "schema_version", "classes", "privileged_roots", "permitted_privileged_entrypoints",
              "modules", "known_violations", "graph_sha256", "closure_bounds"}
    if not isinstance(value, dict) or set(value) != fields:
        raise TcbRefused(f"tcb record needs exactly {sorted(fields)}")
    if value["schema"] != SCHEMA or value["schema_version"] != SCHEMA_VERSION:
        raise TcbRefused("tcb record schema refused")
    if tuple(value["classes"]) != CLASSES:
        raise TcbRefused("tcb classes must be exactly the fixed vocabulary")
    modules = value["modules"]
    if not isinstance(modules, dict) or not modules:
        raise TcbRefused("tcb modules must be a nonempty object")
    for name, entry in modules.items():
        if (not isinstance(entry, dict) or set(entry) - {"class", "reason", "trusted_until_boundary"}
                or entry.get("class") not in CLASSES or not isinstance(entry.get("reason"), str) or not entry["reason"]):
            raise TcbRefused(f"tcb module entry {name!r} is malformed")
        if "trusted_until_boundary" in entry and (entry["trusted_until_boundary"] is not True or entry["class"] != "orchestration"):
            raise TcbRefused(f"trusted_until_boundary is only true, and only on orchestration: {name!r}")
    roots = value["privileged_roots"]
    entrypoints = value["permitted_privileged_entrypoints"]
    if (not isinstance(roots, list) or not roots or any(r not in modules for r in roots)
            or not isinstance(entrypoints, list) or any(e not in modules for e in entrypoints)):
        raise TcbRefused("privileged roots and permitted entrypoints must name classified modules")
    for root in roots:
        if modules[root]["class"] != "privilege-enforcement":
            raise TcbRefused(f"privileged root {root!r} must be privilege-enforcement")
    violations = value["known_violations"]
    if not isinstance(violations, list):
        raise TcbRefused("known_violations must be a list")
    for item in violations:
        if (not isinstance(item, dict) or set(item) != {"module", "path", "reason"} or item["module"] not in modules
                or not isinstance(item["path"], list) or len(item["path"]) < 2 or item["path"][0] != item["module"]
                or item["path"][-1] not in roots or not isinstance(item["reason"], str) or not item["reason"]):
            raise TcbRefused("known_violations entries must name a classified module, its import path to a root and a reason")
    closures = value["closure_bounds"]
    if not isinstance(closures, dict) or not closures:
        raise TcbRefused("closure_bounds must name at least one closure")
    for name, bounds in closures.items():
        if (not isinstance(name, str) or not name or not isinstance(bounds, dict)
                or set(bounds) != {"files", "per_file_max_bytes", "total_max_bytes"}
                or type(bounds["per_file_max_bytes"]) is not int or type(bounds["total_max_bytes"]) is not int
                or not isinstance(bounds["files"], list) or not bounds["files"]
                or not all(isinstance(f, str) for f in bounds["files"])):
            raise TcbRefused(f"closure {name!r} must list files with integer per-file and total byte bounds")
    if not isinstance(value["graph_sha256"], str) or len(value["graph_sha256"]) != 64:
        raise TcbRefused("graph_sha256 must be a sha256")
    return value


def verify(record: Mapping[str, Any], repo_root: str | Path) -> dict:
    """Compare the record with the source under `repo_root`. Returns the derived facts; raises
    TcbRefused with the first discrepancy. Nothing is executed or imported."""
    repo = Path(repo_root)
    graph = import_graph(repo / "factory_kernel")
    modules = record["modules"]
    unclassified = sorted(set(graph) - set(modules))
    if unclassified:
        raise TcbRefused(f"unclassified kernel modules: {unclassified}")
    missing = sorted(set(modules) - set(graph))
    if missing:
        raise TcbRefused(f"classified modules absent from the source: {missing}")
    graph_sha = sha256_value(graph)
    if graph_sha != record["graph_sha256"]:
        raise TcbRefused(f"the kernel import graph changed (recorded {record['graph_sha256'][:12]}, actual {graph_sha[:12]}); describe the change in the record")
    roots = set(record["privileged_roots"])
    permitted = set(record["permitted_privileged_entrypoints"]) | roots
    for name, deps in graph.items():
        if roots & set(deps) and name not in permitted:
            raise TcbRefused(f"{name!r} imports a privileged root without being a permitted entrypoint")
    known = {(item["module"], tuple(item["path"])) for item in record["known_violations"]}
    found: set[tuple[str, tuple[str, ...]]] = set()
    for name, entry in modules.items():
        if entry["class"] in UNPRIVILEGED_CLASSES and name not in roots:
            path = first_path(graph, name, roots)
            if path is not None:
                found.add((name, tuple(path)))
    unknown = sorted(found - known)
    if unknown:
        raise TcbRefused(f"proposal/ui modules reach a privileged root without a recorded violation: {unknown[:3]}")
    stale = sorted(known - found)
    if stale:
        raise TcbRefused(f"recorded violations no longer exist; remove them so the allowance shrinks: {stale[:3]}")
    closures: dict[str, dict] = {}
    for closure_name, bounds in record["closure_bounds"].items():
        sizes = {}
        for rel in bounds["files"]:
            path = repo / rel
            if not path.is_file():
                raise TcbRefused(f"closure {closure_name!r} file missing: {rel}")
            size = len(path.read_bytes().replace(b"\r\n", b"\n"))
            if size > bounds["per_file_max_bytes"]:
                raise TcbRefused(f"closure {closure_name!r} file {rel} is {size} bytes, above the per-file bound {bounds['per_file_max_bytes']}")
            sizes[rel] = size
        total = sum(sizes.values())
        if total > bounds["total_max_bytes"]:
            raise TcbRefused(f"closure {closure_name!r} total {total} bytes is above the bound {bounds['total_max_bytes']}")
        closures[closure_name] = {"sizes": sizes, "total": total}
    for name, entry in modules.items():
        if entry["class"] in UNPRIVILEGED_CLASSES and name in roots:
            raise TcbRefused(f"{name!r} cannot be both a privileged root and {entry['class']}")
    return {"modules": len(graph), "graph_sha256": graph_sha, "violations": sorted(found), "closures": closures,
            "privileged_entrypoints": sorted(permitted - roots),
            "trusted_until_boundary": sorted(n for n, e in modules.items() if e.get("trusted_until_boundary"))}


def verify_repository(repo_root: str | Path) -> dict:
    return verify(load_record(Path(repo_root) / TCB_PATH), repo_root)


__all__ = ["CLASSES", "TCB_PATH", "TcbRefused", "closure", "first_path", "import_graph", "load_record", "validate_record",
           "verify", "verify_repository"]
