#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def run(argv: list[str], cwd: Path = ROOT, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if check and p.returncode:
        die(f"{' '.join(argv)} failed: {((p.stdout or '') + (p.stderr or ''))[-1600:]}")
    return p


def gh_json(pr: str) -> dict:
    return json.loads(run(["gh", "pr", "view", pr, "--json",
                           "body,headRefOid,baseRefOid"], timeout=30).stdout)


def extract(body: str, kind: str) -> tuple[dict, str]:
    for m in BLOCK.finditer(body):
        if m.group(1) != kind:
            continue
        try:
            value = json.loads(m.group(2))
        except json.JSONDecodeError as e:
            die(f"{kind} block is not JSON: {e}")
        actual = digest(value)
        if actual != m.group(3):
            die(f"{kind} hash mismatch")
        return value, actual
    die(f"missing factory-{kind} evidence block")


def parse_harness(text: str, floors: dict) -> dict:
    def number(pattern: str, name: str) -> int:
        m = re.search(pattern, text)
        if not m:
            die(f"harness missing {name} marker")
        return int(m.group(1))
    if "GATE_OK mode=full" not in text:
        die("full harness did not reach GATE_OK")
    static = number(r"STATIC_OK checks=(\d+)", "STATIC_OK checks")
    unit = number(r"UNIT_PASSED tests=(\d+)", "UNIT_PASSED")
    e2e = number(r"E2E_PASSED steps=(\d+)", "E2E_PASSED")
    holdout = number(r"HOLDOUT_PASSED[^\n]*assertions=(\d+)", "HOLDOUT_PASSED")
    total = number(r"MUTATIONS_TOTAL=(\d+)", "MUTATIONS_TOTAL")
    caught = number(r"MUTATIONS_CAUGHT=(\d+)", "MUTATIONS_CAUGHT")
    not_injected = number(r"MUTATIONS_NOT_INJECTED=(\d+)", "MUTATIONS_NOT_INJECTED")
    observed = {"static_checks": static, "unit_tests": unit, "e2e_steps": e2e,
                "holdout_assertions": holdout, "mutations_total": total,
                "mutations_caught": caught, "mutations_not_injected": not_injected}
    for key in ("static_checks", "unit_tests", "holdout_assertions", "mutations_total"):
        if observed[key] < int(floors[key]):
            die(f"{key} regressed below ratchet: {observed[key]} < {floors[key]}")
    if total != caught or not_injected != 0:
        die(f"mutation gate incomplete: total={total} caught={caught} not_injected={not_injected}")
    if e2e < 1:
        die("E2E_PASSED reported zero steps")
    return observed


def changed(base: str, head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", f"{base}...{head}"]).stdout
    return sorted(x for x in out.splitlines() if x)


def trust_root_touched(paths: list[str]) -> list[str]:
    return [p for p in paths if any(p == x or p.startswith(x) for x in TRUST_ROOT)]


def ancestor(old: str, head: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", old, head], check=False).returncode == 0


def verify_proof(proof: dict, head: str) -> dict:
    required = {"version", "test_commit", "cwd", "argv", "files", "red_exit", "green_commit", "green_exit"}
    if not isinstance(proof, dict) or required - proof.keys():
        die("proof is missing required fields")
    if proof["version"] != "1.0" or int(proof["red_exit"]) == 0 or int(proof["green_exit"]) != 0:
        die("proof does not contain a valid RED->GREEN transition")
    if not ancestor(str(proof["test_commit"]), head) or not ancestor(str(proof["green_commit"]), head):
        die("proof commits are not ancestors of current PR head")
    files = proof["files"]
    if not isinstance(files, dict) or not files:
        die("proof has no immutable acceptance tests")
    diff = run(["git", "diff", "--name-only", f"{proof['test_commit']}^", proof["test_commit"]]).stdout
    if sorted(x for x in diff.splitlines() if x) != sorted(files):
        die("test-author commit does not change exactly the declared acceptance tests")
    for rel, expected in files.items():
        p = ROOT / rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            die(f"immutable acceptance test changed: {rel}")
    argv = proof["argv"]
    cwd = (ROOT / proof["cwd"]).resolve()
    if not isinstance(argv, list) or not argv or ROOT not in (cwd, *cwd.parents):
        die("proof command is unsafe")
    p = run([str(x) for x in argv], cwd=cwd, timeout=300, check=False)
    if p.returncode:
        die("independent replay of acceptance tests failed")
    return {"test_commit": proof["test_commit"], "green_commit": proof["green_commit"],
            "files": files, "command_sha256": hashlib.sha256(canonical(
                {"cwd": proof["cwd"], "argv": argv})).hexdigest()}


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

    contract, contract_hash = extract(body, "contract")
    proof, proof_hash = extract(body, "proof")
    issue = contract.get("issue", {}).get("number")
    if not isinstance(issue, int):
        die("contract issue identity missing")
    if not re.search(rf"(?i)\b(?:fixes|closes|resolves)\s+#{issue}\b", body):
        die("contract issue does not match PR linkage")

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
        "version": "2.0", "pr": int(args.pr), "issue": issue,
        "base_sha": base, "head_sha": head,
        "contract_sha256": contract_hash, "proof_sha256": proof_hash,
        "proof": proof_result,
        "validator_verdict_sha256": digest(verdict),
        "harness_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "observed": observed,
    }
    Path(args.output).write_bytes(canonical(bundle))
    print(f"EVIDENCE_OK head={head} bundle_sha256={digest(bundle)}")


if __name__ == "__main__":
    main()
