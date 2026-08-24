#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("FACTORY_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
BLOCK = re.compile(
    r"<!-- factory-(contract|proof):start -->\s*```factory-\1\s*(\{.*?\})\s*```\s*"
    r"\1-sha256:\s*([0-9a-f]{64})\s*<!-- factory-\1:end -->", re.S
)
TRUST_ROOT = (
    ".archon/workflows/dark-factory-validate-pr.yaml",
    "scripts/factory_protocol.py", "scripts/factory_proof.py", "scripts/factory_evidence.py",
    "harness/", ".factory/holdout/", ".factory/locks/floor.json",
)


def die(msg: str) -> None:
    print(f"EVIDENCE_FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def canonical(v: object) -> bytes:
    return (json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(v: object) -> str:
    return hashlib.sha256(canonical(v)).hexdigest()


def run(argv: list[str], cwd: Path = ROOT, timeout: int = 300,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if check and p.returncode:
        die(f"{' '.join(argv)} failed: {((p.stdout or '') + (p.stderr or ''))[-1600:]}")
    return p


def gh_json(pr: str) -> dict:
    return json.loads(run(["gh", "pr", "view", pr, "--json",
                           "body,headRefOid,baseRefOid"], timeout=30).stdout)


def extract(body: str, kind: str) -> tuple[dict, str]:
    for match in BLOCK.finditer(body):
        if match.group(1) != kind:
            continue
        try:
            value = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            die(f"{kind} block is not JSON: {exc}")
        actual = digest(value)
        if actual != match.group(3):
            die(f"{kind} hash mismatch")
        return value, actual
    die(f"missing factory-{kind} evidence block")


def load_protocol():
    path = ROOT / "scripts" / "factory_protocol.py"
    spec = importlib.util.spec_from_file_location("factory_protocol_authoritative", path)
    if spec is None or spec.loader is None:
        die("cannot load authoritative contract validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_contract(contract: dict, expected_hash: str, issue: int) -> dict:
    validator = load_protocol()
    try:
        actual = validator.validate_contract(contract, issue)
    except SystemExit:
        die("attached contract failed authoritative schema/semantic validation")
    if actual != expected_hash:
        die("authoritative contract canonical hash disagrees with attached hash")
    return {"sha256": actual, "criteria": len(contract["behaviors"])}


def parse_harness(text: str, floors: dict) -> dict:
    def number(pattern: str, name: str) -> int:
        match = re.search(pattern, text)
        if not match:
            die(f"harness missing {name} marker")
        return int(match.group(1))

    if "GATE_OK mode=full" not in text:
        die("full harness did not reach GATE_OK")
    observed = {
        "static_checks": number(r"STATIC_OK checks=(\d+)", "STATIC_OK checks"),
        "unit_tests": number(r"UNIT_PASSED tests=(\d+)", "UNIT_PASSED"),
        "e2e_steps": number(r"E2E_PASSED steps=(\d+)", "E2E_PASSED"),
        "holdout_assertions": number(r"HOLDOUT_PASSED[^\n]*assertions=(\d+)", "HOLDOUT_PASSED"),
        "mutations_total": number(r"MUTATIONS_TOTAL=(\d+)", "MUTATIONS_TOTAL"),
        "mutations_caught": number(r"MUTATIONS_CAUGHT=(\d+)", "MUTATIONS_CAUGHT"),
        "mutations_not_injected": number(r"MUTATIONS_NOT_INJECTED=(\d+)", "MUTATIONS_NOT_INJECTED"),
    }
    for key in ("static_checks", "unit_tests", "holdout_assertions", "mutations_total"):
        if observed[key] < int(floors[key]):
            die(f"{key} regressed below ratchet: {observed[key]} < {floors[key]}")
    if observed["mutations_total"] != observed["mutations_caught"] or observed["mutations_not_injected"] != 0:
        die("mutation gate incomplete: "
            f"total={observed['mutations_total']} caught={observed['mutations_caught']} "
            f"not_injected={observed['mutations_not_injected']}")
    if observed["e2e_steps"] < 1:
        die("E2E_PASSED reported zero steps")
    return observed


def changed(base: str, head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", f"{base}...{head}"]).stdout
    return sorted(x for x in out.splitlines() if x)


def trust_root_touched(paths: list[str]) -> list[str]:
    return [p for p in paths if any(p == x or p.startswith(x) for x in TRUST_ROOT)]


def trust_root_drift(head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", "origin/main", head, "--", *TRUST_ROOT]).stdout
    return sorted(x for x in out.splitlines() if x)


def ancestor(old: str, head: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", old, head], check=False).returncode == 0


def validate_proof_fields(proof: dict, head: str) -> None:
    required = {
        "version", "test_commit", "cwd", "argv", "files", "red_exit", "red_output_sha256",
        "expected_failure", "green_commit", "green_exit",
    }
    if not isinstance(proof, dict) or required - proof.keys():
        die("proof is missing required fields")
    if proof["version"] != "1.0" or int(proof["red_exit"]) == 0 or int(proof["green_exit"]) != 0:
        die("proof does not contain a valid RED->GREEN transition")
    if str(proof["green_commit"]) != head:
        die("final GREEN proof is not bound to current PR head")
    if not isinstance(proof["expected_failure"], str) or len(proof["expected_failure"].strip()) < 3:
        die("proof expected_failure is too weak")
    argv = proof["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
        die("proof command is invalid")
    if not isinstance(proof["files"], dict) or not proof["files"]:
        die("proof has no immutable acceptance tests")


def validate_red_result(returncode: int, output: str, expected_failure: str) -> None:
    if returncode == 0:
        die("independent RED replay unexpectedly passed")
    if expected_failure.lower() not in output.lower():
        die("independent RED replay failed for the wrong reason")


def share_runtime(red_root: Path) -> None:
    for rel in ("app/backend/.venv", "app/frontend/node_modules"):
        source = ROOT / rel
        target = red_root / rel
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.symlink_to(source, target_is_directory=True)
            except OSError:
                pass


def replay_red(proof: dict) -> dict:
    test_commit = str(proof["test_commit"])
    if not ancestor(test_commit, str(proof["green_commit"])):
        die("test-author commit is not an ancestor of current GREEN head")
    diff = run(["git", "diff", "--name-only", f"{test_commit}^", test_commit]).stdout
    files = proof["files"]
    if sorted(x for x in diff.splitlines() if x) != sorted(files):
        die("test-author commit does not change exactly the declared acceptance tests")

    with tempfile.TemporaryDirectory(prefix="dark-factory-red-") as tmp:
        red_root = Path(tmp) / "worktree"
        run(["git", "worktree", "add", "--detach", str(red_root), test_commit], timeout=120)
        try:
            share_runtime(red_root)
            for rel, expected in files.items():
                path = red_root / rel
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    die(f"RED checkpoint test hash mismatch: {rel}")
            cwd = (red_root / proof["cwd"]).resolve()
            if red_root not in (cwd, *cwd.parents) or not cwd.is_dir():
                die("proof RED cwd is unsafe")
            result = run(list(proof["argv"]), cwd=cwd, timeout=300, check=False)
            output = (result.stdout or "") + (result.stderr or "")
            validate_red_result(result.returncode, output, proof["expected_failure"])
            return {
                "exit": result.returncode,
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "expected_failure": proof["expected_failure"],
            }
        finally:
            run(["git", "worktree", "remove", "--force", str(red_root)], timeout=120, check=False)
            run(["git", "worktree", "prune"], timeout=30, check=False)


def verify_proof(proof: dict, head: str) -> dict:
    validate_proof_fields(proof, head)
    if not ancestor(str(proof["test_commit"]), head):
        die("test-author commit is not an ancestor of current PR head")

    files = proof["files"]
    for rel, expected in files.items():
        path = ROOT / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            die(f"immutable acceptance test changed: {rel}")

    red = replay_red(proof)

    cwd = (ROOT / proof["cwd"]).resolve()
    if ROOT not in (cwd, *cwd.parents) or not cwd.is_dir():
        die("proof GREEN cwd is unsafe")
    green_run = run(list(proof["argv"]), cwd=cwd, timeout=300, check=False)
    green_output = (green_run.stdout or "") + (green_run.stderr or "")
    if green_run.returncode:
        die("independent GREEN replay of acceptance tests failed")

    return {
        "test_commit": proof["test_commit"],
        "green_commit": head,
        "files": files,
        "command_sha256": hashlib.sha256(canonical(
            {"cwd": proof["cwd"], "argv": proof["argv"]})).hexdigest(),
        "red_replay": red,
        "green_replay": {
            "exit": green_run.returncode,
            "output_sha256": hashlib.sha256(green_output.encode()).hexdigest(),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    first = gh_json(args.pr)
    head, base, body = first["headRefOid"], first["baseRefOid"], first["body"] or ""
    local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if local != head:
        die(f"validator worktree is stale: HEAD={local} PR={head}")

    touched = trust_root_touched(changed(base, head))
    if touched:
        die("autonomous PR touched factory trust root: " + ", ".join(touched))
    drift = trust_root_drift(head)
    if drift:
        die("PR trust root is not current with origin/main; rebase required: " + ", ".join(drift))

    contract, contract_hash = extract(body, "contract")
    proof, proof_hash = extract(body, "proof")
    issue = contract.get("issue", {}).get("number")
    if not isinstance(issue, int):
        die("contract issue identity missing")
    if not re.search(rf"(?i)\b(?:fixes|closes|resolves)\s+#{issue}\b", body):
        die("contract issue does not match PR linkage")
    contract_result = verify_contract(contract, contract_hash, issue)

    verdict = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
    if verdict.get("verdict") != "approve":
        die("evidence gate only authorizes an approve verdict")
    proof_result = verify_proof(proof, head)

    floors = json.loads(run(["git", "show", "origin/main:.factory/locks/floor.json"]).stdout)
    harness = run([sys.executable, "harness/ci.py"], timeout=1800, check=False)
    transcript = (harness.stdout or "") + (harness.stderr or "")
    if harness.returncode:
        die("full harness failed: " + transcript[-2000:])
    observed = parse_harness(transcript, floors)

    second = gh_json(args.pr)
    if second["headRefOid"] != head:
        die("PR head changed while evidence was being assembled")

    bundle = {
        "version": "2.1", "pr": int(args.pr), "issue": issue,
        "base_sha": base, "head_sha": head,
        "contract_sha256": contract_hash, "contract": contract_result,
        "proof_sha256": proof_hash, "proof": proof_result,
        "validator_verdict_sha256": digest(verdict),
        "harness_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "observed": observed,
    }
    Path(args.output).write_bytes(canonical(bundle))
    print(f"EVIDENCE_OK head={head} bundle_sha256={digest(bundle)}")


if __name__ == "__main__":
    main()
