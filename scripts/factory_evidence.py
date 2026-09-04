#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, importlib.util, json, os, re, subprocess, sys, tempfile
from pathlib import Path

# Code lives beside this file (HERE); the tree under test is the working directory (ROOT).
# The kernel runs every trust-root program from its own checkout of main with cwd set to the
# PR worktree, so a PR's copy of this program is never the authority that judges it (D-036).
# The authoritative validators below are loaded from beside this file, never from ROOT: a PR
# head's copy of a validator must not be the program that judges that PR.
HERE = Path(__file__).resolve().parent
ROOT = Path.cwd().resolve()
BLOCK = re.compile(
    r"<!-- factory-(contract|proof|design):start -->\s*```factory-\1\s*(\{.*?\})\s*```\s*"
    r"\1-sha256:\s*([0-9a-f]{64})\s*<!-- factory-\1:end -->", re.S
)
TRUST_ROOT = (
    "factory_kernel/",
    ".factory/kernel.json", ".factory/evidence-spine.json", ".factory/prompts/",
    ".factory/architecture.json", ".factory/holdout/", ".factory/locks/",
    "scripts/", "harness/", ".github/", "deploy/systemd/",
    "FACTORY_RULES.md", "MISSION.md", "CLAUDE.md",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GITHUB_CREDENTIALS = ("GH_TOKEN", "GITHUB_TOKEN")
VALIDATION_CREDENTIALS = (
    "DATABASE_URL", "OPENROUTER_API_KEY", "JWT_SECRET", "SUPADATA_API_KEY",
    "YOUTUBE_CHANNEL_ID", "DARK_FACTORY_E2E_EMAIL", "DARK_FACTORY_E2E_PASSWORD",
)
PROVIDER_CREDENTIAL_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "GOOGLE_", "AZURE_")


def _child_env(scope: str = "none") -> dict[str, str]:
    if scope not in {"none", "github", "validation"}:
        raise ValueError(f"invalid evidence credential scope: {scope}")
    original = dict(os.environ)

    def sensitive(name: str) -> bool:
        return (
            name in GITHUB_CREDENTIALS
            or name in VALIDATION_CREDENTIALS
            or name.startswith(PROVIDER_CREDENTIAL_PREFIXES)
        )

    child = {key: value for key, value in original.items() if not sensitive(key)}
    allowed = GITHUB_CREDENTIALS if scope == "github" else VALIDATION_CREDENTIALS if scope == "validation" else ()
    for key in allowed:
        if original.get(key):
            child[key] = original[key]
    return child


