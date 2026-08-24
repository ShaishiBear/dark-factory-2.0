#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("FACTORY_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
REGISTRY = ".factory/holdout/immunity.json"
ID = re.compile(r"^IMM-[1-9][0-9]*$")


def die(message: str) -> None:
    print(f"IMMUNITY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_file(root: Path, rel: object) -> Path:
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        die("immunity assertion path is invalid")
    path = (root / rel).resolve()
    if root not in (path, *path.parents):
        die("immunity assertion path escapes repository root")
    if not path.is_file():
        die(f"immunity detector file is missing: {rel}")
    return path


def pointer(value: object, raw: object) -> object:
    if raw == "":
        return value
    if not isinstance(raw, str) or not raw.startswith("/"):
        die("immunity JSON pointer is invalid")
    current = value
    for token in raw[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            die(f"immunity JSON pointer does not resolve: {raw}")
    return current


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read immunity detector JSON: {exc}")


def check_assertion(assertion: object, root: Path) -> None:
    if not isinstance(assertion, dict):
        die("immunity assertion must be an object")
    kind = assertion.get("kind")
    path = safe_file(root, assertion.get("path"))
    if kind == "text_contains":
        needle = assertion.get("value")
        if not isinstance(needle, str) or not needle:
            die("text_contains immunity assertion has no value")
        if needle not in path.read_text(encoding="utf-8"):
            die(f"immunity text detector no longer holds: {assertion['path']}")
        return

    data = read_json(path)
    selected = pointer(data, assertion.get("pointer"))
    minimum = assertion.get("minimum")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        die("immunity minimum must be a non-negative integer")
    if kind == "json_number_min":
        if not isinstance(selected, (int, float)) or isinstance(selected, bool) or selected < minimum:
            die(f"immunity numeric floor regressed: {assertion['path']} {assertion.get('pointer')}")
        return
    if kind == "json_array_match_min":
        field, contains = assertion.get("field"), assertion.get("contains")
        if not isinstance(selected, list) or not isinstance(field, str) or not field or not isinstance(contains, str) or not contains:
            die("json_array_match_min immunity assertion is invalid")
        matches = 0
        for item in selected:
            if not isinstance(item, dict):
                continue
            candidate = item.get(field)
            if isinstance(candidate, list) and contains in candidate:
                matches += 1
            elif candidate == contains:
                matches += 1
        if matches < minimum:
            die(f"immunity array detector regressed: {assertion['path']} requires {minimum}, saw {matches}")
        return
    die(f"unknown immunity assertion kind: {kind}")


def verify_registry(registry: object, root: Path = ROOT) -> dict:
    root = root.resolve()
    if not isinstance(registry, dict) or registry.get("version") != "1.0":
        die("immunity registry version must be 1.0")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        die("immunity registry entries must be an array")
    seen: set[str] = set()
    active_ids: list[str] = []
    assertions = 0
    for entry in entries:
        if not isinstance(entry, dict):
            die("immunity entry must be an object")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not ID.fullmatch(entry_id) or entry_id in seen:
            die("immunity entry id is invalid or duplicated")
        seen.add(entry_id)
        status = entry.get("status")
        if status not in {"active", "retired"}:
            die(f"immunity entry {entry_id} has invalid status")
        source = entry.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("kind"), str) or not source.get("kind") or not isinstance(source.get("ref"), str) or not source.get("ref"):
            die(f"immunity entry {entry_id} lacks source identity")
        for key in ("failure_class", "lesson"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                die(f"immunity entry {entry_id} lacks {key}")
        if status == "retired":
            continue
        checks = entry.get("assertions")
        if not isinstance(checks, list) or not checks:
            die(f"active immunity entry {entry_id} has no assertions")
        for assertion in checks:
            check_assertion(assertion, root)
            assertions += 1
        active_ids.append(entry_id)
    return {
        "version": "1.0", "verdict": "pass", "registry_sha256": digest(registry),
        "active_entries": len(active_ids), "assertions": assertions, "entry_ids": active_ids,
    }


def verify_current(root: Path = ROOT) -> dict:
    root = root.resolve()
    return verify_registry(read_json(safe_file(root, REGISTRY)), root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    result = verify_current()
    payload = canonical(result)
    if args.output:
        Path(args.output).write_bytes(payload)
    print(f"IMMUNITY_OK entries={result['active_entries']} assertions={result['assertions']} sha256={result['registry_sha256']}")


if __name__ == "__main__":
    main()
