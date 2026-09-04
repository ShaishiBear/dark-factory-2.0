#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_shapes import normalise_lists  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
FRONTEND = ROOT / "app" / "frontend"
TS_HELPER = ROOT / "scripts" / "factory_impact_ts.cjs"
TEST_MARKERS = ("/tests/", "/__tests__/", ".test.", ".spec.")


def die(message: str) -> None:
    print(f"IMPACT_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        die(f"{path} must contain an object")
    return value


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def is_test(path: str) -> bool:
    value = "/" + path.replace("\\", "/")
    return path.startswith("tests/") or any(marker in value for marker in TEST_MARKERS)


def py_module(path: Path) -> str | None:
    try:
        part = path.resolve().relative_to(BACKEND.resolve())
    except ValueError:
        return None
    pieces = list(part.with_suffix("").parts)
    if pieces and pieces[-1] == "__init__":
        pieces.pop()
    return ".".join(pieces)


def py_sources() -> list[Path]:
    roots = [BACKEND, ROOT / "tests", ROOT / "harness", ROOT / "scripts"]
    out: list[Path] = []
    for base in roots:
        if base.is_dir():
            out.extend(p for p in base.rglob("*.py") if ".venv" not in p.parts)
    return sorted(set(out))


def parse_py(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None


def py_imports(tree: ast.AST, path: Path) -> set[str]:
    imports: set[str] = set()
    current = py_module(path) or ""
    package = current.split(".") if path.name == "__init__.py" else current.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                keep = max(0, len(package) - (node.level - 1))
                imports.add(".".join([*package[:keep], node.module]).strip("."))
            else:
                imports.add(node.module)
    return imports


def py_symbols(path: Path, ranges: list[tuple[int, int]] | None = None) -> list[dict]:
    tree = parse_py(path)
    if tree is None:
        return []
    found: list[dict] = []
    stack: list[str] = []

    def visit(node: ast.AST) -> None:
        named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        name = getattr(node, "name", "") if named else ""
        if named:
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            if ranges is None or any(start <= hi and end >= lo for lo, hi in ranges):
                qual = ".".join([*stack, name])
                decorators = [ast.unparse(d) for d in getattr(node, "decorator_list", [])]
                route = any(d.split("(", 1)[0].endswith((".get", ".post", ".put", ".patch", ".delete")) for d in decorators)
                found.append({
                    "language": "python", "file": rel(path), "name": qual,
                    "line": start, "public": (not name.startswith("_") and len(stack) == 0) or route,
                })
            stack.append(name)
        for child in ast.iter_child_nodes(node):
            visit(child)
        if named:
            stack.pop()

    visit(tree)
    return found


def py_dependents(seed_files: set[str]) -> tuple[set[str], set[str]]:
    modules = {m for p in seed_files if (m := py_module(ROOT / p))}
    callers: set[str] = set()
    tests: set[str] = set()
    for path in py_sources():
        r = rel(path)
        if r in seed_files:
            continue
        tree = parse_py(path)
        if tree is None:
            continue
        imported = py_imports(tree, path)
        module_hit = any(any(x == m or x.startswith(m + ".") or m.startswith(x + ".") for x in imported) for m in modules)
        if module_hit:
            (tests if is_test(r) else callers).add(r)
    return callers, tests


def run_ts(payload: dict) -> dict:
    if not TS_HELPER.is_file():
        die("TypeScript impact helper is missing")
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        die("bun/node is required for TypeScript compiler impact analysis")
    fd, name = tempfile.mkstemp(prefix="df-impact-", suffix=".json")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run([runtime, str(TS_HELPER), str(tmp)], cwd=FRONTEND,
                              text=True, capture_output=True, timeout=60)
        if proc.returncode != 0:
            die(f"TypeScript impact analysis failed: {(proc.stderr or proc.stdout)[-1200:]}")
        value = json.loads(proc.stdout)
        if not isinstance(value, dict):
            die("TypeScript impact helper returned invalid JSON")
        return value
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        die(f"TypeScript impact analysis failed: {exc}")
    finally:
        tmp.unlink(missing_ok=True)


def diff_ranges(base_ref: str) -> dict[str, list[tuple[int, int]]]:
    proc = subprocess.run(["git", "diff", "--unified=0", f"{base_ref}...HEAD", "--",
                           "app/backend", "app/frontend/src"], cwd=ROOT,
                          text=True, capture_output=True)
    if proc.returncode != 0:
        die(f"cannot diff against {base_ref}: {proc.stderr.strip()}")
    current: str | None = None
    old_path: str | None = None
    ranges: dict[str, list[tuple[int, int]]] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("--- a/"):
            old_path = line[6:]
        elif line.startswith("+++ b/"):
            current = line[6:]
            ranges.setdefault(current, [])
        elif line.startswith("+++ /dev/null"):
            current = None
            if old_path:
                ranges.setdefault(old_path, [])
        elif current and line.startswith("@@"):
            try:
                plus = line.split("+", 1)[1].split(" ", 1)[0]
                start_s, _, count_s = plus.partition(",")
                start, count = int(start_s), int(count_s or "1")
                if count > 0:
                    ranges[current].append((start, start + count - 1))
            except (IndexError, ValueError):
                die(f"cannot parse diff hunk: {line}")
    return ranges


def analyze(files: set[str], ranges: dict[str, list[tuple[int, int]]] | None = None) -> dict:
    py_files = {p for p in files if p.endswith(".py")}
    ts_files = {p for p in files if p.endswith((".ts", ".tsx"))}
    symbols: list[dict] = []
    for path in sorted(py_files):
        if (ROOT / path).is_file():
            symbols.extend(py_symbols(ROOT / path, None if ranges is None else ranges.get(path, [])))
    py_callers, py_tests = py_dependents(py_files)
    ts = run_ts({"files": sorted(ts_files), "ranges": ranges or {}}) if ts_files else {"symbols": [], "callers": [], "tests": []}
    all_symbols = symbols + list(ts.get("symbols", []))
    callers = py_callers | set(ts.get("callers", []))
    tests = py_tests | set(ts.get("tests", []))
    public = [s for s in all_symbols if s.get("public")]
    derived_files = sorted((callers | tests) - files)
    return {
        "symbols": all_symbols,
        "callers": sorted(callers),
        "tests": sorted(tests),
        "public_interfaces": public,
        "derived_files": derived_files,
    }


CONTEXT_LISTS = ("files", "symbols", "callers", "tests", "invariants", "adrs", "history")


def context_mode(args: argparse.Namespace) -> None:
    # Entries may be plain strings or objects carrying the canonical key; the enriched
    # artifact is always plain strings (scripts/factory_shapes.py).
    raw = normalise_lists(load(args.input), CONTEXT_LISTS, "context", die)
    seed_files = {p for p in raw.get("files", []) if isinstance(p, str)}
    if not seed_files:
        die("context has no seed files")
    impact = analyze(seed_files)
    out = dict(raw)
    out["files"] = sorted(seed_files | set(impact["derived_files"]))
    for key in ("symbols", "callers", "tests"):
        existing = list(out.get(key, []))
        derived = impact["symbols"] if key == "symbols" else impact[key]
        existing.extend(f"[derived] {canonical(item) if isinstance(item, dict) else item}" for item in derived)
        out[key] = existing
    impact_record = {"version": "1.0", "mode": "context", **impact}
    impact_record["sha256"] = hashlib.sha256(canonical(impact_record).encode()).hexdigest()
    out["deterministic_impact"] = impact_record
    Path(args.output).write_text(canonical(out) + "\n", encoding="utf-8")
    print(f"CONTEXT_IMPACT_OK seeds={len(seed_files)} derived={len(impact['derived_files'])} tests={len(impact['tests'])}")


def diff_mode(args: argparse.Namespace) -> None:
    context = load(args.context)
    ranges = diff_ranges(args.base_ref)
    changed = set(ranges)
    deleted = sorted(p for p in changed if not (ROOT / p).is_file())
    impact = analyze(changed, ranges)
    known = {p for p in context.get("files", []) if isinstance(p, str)}
    observed = set(impact["callers"]) | set(impact["tests"])
    missing = sorted(observed - known - changed)
    result = {
        "version": "1.0", "mode": "diff", "base_ref": args.base_ref,
        "changed_files": sorted(changed), "deleted_files": deleted,
        "ranges": {k: v for k, v in sorted(ranges.items())},
        **impact, "missing_context": missing,
        "risk": "high" if impact["public_interfaces"] or deleted else ("medium" if impact["callers"] else "low"),
    }
    Path(args.output).write_text(canonical(result) + "\n", encoding="utf-8")
    if missing:
        die("implementation reached files outside validated context: " + ", ".join(missing))
    print(f"CHANGE_IMPACT_OK files={len(changed)} symbols={len(impact['symbols'])} callers={len(impact['callers'])} tests={len(impact['tests'])} risk={result['risk']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("context")
    p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.set_defaults(fn=context_mode)
    p = sub.add_parser("diff")
    p.add_argument("--base-ref", default="origin/main"); p.add_argument("--context", required=True); p.add_argument("--output", required=True); p.set_defaults(fn=diff_mode)
    args = parser.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