def die(msg: str) -> None:
    print(f"EVIDENCE_FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def canonical(v: object) -> bytes:
    return (json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(v: object) -> str:
    return hashlib.sha256(canonical(v)).hexdigest()


def run(argv: list[str], cwd: Path = ROOT, timeout: int = 300,
        check: bool = True, credential_scope: str = "none") -> subprocess.CompletedProcess[str]:
    p = subprocess.run(
        argv, cwd=cwd, env=_child_env(credential_scope), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and p.returncode:
        die(f"{' '.join(argv)} failed: {((p.stdout or '') + (p.stderr or ''))[-1600:]}")
    return p


def gh_json(pr: str) -> dict:
    return json.loads(run(["gh", "pr", "view", pr, "--json",
                           "body,headRefOid,baseRefOid"], timeout=30, credential_scope="github").stdout)


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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        die(f"cannot load authoritative {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_protocol():
    return load_module(HERE / "factory_protocol.py", "factory_protocol_authoritative")


def load_security():
    return load_module(HERE / "factory_security.py", "factory_security_authoritative")


def load_architecture_guard():
    return load_module(
        HERE / "factory_architecture_guard.py",
        "factory_architecture_guard_authoritative",
    )


def verify_contract(contract: dict, expected_hash: str, issue: int) -> dict:
    validator = load_protocol()
    try:
        actual = validator.validate_contract(contract, issue)
    except SystemExit:
        die("attached contract failed authoritative schema/semantic validation")
    if actual != expected_hash:
        die("authoritative contract canonical hash disagrees with attached hash")
    return {"sha256": actual, "criteria": len(contract["behaviors"])}


def verify_security_result(value: object) -> dict:
    if not isinstance(value, dict) or value.get("version") != "1.0":
        die("deterministic security guard returned invalid evidence")
    if value.get("verdict") != "pass":
        die("deterministic security guard did not authorize merge")
    collections: dict[str, list] = {}
    for key in ("protected_paths", "dependency_changes", "secret_findings", "findings"):
        raw = value.get(key)
        if not isinstance(raw, list):
            die(f"deterministic security guard {key} is invalid")
        collections[key] = raw
    if collections["findings"] or collections["protected_paths"] or collections["secret_findings"]:
        die("deterministic security guard pass contains contradictory findings")
    return {
        "sha256": digest(value), "verdict": "pass",
        "dependency_changes": len(collections["dependency_changes"]),
        "protected_paths": 0, "secret_findings": 0, "findings": 0,
    }


def verify_security(pr: str) -> dict:
    guard = load_security()
    try:
        result = guard.verify_pr(pr)
    except SystemExit:
        die("deterministic security guard failed while evaluating the current PR")
    return verify_security_result(result)


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
    semantic_markers = {
        "mutations_independent_caught": (r"MUTATIONS_INDEPENDENT_CAUGHT=(\d+)", "MUTATIONS_INDEPENDENT_CAUGHT"),
        "mutations_security_caught": (r"MUTATIONS_SECURITY_CAUGHT=(\d+)", "MUTATIONS_SECURITY_CAUGHT"),
    }
    for key, (pattern, marker) in semantic_markers.items():
        if key in floors:
            observed[key] = number(pattern, marker)
    ratchets = ["static_checks", "unit_tests", "holdout_assertions", "mutations_total"]
    ratchets.extend(key for key in semantic_markers if key in floors)
    for key in ratchets:
        if observed[key] < int(floors[key]):
            die(f"{key} regressed below ratchet: {observed[key]} < {floors[key]}")
    if observed["mutations_total"] != observed["mutations_caught"] or observed["mutations_not_injected"] != 0:
        die("mutation gate incomplete")
    if observed["e2e_steps"] < 1:
        die("E2E_PASSED reported zero steps")
    return observed


def changed(base: str, head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", f"{base}...{head}"]).stdout
    return sorted(x for x in out.splitlines() if x)


def binary_diff_sha(base: str, head: str) -> str:
    p = subprocess.run(["git", "diff", "--binary", f"{base}...{head}"], cwd=ROOT,
                       capture_output=True, timeout=300)
    if p.returncode:
        die("cannot compute authoritative binary diff")
    return hashlib.sha256(p.stdout).hexdigest()


def trust_root_touched(paths: list[str]) -> list[str]:
    return [p for p in paths if any(p == x or p.startswith(x) for x in TRUST_ROOT)]


def trust_root_drift(head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", "origin/main", head, "--", *TRUST_ROOT]).stdout
    return sorted(x for x in out.splitlines() if x)


def ancestor(old: str, head: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", old, head], check=False).returncode == 0


def overlaps(path: str, prefix: str) -> bool:
    p, q = path.rstrip("/"), prefix.rstrip("/")
    return p == q or p.startswith(q + "/") or q.startswith(p + "/")


def applicable(entries: object, files: list[str], path_key: str, *, active_only: bool = False) -> list[str]:
    if not isinstance(entries, list):
        die("architecture policy collection is invalid")
    result: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            die("architecture policy entry is invalid")
        if active_only and entry.get("active") is not True:
            continue
        paths = entry.get(path_key)
        if not isinstance(paths, list) or any(not isinstance(x, str) or not x for x in paths):
            die("architecture policy path scope is invalid")
        if any(overlaps(path, prefix) for path in files for prefix in paths):
            result.append(entry["id"])
    return sorted(result)


def verify_architecture(proof: dict, head: str, base: str, contract_hash: str, policy: dict,
                        *, files: list[str] | None = None, diff_sha256: str | None = None) -> dict:
    arch = proof.get("architecture_builder")
    if not isinstance(arch, dict):
        die("proof lacks builder architecture conformance")
    if proof.get("architecture_builder_sha256") != digest(arch):
        die("builder architecture conformance hash mismatch")
    if policy.get("version") != "1.0":
        die("authoritative architecture policy version must be 1.0")
    if arch.get("version") != "1.0" or arch.get("verdict") != "conform":
        die("builder architecture verdict is not conform")
    if arch.get("convergence") == "regresses":
        die("builder architecture claims conform while regressing")
    if arch.get("policy_sha256") != digest(policy):
        die("builder architecture used stale or different policy")
    if arch.get("head_sha") != head:
        die("builder architecture is not bound to current PR head")
    if arch.get("contract_sha256") != contract_hash:
        die("builder architecture is not bound to attached contract")
    if arch.get("design_sha256") != proof.get("design_sha256"):
        die("builder architecture is not bound to proof design")
    for key in ("context_sha256", "governor_sha256"):
        if not HEX64.fullmatch(str(arch.get(key, ""))):
            die(f"builder architecture {key} is invalid")
    actual_files = sorted(files if files is not None else changed(base, head))
    if sorted(arch.get("changed_files") or []) != actual_files:
        die("builder architecture changed-file set is stale or incomplete")
    actual_diff = diff_sha256 if diff_sha256 is not None else binary_diff_sha(base, head)
    if arch.get("diff_sha256") != actual_diff:
        die("builder architecture binary diff hash is stale")
    expected = {
        "principles": applicable(policy.get("principles"), actual_files, "scope"),
        "migrations": applicable(policy.get("migrations"), actual_files, "paths", active_only=True),
        "debts": applicable(policy.get("debt"), actual_files, "paths"),
    }
    for key, ids in expected.items():
        value = arch.get(key)
        if not isinstance(value, list) or sorted(value) != ids or len(value) != len(set(value)):
            die(f"builder architecture {key} does not exactly match authoritative policy applicability")
    return {
        "sha256": digest(arch), "policy_sha256": digest(policy),
        "head_sha": head, "diff_sha256": actual_diff, "changed_files": actual_files,
        "principles": expected["principles"], "migrations": expected["migrations"],
        "debts": expected["debts"], "verdict": arch["verdict"],
        "convergence": arch.get("convergence"),
    }


def verify_architecture_guard(
    proof: dict, design: dict, design_hash: str, head: str, base: str, policy: dict
) -> dict:
    if design_hash != proof.get("design_sha256"):
        die("attached design does not match proof design hash")
    builder = proof.get("architecture_guard")
    if not isinstance(builder, dict) or not HEX64.fullmatch(str(builder.get("sha256", ""))):
        die("proof lacks deterministic architecture guard provenance")
    for key in (
        "new_forbidden_edges", "new_cycles", "no_growth_regressions", "unplanned_product_files"
    ):
        if builder.get(key) != 0:
            die(f"builder architecture guard reports non-zero {key}")
    guard = load_architecture_guard()
    try:
        recomputed = guard.compute(policy, design, base, head)
    except SystemExit:
        die("authoritative architecture drift guard rejected current PR")
    recomputed_hash = guard.digest(recomputed)
    if recomputed_hash != builder["sha256"]:
        die("builder architecture guard hash does not match independent recomputation")
    if recomputed.get("base_sha") != base or recomputed.get("head_sha") != head:
        die("architecture guard is not bound to exact PR base/head")
    if recomputed.get("policy_sha256") != digest(policy) or recomputed.get("design_sha256") != design_hash:
        die("architecture guard is not bound to authoritative policy/design")
    if (
        recomputed.get("unplanned_product_files")
        or recomputed.get("unauthorized_new_files")
        or recomputed.get("forbidden_edges", {}).get("new")
        or recomputed.get("cycles", {}).get("new")
        or recomputed.get("no_growth_regressions")
    ):
        die("architecture drift guard contains a blocking deterministic regression")
    return {
        "sha256": recomputed_hash,
        "policy_sha256": recomputed["policy_sha256"],
        "design_sha256": design_hash,
        "base_sha": base,
        "head_sha": head,
        "production_changed_files": recomputed.get("production_changed_files", []),
        "new_product_files": recomputed.get("new_product_files", []),
        "new_forbidden_edges": 0,
        "new_cycles": 0,
        "no_growth_regressions": 0,
        "unplanned_product_files": 0,
    }


def verify_architecture_holdout(value: object, files: list[str], policy: dict) -> dict:
    if not isinstance(value, dict) or value.get("version") != "1.0":
        die("architecture holdout is missing or invalid")
    if value.get("verdict") != "pass" or value.get("convergence") == "regresses":
        die("architecture holdout did not authorize merge")
    expected = {
        "principles": applicable(policy.get("principles"), files, "scope"),
        "migrations": applicable(policy.get("migrations"), files, "paths", active_only=True),
        "debts": applicable(policy.get("debt"), files, "paths"),
    }
    for key, ids in expected.items():
        raw = value.get(key)
        if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
            die(f"architecture holdout {key} is invalid")
        if sorted(raw) != ids or len(raw) != len(set(raw)):
            die(f"architecture holdout {key} does not exactly match authoritative policy applicability")
    findings = value.get("findings")
    if not isinstance(findings, list):
        die("architecture holdout findings are invalid")
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("severity") not in {"critical", "high", "medium", "low"}:
            die("architecture holdout finding is malformed")
        if not isinstance(finding.get("description"), str) or not finding["description"].strip():
            die("architecture holdout finding lacks description")
        if finding["severity"] in {"critical", "high"}:
            die("architecture holdout contains a blocking finding")
    return {"sha256": digest(value), "verdict": "pass", "convergence": value.get("convergence"),
            "findings": len(findings), **expected}


def contract_ids(contract: dict) -> list[str]:
    return [b["id"] for b in contract["behaviors"]]


def plan_from_proof(proof: dict) -> dict:
    cps = []
    for cp in proof["checkpoints"]:
        cps.append({k: cp[k] for k in ("acceptance_id", "seams", "cwd", "argv", "files", "expected_failure")})
    return {
        "version": "1.0", "contract_sha256": proof["contract_sha256"],
        "design_sha256": proof["design_sha256"], "test_commit": proof["test_commit"],
        "checkpoints": cps,
    }


def validate_checkpoint(cp: object) -> dict:
    required = {"acceptance_id", "seams", "cwd", "argv", "files", "expected_failure",
                "red_exit", "red_output_sha256"}
    if not isinstance(cp, dict) or required - cp.keys():
        die("proof checkpoint missing required fields")
    if not re.fullmatch(r"AC-[1-9][0-9]*", str(cp["acceptance_id"])):
        die("proof checkpoint has invalid acceptance_id")
    for key in ("seams", "files", "argv"):
        if not isinstance(cp[key], list) or not cp[key] or any(not isinstance(x, str) or not x for x in cp[key]):
            die(f"proof checkpoint {cp['acceptance_id']} has invalid {key}")
    if not isinstance(cp["cwd"], str) or not cp["cwd"]:
        die("proof checkpoint cwd is invalid")
    if not isinstance(cp["expected_failure"], str) or len(cp["expected_failure"].strip()) < 3:
        die("proof checkpoint expected_failure is too weak")
    if int(cp["red_exit"]) == 0 or not HEX64.fullmatch(str(cp["red_output_sha256"])):
        die("proof checkpoint does not contain valid RED evidence")
    return cp


def validate_proof_fields(proof: dict, head: str, contract: dict, contract_hash: str) -> None:
    required = {
        "version", "test_commit", "contract_sha256", "design_sha256", "files",
        "checkpoints", "test_plan_sha256", "green_commit", "green_results",
    }
    if not isinstance(proof, dict) or required - proof.keys():
        die("proof is missing required fields")
    if proof["version"] != "2.0":
        die("proof version must be 2.0")
    if proof["contract_sha256"] != contract_hash:
        die("proof is not bound to attached contract")
    if not HEX64.fullmatch(str(proof["design_sha256"])):
        die("proof design hash is invalid")
    if str(proof["green_commit"]) != head:
        die("final GREEN proof is not bound to current PR head")
    if not isinstance(proof["files"], dict) or not proof["files"]:
        die("proof has no immutable acceptance tests")
    if any(not HEX64.fullmatch(str(v)) for v in proof["files"].values()):
        die("proof has invalid acceptance-test hashes")
    cps = [validate_checkpoint(cp) for cp in proof["checkpoints"]] if isinstance(proof["checkpoints"], list) else die("proof checkpoints invalid")
    ids = [cp["acceptance_id"] for cp in cps]
    expected = contract_ids(contract)
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        die("proof checkpoints must cover every contract AC exactly once")
    if any(f not in proof["files"] for cp in cps for f in cp["files"]):
        die("checkpoint references a test outside immutable proof files")
    plan = plan_from_proof(proof)
    if digest(plan) != proof["test_plan_sha256"]:
        die("proof test-plan hash mismatch")
    greens = proof["green_results"]
    if not isinstance(greens, list) or len(greens) != len(cps):
        die("proof GREEN results do not cover every checkpoint")
    green_ids = [x.get("acceptance_id") for x in greens if isinstance(x, dict)]
    if set(green_ids) != set(expected) or any(int(x.get("exit", 1)) != 0 for x in greens):
        die("proof GREEN results are incomplete or non-green")


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


def replay_red(proof: dict) -> list[dict]:
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
            results = []
            for cp in proof["checkpoints"]:
                cwd = (red_root / cp["cwd"]).resolve()
                if red_root not in (cwd, *cwd.parents) or not cwd.is_dir():
                    die(f"{cp['acceptance_id']} RED cwd is unsafe")
                result = run(list(cp["argv"]), cwd=cwd, timeout=300, check=False)
                output = (result.stdout or "") + (result.stderr or "")
                validate_red_result(result.returncode, output, cp["expected_failure"])
                results.append({
                    "acceptance_id": cp["acceptance_id"], "exit": result.returncode,
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "expected_failure": cp["expected_failure"],
                })
            return results
        finally:
            run(["git", "worktree", "remove", "--force", str(red_root)], timeout=120, check=False)
            run(["git", "worktree", "prune"], timeout=30, check=False)


def replay_green(proof: dict) -> list[dict]:
    results = []
    for cp in proof["checkpoints"]:
        cwd = (ROOT / cp["cwd"]).resolve()
        if ROOT not in (cwd, *cwd.parents) or not cwd.is_dir():
            die(f"{cp['acceptance_id']} GREEN cwd is unsafe")
        result = run(list(cp["argv"]), cwd=cwd, timeout=300, check=False)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode:
            die(f"{cp['acceptance_id']} independent GREEN replay failed")
        results.append({
            "acceptance_id": cp["acceptance_id"], "exit": result.returncode,
            "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        })
    return results


def verify_proof(proof: dict, head: str, contract: dict, contract_hash: str) -> dict:
    validate_proof_fields(proof, head, contract, contract_hash)
    if not ancestor(str(proof["test_commit"]), head):
        die("test-author commit is not an ancestor of current PR head")
    for rel, expected in proof["files"].items():
        path = ROOT / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            die(f"immutable acceptance test changed: {rel}")
    red = replay_red(proof)
    green = replay_green(proof)
    return {
        "test_commit": proof["test_commit"], "green_commit": head,
        "criteria": len(proof["checkpoints"]), "files": proof["files"],
        "test_plan_sha256": proof["test_plan_sha256"],
        "design_sha256": proof["design_sha256"],
        "red_replay": red, "green_replay": green,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--architecture-verdict", required=True)
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
    design, design_hash = extract(body, "design")
    proof, proof_hash = extract(body, "proof")
    issue = contract.get("issue", {}).get("number")
    if not isinstance(issue, int):
        die("contract issue identity missing")
    if not re.search(rf"(?i)\b(?:fixes|closes|resolves)\s+#{issue}\b", body):
        die("contract issue does not match PR linkage")
    contract_result = verify_contract(contract, contract_hash, issue)
    security_result = verify_security(args.pr)

    verdict = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
    if verdict.get("verdict") != "approve":
        die("evidence gate only authorizes an approve verdict")
    proof_result = verify_proof(proof, head, contract, contract_hash)
    if design_hash != proof_result["design_sha256"]:
        die("attached design hash does not match independently validated proof")
    policy = json.loads(run(["git", "show", "origin/main:.factory/architecture.json"]).stdout)
    architecture_guard_result = verify_architecture_guard(
        proof, design, design_hash, head, base, policy
    )
    architecture_result = verify_architecture(proof, head, base, contract_hash, policy)
    holdout = json.loads(Path(args.architecture_verdict).read_text(encoding="utf-8"))
    holdout_result = verify_architecture_holdout(holdout, architecture_result["changed_files"], policy)

    floors = json.loads(run(["git", "show", "origin/main:.factory/locks/floor.json"]).stdout)
    harness = run(
        [sys.executable, "harness/ci.py"], timeout=1800, check=False,
        credential_scope="validation",
    )
    transcript = (harness.stdout or "") + (harness.stderr or "")
    if harness.returncode:
        die("full harness failed: " + transcript[-2000:])
    observed = parse_harness(transcript, floors)

    second = gh_json(args.pr)
    if second["headRefOid"] != head:
        die("PR head changed while evidence was being assembled")

    bundle = {
        "version": "5.0", "pr": int(args.pr), "issue": issue,
        "base_sha": base, "head_sha": head,
        "contract_sha256": contract_hash, "contract": contract_result,
        "design_sha256": design_hash,
        "proof_sha256": proof_hash, "proof": proof_result,
        "architecture": architecture_result,
        "architecture_guard": architecture_guard_result,
        "architecture_holdout": holdout_result,
        "security": security_result,
        "validator_verdict_sha256": digest(verdict),
        "harness_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "observed": observed,
    }
    Path(args.output).write_bytes(canonical(bundle))
    print(f"EVIDENCE_OK head={head} bundle_sha256={digest(bundle)}")


if __name__ == "__main__":
    main()
