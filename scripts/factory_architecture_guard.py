#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import posixpath
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_PREFIXES = ("app/backend/", "app/frontend/")
TEST_MARKERS = ("/tests/", "/__tests__/", ".test.", ".spec.")
TS_IMPORT = re.compile(
    r"(?:import|export)\s+(?:[^'\"]*?\sfrom\s*)?['\"]([^'\"]+)['\"]|"
    r"import\(\s*['\"]([^'\"]+)['\"]\s*\)"
)


def die(message: str) -> None:
    print(f"ARCHITECTURE_GUARD_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        die(f"{path} must contain an object")
    return value


def safe_repo_file(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts and path == p.as_posix()


def is_product(path: str) -> bool:
    value = "/" + path.replace("\\", "/")
    return path.startswith(PRODUCT_PREFIXES) and not any(marker in value for marker in TEST_MARKERS)


def layer_table(policy: dict) -> tuple[dict[str, tuple[str, ...]], dict[str, frozenset[str]]]:
    raw = policy.get("layers")
    if not isinstance(raw, list) or not raw:
        die("architecture policy layers must be a non-empty list")
    paths: dict[str, tuple[str, ...]] = {}
    allowed: dict[str, frozenset[str]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            die("architecture layer must be an object")
        layer_id = entry.get("id")
        layer_paths = entry.get("paths")
        imports = entry.get("allowed_imports")
        if not isinstance(layer_id, str) or not layer_id or layer_id in paths:
            die("architecture layer ids must be unique non-empty strings")
        if (
            not isinstance(layer_paths, list)
            or not layer_paths
            or any(not isinstance(x, str) or not safe_repo_file(x.rstrip("/") + "/x") for x in layer_paths)
        ):
            die(f"architecture layer {layer_id} has invalid paths")
        if not isinstance(imports, list) or any(not isinstance(x, str) or not x for x in imports):
            die(f"architecture layer {layer_id} has invalid allowed_imports")
        paths[layer_id] = tuple(x.rstrip("/") for x in layer_paths)
        allowed[layer_id] = frozenset(imports)
    unknown = sorted({x for values in allowed.values() for x in values if x not in paths})
    if unknown:
        die("architecture layer allowed_imports reference unknown layers: " + ", ".join(unknown))
    return paths, allowed


def classify(path: str, layer_paths: dict[str, tuple[str, ...]]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for layer_id, prefixes in layer_paths.items():
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                candidates.append((len(prefix), layer_id))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        die(f"path {path} matches multiple equally specific architecture layers")
    return candidates[0][1]


def forbidden_edges(policy: dict, edges: Iterable[tuple[str, str]]) -> list[str]:
    layer_paths, allowed = layer_table(policy)
    violations: set[str] = set()
    for source, target in edges:
        source_layer = classify(source, layer_paths)
        target_layer = classify(target, layer_paths)
        if source_layer is None or target_layer is None or source_layer == target_layer:
            continue
        if target_layer not in allowed[source_layer]:
            violations.add(f"{source} -> {target} [{source_layer}->{target_layer}]")
    return sorted(violations)


def cycle_sets(edges: Iterable[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        nodes.update((source, target))
        graph[source].add(target)

    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph.get(node, ())):
            if neighbor not in indices:
                visit(neighbor)
                low[node] = min(low[node], low[neighbor])
            elif neighbor in on_stack:
                low[node] = min(low[node], indices[neighbor])
        if low[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            current = stack.pop()
            on_stack.remove(current)
            component.append(current)
            if current == node:
                break
        if len(component) > 1 or (len(component) == 1 and node in graph.get(node, set())):
            found.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted(found)


def authorize_files(
    design: dict, changed_files: Iterable[str], new_files: Iterable[str]
) -> tuple[list[str], list[str]]:
    planned = design.get("planned_files")
    allowed_new = design.get("allowed_new_files")
    if not isinstance(planned, list) or not planned or any(not isinstance(x, str) for x in planned):
        die("compiled design lacks planned_files")
    if not isinstance(allowed_new, list) or any(not isinstance(x, str) for x in allowed_new):
        die("compiled design lacks allowed_new_files")
    planned_set = set(planned)
    allowed_new_set = set(allowed_new)
    if not allowed_new_set.issubset(planned_set):
        die("design allowed_new_files must be a subset of planned_files")
    product_changed = sorted(path for path in changed_files if is_product(path))
    unplanned = sorted(set(product_changed) - planned_set)
    unauthorized_new = sorted(set(new_files) & set(product_changed) - allowed_new_set)
    return unplanned, unauthorized_new


def _git(*argv: str, text: bool = True) -> str | bytes:
    proc = subprocess.run(
        ["git", *argv], cwd=ROOT, capture_output=True, text=text, timeout=120
    )
    if proc.returncode:
        detail = (proc.stderr if text else proc.stderr.decode(errors="replace"))[-1000:]
        die(f"git {' '.join(argv)} failed: {detail}")
    return proc.stdout


def resolve_ref(ref: str) -> str:
    out = _git("rev-parse", ref)
    assert isinstance(out, str)
    value = out.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        die(f"cannot resolve exact git object for {ref}")
    return value


def git_files(ref: str) -> list[str]:
    out = _git("ls-tree", "-r", "--name-only", ref, "--", "app/backend", "app/frontend/src")
    assert isinstance(out, str)
    return sorted(path for path in out.splitlines() if is_product(path))


def git_text(ref: str, path: str) -> str:
    out = _git("show", f"{ref}:{path}")
    assert isinstance(out, str)
    return out


def module_name(path: str) -> str | None:
    prefix = "app/backend/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    parts = list(PurePosixPath(path[len(prefix):]).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def python_edges(ref: str, files: list[str]) -> set[tuple[str, str]]:
    py_files = [path for path in files if path.endswith(".py")]
    modules = {name: path for path in py_files if (name := module_name(path))}
    edges: set[tuple[str, str]] = set()

    def resolve(name: str) -> str | None:
        current = name
        while current:
            if current in modules:
                return modules[current]
            current = current.rpartition(".")[0]
        return None

    for source in py_files:
        try:
            tree = ast.parse(git_text(ref, source), filename=source)
        except SyntaxError as exc:
            die(f"cannot parse {source} at {ref}: {exc}")
        current = module_name(source) or ""
        package = current.split(".") if source.endswith("/__init__.py") else current.split(".")[:-1]
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    keep = max(0, len(package) - (node.level - 1))
                    base = ".".join([*package[:keep], base]).strip(".")
                if base:
                    names.add(base)
                    for alias in node.names:
                        if alias.name != "*":
                            names.add(f"{base}.{alias.name}")
        for name in names:
            if target := resolve(name):
                if target != source:
                    edges.add((source, target))
    return edges


def _resolve_ts(source: str, spec: str, file_set: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source), spec))
    candidates = [
        base,
        base + ".ts",
        base + ".tsx",
        base + ".js",
        base + ".jsx",
        base + "/index.ts",
        base + "/index.tsx",
        base + "/index.js",
        base + "/index.jsx",
    ]
    return next((path for path in candidates if path in file_set), None)


def typescript_edges(ref: str, files: list[str]) -> set[tuple[str, str]]:
    ts_files = [path for path in files if path.endswith((".ts", ".tsx", ".js", ".jsx"))]
    file_set = set(ts_files)
    edges: set[tuple[str, str]] = set()
    for source in ts_files:
        text = git_text(ref, source)
        for match in TS_IMPORT.finditer(text):
            spec = match.group(1) or match.group(2)
            if spec and (target := _resolve_ts(source, spec, file_set)) and target != source:
                edges.add((source, target))
    return edges


def graph_edges(ref: str) -> set[tuple[str, str]]:
    files = git_files(ref)
    return python_edges(ref, files) | typescript_edges(ref, files)


def changed_files(base_ref: str, head_ref: str) -> list[str]:
    out = _git("diff", "--name-only", f"{base_ref}...{head_ref}")
    assert isinstance(out, str)
    return sorted(path for path in out.splitlines() if path)


def new_product_files(base_ref: str, head_ref: str) -> list[str]:
    out = _git("diff", "--name-status", f"{base_ref}...{head_ref}")
    assert isinstance(out, str)
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].startswith("A") and is_product(parts[-1]):
            result.append(parts[-1])
    return sorted(result)


def scope_bytes(ref: str, prefixes: Iterable[str]) -> int:
    total = 0
    for prefix in prefixes:
        out = _git("ls-tree", "-r", "-l", ref, "--", prefix)
        assert isinstance(out, str)
        for line in out.splitlines():
            left, sep, _path = line.partition("\t")
            if not sep:
                continue
            bits = left.split()
            if len(bits) >= 4 and bits[1] == "blob" and bits[3].isdigit():
                total += int(bits[3])
    return total


def debt_growth(policy: dict, base_ref: str, head_ref: str, changed: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in policy.get("debt", []):
        if not isinstance(entry, dict) or entry.get("mode") != "no-growth":
            continue
        paths = entry.get("paths")
        if not isinstance(paths, list):
            die("no-growth debt paths are invalid")
        touched = any(
            path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
            for path in changed
            for prefix in paths
        )
        base_bytes = scope_bytes(base_ref, paths)
        head_bytes = scope_bytes(head_ref, paths)
        result[str(entry.get("id"))] = {
            "base_bytes": base_bytes,
            "head_bytes": head_bytes,
            "delta_bytes": head_bytes - base_bytes,
            "touched": touched,
        }
    return result


def compute(policy: dict, design: dict, base_ref: str, head_ref: str) -> dict:
    graph_cfg = policy.get("graph")
    if not isinstance(graph_cfg, dict):
        die("architecture policy graph must be an object")
    for key in ("enforce_new_forbidden_edges", "enforce_new_cycles", "enforce_no_growth_debt"):
        if not isinstance(graph_cfg.get(key), bool):
            die(f"architecture policy graph.{key} must be boolean")

    base_sha = resolve_ref(base_ref)
    head_sha = resolve_ref(head_ref)
    base_edges = graph_edges(base_sha)
    head_edges = graph_edges(head_sha)
    base_forbidden = forbidden_edges(policy, base_edges)
    head_forbidden = forbidden_edges(policy, head_edges)
    new_forbidden = sorted(set(head_forbidden) - set(base_forbidden))
    base_cycles = cycle_sets(base_edges)
    head_cycles = cycle_sets(head_edges)
    base_cycle_keys = {"|".join(cycle) for cycle in base_cycles}
    new_cycles = [cycle for cycle in head_cycles if "|".join(cycle) not in base_cycle_keys]
    changed = changed_files(base_sha, head_sha)
    new_files = new_product_files(base_sha, head_sha)
    unplanned, unauthorized_new = authorize_files(design, changed, new_files)
    debt = debt_growth(policy, base_sha, head_sha, changed)
    growth = sorted(
        debt_id
        for debt_id, values in debt.items()
        if values["touched"] and values["delta_bytes"] > 0
    )

    result = {
        "version": "1.0",
        "policy_sha256": digest(policy),
        "design_sha256": digest(design),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": changed,
        "production_changed_files": sorted(path for path in changed if is_product(path)),
        "new_product_files": new_files,
        "unplanned_product_files": unplanned,
        "unauthorized_new_files": unauthorized_new,
        "forbidden_edges": {"base": base_forbidden, "head": head_forbidden, "new": new_forbidden},
        "cycles": {"base": base_cycles, "head": head_cycles, "new": new_cycles},
        "debt_bytes": debt,
        "no_growth_regressions": growth,
    }

    failures = []
    if unplanned:
        failures.append("production files changed outside compiled design: " + ", ".join(unplanned))
    if unauthorized_new:
        failures.append("new production files were not explicitly authorized: " + ", ".join(unauthorized_new))
    if graph_cfg["enforce_new_forbidden_edges"] and new_forbidden:
        failures.append("new forbidden dependency edges: " + "; ".join(new_forbidden))
    if graph_cfg["enforce_new_cycles"] and new_cycles:
        failures.append("new dependency cycles: " + "; ".join(" -> ".join(x) for x in new_cycles))
    if graph_cfg["enforce_no_growth_debt"] and growth:
        failures.append("no-growth architecture debt increased: " + ", ".join(growth))
    if failures:
        die(" | ".join(failures))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default=".factory/architecture.json")
    parser.add_argument("--design", required=True)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    policy = load(args.policy)
    design = load(args.design)
    result = compute(policy, design, args.base_ref, args.head_ref)
    Path(args.output).write_bytes(canonical(result))
    print(
        "ARCHITECTURE_GUARD_OK "
        f"production_files={len(result['production_changed_files'])} "
        f"new_forbidden_edges={len(result['forbidden_edges']['new'])} "
        f"new_cycles={len(result['cycles']['new'])} "
        f"no_growth_regressions={len(result['no_growth_regressions'])} "
        f"sha256={digest(result)}"
    )


if __name__ == "__main__":
    main()
