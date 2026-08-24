#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from factory_protocol import canonical, validate_contract

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = {"proceed", "prefactor", "decompose"}
CONVERGENCE = {"improves", "neutral", "regresses"}
CONFORMANCE = {"conform", "deviates"}


def die(message: str) -> None:
    print(f"ARCHITECTURE_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        die(f"{path} must contain an object")
    return value


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strings(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        die(f"{name} must be a list")
    if not allow_empty and not value:
        die(f"{name} must not be empty")
    if any(not isinstance(x, str) or not x.strip() for x in value):
        die(f"{name} must contain non-empty strings")
    if len(set(value)) != len(value):
        die(f"{name} must not contain duplicates")
    return list(value)


def validate_policy(policy: dict) -> str:
    if policy.get("version") != "1.0":
        die("architecture policy version must be 1.0")
    for key in ("principles", "migrations", "debt"):
        if not isinstance(policy.get(key), list) or not policy[key]:
            die(f"architecture policy {key} must be a non-empty list")
    seen: set[str] = set()
    for entry in policy["principles"]:
        if not isinstance(entry, dict) or set(("id", "scope", "rule")) - entry.keys():
            die("architecture principle requires id/scope/rule")
        strings(entry["scope"], f"principle {entry.get('id')} scope")
        if not isinstance(entry["rule"], str) or not entry["rule"].strip():
            die("architecture principle rule must be non-empty")
        if not isinstance(entry["id"], str) or not entry["id"].strip() or entry["id"] in seen:
            die("architecture policy ids must be unique non-empty strings")
        seen.add(entry["id"])
    for key in ("migrations", "debt"):
        for entry in policy[key]:
            required = {"id", "paths"}
            if key == "migrations":
                required |= {"active", "direction"}
            else:
                required |= {"mode", "note"}
            if not isinstance(entry, dict) or required - entry.keys():
                die(f"architecture {key} entry missing required fields")
            strings(entry["paths"], f"{key} {entry.get('id')} paths")
            if not isinstance(entry["id"], str) or not entry["id"].strip() or entry["id"] in seen:
                die("architecture policy ids must be unique non-empty strings")
            seen.add(entry["id"])
            if key == "migrations":
                if not isinstance(entry["active"], bool) or not isinstance(entry["direction"], str) or not entry["direction"].strip():
                    die("migration requires boolean active and non-empty direction")
            else:
                if entry["mode"] not in {"no-growth", "acknowledge"}:
                    die("debt mode must be no-growth or acknowledge")
                if not isinstance(entry["note"], str) or not entry["note"].strip():
                    die("debt note must be non-empty")
    return digest(policy)


def overlaps(path: str, prefix: str) -> bool:
    p, q = path.rstrip("/"), prefix.rstrip("/")
    return p == q or p.startswith(q + "/") or q.startswith(p + "/")


def applicable(entries: list[dict], files: list[str], key: str, *, active_only: bool = False) -> list[str]:
    result = []
    for entry in entries:
        if active_only and not entry.get("active", False):
            continue
        if any(overlaps(path, prefix) for path in files for prefix in entry[key]):
            result.append(entry["id"])
    return sorted(result)


def exact_ids(raw: dict, key: str, expected: list[str]) -> list[str]:
    actual = sorted(strings(raw.get(key), f"governor {key}", allow_empty=True))
    if actual != expected:
        die(f"governor {key} must exactly match applicable policy ids: expected {expected}, got {actual}")
    return actual


def validate_bindings(contract: dict, context: dict, design: dict) -> tuple[str, str, str]:
    contract_hash = validate_contract(contract)
    context_hash = digest(context)
    design_hash = digest(design)
    if context.get("contract_sha256") != contract_hash:
        die("context is not bound to supplied contract")
    if design.get("contract_sha256") != contract_hash or design.get("context_sha256") != context_hash:
        die("design is not bound to supplied contract/context")
    return contract_hash, context_hash, design_hash


def compile_value(policy: dict, raw: dict, contract: dict, context: dict, design: dict) -> dict:
    policy_hash = validate_policy(policy)
    contract_hash, context_hash, design_hash = validate_bindings(contract, context, design)
    files = strings(context.get("files"), "context files")
    if raw.get("version") != "1.0":
        die("governor version must be 1.0")
    decision = raw.get("decision")
    convergence = raw.get("convergence")
    if decision not in DECISIONS or convergence not in CONVERGENCE:
        die("governor decision/convergence is invalid")
    expected_principles = applicable(policy["principles"], files, "scope")
    expected_migrations = applicable(policy["migrations"], files, "paths", active_only=True)
    expected_debt = applicable(policy["debt"], files, "paths")
    principles = exact_ids(raw, "principles", expected_principles)
    migrations = exact_ids(raw, "migrations", expected_migrations)
    debts = exact_ids(raw, "debts", expected_debt)
    rationale = strings(raw.get("rationale"), "governor rationale")
    required_changes = strings(raw.get("required_changes"), "governor required_changes", allow_empty=True)
    if convergence == "regresses" and decision == "proceed":
        die("a regressing design cannot proceed")
    if decision in {"prefactor", "decompose"} and not required_changes:
        die(f"{decision} requires concrete required_changes")
    if decision == "proceed" and required_changes:
        die("proceed must not carry required structural changes")
    return {
        "version": "1.0",
        "policy_sha256": policy_hash,
        "contract_sha256": contract_hash,
        "context_sha256": context_hash,
        "design_sha256": design_hash,
        "decision": decision,
        "convergence": convergence,
        "principles": principles,
        "migrations": migrations,
        "debts": debts,
        "rationale": rationale,
        "required_changes": required_changes,
        "source_files": sorted(files),
    }


def enforce_scope_value(governor: dict, action: str) -> None:
    if governor.get("version") != "1.0" or governor.get("decision") not in DECISIONS:
        die("compiled governor artifact is invalid")
    if action not in {"implement", "decompose"}:
        die("scope action must be implement or decompose")
    if governor["decision"] != "proceed" and action != "decompose":
        die(f"architecture decision {governor['decision']} vetoes implementation")


def validate_governor_binding(
    governor: dict, policy_hash: str, contract_hash: str, context_hash: str, design_hash: str
) -> str:
    if governor.get("version") != "1.0" or governor.get("decision") not in DECISIONS:
        die("compiled governor artifact is invalid")
    expected = {
        "policy_sha256": policy_hash,
        "contract_sha256": contract_hash,
        "context_sha256": context_hash,
        "design_sha256": design_hash,
    }
    for key, value in expected.items():
        if governor.get(key) != value:
            die(f"compiled governor {key} does not match supplied artifacts")
    if governor["decision"] != "proceed":
        die(f"implementation exists after architecture decision {governor['decision']}")
    return digest(governor)


def compile_conformance_value(
    policy: dict,
    raw: dict,
    contract: dict,
    context: dict,
    design: dict,
    governor: dict,
    *,
    head_sha: str,
    changed_files: list[str],
    diff_sha256: str,
) -> dict:
    policy_hash = validate_policy(policy)
    contract_hash, context_hash, design_hash = validate_bindings(contract, context, design)
    governor_hash = validate_governor_binding(
        governor, policy_hash, contract_hash, context_hash, design_hash
    )
    files = strings(changed_files, "changed files")
    if raw.get("version") != "1.0":
        die("conformance version must be 1.0")
    verdict = raw.get("verdict")
    convergence = raw.get("convergence")
    if verdict not in CONFORMANCE or convergence not in CONVERGENCE:
        die("conformance verdict/convergence is invalid")
    principles = exact_ids(raw, "principles", applicable(policy["principles"], files, "scope"))
    migrations = exact_ids(
        raw, "migrations", applicable(policy["migrations"], files, "paths", active_only=True)
    )
    debts = exact_ids(raw, "debts", applicable(policy["debt"], files, "paths"))
    rationale = strings(raw.get("rationale"), "conformance rationale")
    findings = strings(raw.get("findings"), "conformance findings", allow_empty=True)
    if verdict == "conform" and convergence == "regresses":
        die("a regressing implementation cannot conform")
    if verdict == "deviates" and not findings:
        die("architectural deviation requires concrete findings")
    if verdict == "conform" and findings:
        die("conforming implementation must not carry deviation findings")
    if not isinstance(head_sha, str) or len(head_sha) < 7:
        die("invalid implementation head sha")
    if not isinstance(diff_sha256, str) or len(diff_sha256) != 64:
        die("invalid implementation diff hash")
    return {
        "version": "1.0",
        "policy_sha256": policy_hash,
        "contract_sha256": contract_hash,
        "context_sha256": context_hash,
        "design_sha256": design_hash,
        "governor_sha256": governor_hash,
        "head_sha": head_sha,
        "diff_sha256": diff_sha256,
        "verdict": verdict,
        "convergence": convergence,
        "principles": principles,
        "migrations": migrations,
        "debts": debts,
        "rationale": rationale,
        "findings": findings,
        "changed_files": sorted(files),
    }


def git_diff_state(base_ref: str | None = None) -> tuple[str, list[str], str]:
    base = (base_ref or os.environ.get("FACTORY_BASE_REF", "")).strip() or "origin/main"
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        names = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...{head}"], cwd=ROOT, text=True
        )
        patch = subprocess.check_output(
            ["git", "diff", "--binary", f"{base}...{head}"], cwd=ROOT
        )
    except subprocess.CalledProcessError as exc:
        die(f"cannot compute implementation diff against {base}: {exc}")
    files = sorted(x for x in names.splitlines() if x)
    if not files:
        die("implementation diff is empty")
    return head, files, hashlib.sha256(patch).hexdigest()


def run_compile(args: argparse.Namespace) -> None:
    result = compile_value(
        load(args.policy), load(args.input), load(args.contract), load(args.context), load(args.design)
    )
    Path(args.output).write_bytes(canonical(result))
    print(
        f"ARCHITECTURE_OK decision={result['decision']} convergence={result['convergence']} "
        f"sha256={digest(result)} migrations={len(result['migrations'])} debt={len(result['debts'])}"
    )


def run_scope(args: argparse.Namespace) -> None:
    governor = load(args.governor)
    enforce_scope_value(governor, args.action)
    print(f"ARCHITECTURE_SCOPE_OK decision={governor['decision']} action={args.action}")


def run_conformance(args: argparse.Namespace) -> None:
    head, files, diff_hash = git_diff_state(args.base_ref)
    result = compile_conformance_value(
        load(args.policy),
        load(args.input),
        load(args.contract),
        load(args.context),
        load(args.design),
        load(args.governor),
        head_sha=head,
        changed_files=files,
        diff_sha256=diff_hash,
    )
    Path(args.output).write_bytes(canonical(result))
    if result["verdict"] != "conform":
        die("finished implementation deviates from governed architecture")
    print(
        f"ARCHITECTURE_CONFORM_OK head={head} sha256={digest(result)} "
        f"convergence={result['convergence']} files={len(files)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compile")
    p.add_argument("--policy", required=True); p.add_argument("--input", required=True)
    p.add_argument("--contract", required=True); p.add_argument("--context", required=True)
    p.add_argument("--design", required=True); p.add_argument("--output", required=True)
    p.set_defaults(fn=run_compile)
    p = sub.add_parser("scope")
    p.add_argument("--governor", required=True); p.add_argument("--action", required=True)
    p.set_defaults(fn=run_scope)
    p = sub.add_parser("conformance")
    p.add_argument("--policy", required=True); p.add_argument("--input", required=True)
    p.add_argument("--contract", required=True); p.add_argument("--context", required=True)
    p.add_argument("--design", required=True); p.add_argument("--governor", required=True)
    p.add_argument("--output", required=True); p.add_argument("--base-ref")
    p.set_defaults(fn=run_conformance)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
