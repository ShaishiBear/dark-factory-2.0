#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Code lives beside this file (HERE); the tree under test is the working directory (ROOT).
# The kernel runs every trust-root program from its own checkout of main with cwd set to the
# PR worktree, so a PR's copy of this program is never the authority that judges it (D-036).
# The kernel package is imported from beside this file; the caller need not export PYTHONPATH.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from factory_kernel.spine import load_policy  # noqa: E402

ROOT = Path.cwd().resolve()
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def die(message: str) -> None:
    print(f"MERGE_VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )
    if check and proc.returncode:
        die(f"{' '.join(argv)} failed: {((proc.stdout or '') + (proc.stderr or ''))[-1200:]}")
    return proc


def load_json(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read JSON evidence: {exc}")
    if not isinstance(value, dict):
        die("JSON evidence must be an object")
    return value


def require_oid(value: object, name: str) -> str:
    text = str(value or "")
    if not GIT_OID.fullmatch(text):
        die(f"{name} is not a valid git object id")
    return text


def verify_spine(bundle: dict, base: str, head: str) -> None:
    spine = bundle.get("spine")
    if not isinstance(spine, dict) or spine.get("version") != "1.0":
        die("Evidence Bundle lacks canonical evidence spine")
    if spine.get("completion_level") != 100:
        die("evidence spine has not reached 100 percent completion")
    if spine.get("base_sha") != base or spine.get("head_sha") != head:
        die("evidence spine is not bound to bundle base/head")

    try:
        policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
    except ValueError as exc:
        die(f"cannot load protected evidence-spine policy: {exc}")
    if spine.get("policy_sha256") != policy.sha256():
        die("evidence spine was compiled against a different protected policy")

    claims = spine.get("claims")
    expected = [requirement.claim_id for requirement in policy.requirements]
    if not isinstance(claims, list) or [row.get("claim_id") for row in claims if isinstance(row, dict)] != expected:
        die("evidence spine does not contain the exact required claim sequence")
    if any(not isinstance(row, dict) or row.get("completion_level") != 100 for row in claims):
        die("one or more evidence-spine claims are below 100 percent")

    # A completion number is the closure's own summary of its work. Merge authority re-derives the
    # independence requirement from the protected policy and checks the certificate hashes itself,
    # so a weakened closure cannot buy merge with a 100 it did not earn.
    rows = {row["claim_id"]: row for row in claims}
    for requirement in policy.requirements:
        row = rows[requirement.claim_id]
        deterministic = str(row.get("deterministic_sha256") or "")
        independent = str(row.get("independent_sha256") or "")
        if requirement.deterministic_required and not SHA256.fullmatch(deterministic):
            die(f"spine claim {requirement.claim_id} has no deterministic certification hash")
        if requirement.independent_required:
            if not SHA256.fullmatch(independent):
                die(f"spine claim {requirement.claim_id} has no independent certification hash")
            if independent == deterministic:
                die(
                    f"spine claim {requirement.claim_id} reuses its deterministic certification "
                    "as independent evidence"
                )
            if independent == str(row.get("artifact_sha256") or ""):
                die(f"spine claim {requirement.claim_id} independently certifies itself")
        if requirement.exact_head_required and row.get("exact_head_sha") != head:
            die(f"spine claim {requirement.claim_id} is not bound to the exact candidate head")

    manifest = str(spine.get("manifest_sha256") or "")
    provenance = str(spine.get("builder_provenance_sha256") or "")
    if not SHA256.fullmatch(manifest) or bundle.get("run_manifest_sha256") != manifest:
        die("Evidence Bundle manifest hash does not match the completed spine")
    if not SHA256.fullmatch(provenance) or bundle.get("builder_provenance_sha256") != provenance:
        die("Evidence Bundle builder provenance hash does not match the completed spine")


def bundle_fields(bundle: dict) -> tuple[str, str]:
    if bundle.get("version") != "5.0":
        die("merged-SHA verifier requires Evidence Bundle v5")
    base = require_oid(bundle.get("base_sha"), "bundle base_sha")
    head = require_oid(bundle.get("head_sha"), "bundle head_sha")
    verify_spine(bundle, base, head)
    return base, head


def expected_authorization(bundle: dict, evidence_sha256: str, head_tree: str) -> dict:
    base, head = bundle_fields(bundle)
    if not SHA256.fullmatch(evidence_sha256):
        die("evidence bundle byte hash is invalid")
    return {
        "version": "1.0",
        "evidence_sha256": evidence_sha256,
        "base_ref": "main",
        "base_sha": base,
        "head_sha": head,
        "head_tree_sha": require_oid(head_tree, "head tree"),
    }


def pre_authorization(bundle: dict, *, evidence_sha256: str, pr_meta: dict,
                      local_head: str, current_base: str, head_tree: str,
                      base_is_ancestor: bool) -> dict:
    base, head = bundle_fields(bundle)
    if pr_meta.get("baseRefName") != "main":
        die("factory auto-merge is only authorized onto main")
    if pr_meta.get("baseRefOid") != base or current_base != base:
        die("main moved after evidence; rebase/revalidate before merge")
    if pr_meta.get("headRefOid") != head or local_head != head:
        die("PR head moved after evidence")
    if not base_is_ancestor:
        die("evidenced base is not an ancestor of evidenced head")
    return expected_authorization(bundle, evidence_sha256, head_tree)


def post_result(bundle: dict, authorization: dict, *, evidence_sha256: str,
                pr_meta: dict, merge_sha: str, merge_parents: list[str],
                merge_tree: str, merge_is_ancestor: bool) -> dict:
    base, head = bundle_fields(bundle)
    auth_tree = require_oid(authorization.get("head_tree_sha"), "authorized head tree")
    if authorization != expected_authorization(bundle, evidence_sha256, auth_tree):
        die("merge authorization does not exactly match current Evidence Bundle")
    if pr_meta.get("baseRefName") != "main" or pr_meta.get("headRefOid") != head:
        die("PR identity changed between authorization and merged-SHA verification")
    if not pr_meta.get("mergedAt") or pr_meta.get("mergeCommit", {}).get("oid") != merge_sha:
        die("GitHub does not report the expected merged commit")
    if not merge_is_ancestor:
        die("merged commit is not contained in current origin/main history")
    if len(merge_parents) != 1 or merge_parents[0] != base:
        die("squash merge parent is not the evidenced base SHA")
    if merge_tree != auth_tree:
        die("merged commit tree is not byte-identical to the authorized PR head tree")
    return {
        "version": "1.0", "verdict": "verified",
        "merge_sha": merge_sha, "base_sha": base,
        "head_sha": head, "tree_sha": merge_tree,
        "evidence_sha256": evidence_sha256,
    }


def gh_meta(pr: str) -> dict:
    fields = "baseRefName,baseRefOid,headRefOid,mergedAt,mergeCommit"
    return json.loads(run(["gh", "pr", "view", pr, "--json", fields], timeout=30).stdout)


def git_oid(rev: str) -> str:
    return require_oid(run(["git", "rev-parse", rev], timeout=30).stdout.strip(), rev)


def pre(args: argparse.Namespace) -> None:
    evidence_path = Path(args.evidence)
    bundle = load_json(args.evidence)
    run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    meta = gh_meta(args.pr)
    base, head = bundle_fields(bundle)
    auth = pre_authorization(
        bundle,
        evidence_sha256=file_sha(evidence_path),
        pr_meta=meta,
        local_head=git_oid("HEAD"),
        current_base=git_oid("origin/main"),
        head_tree=git_oid(f"{head}^{{tree}}"),
        base_is_ancestor=run(["git", "merge-base", "--is-ancestor", base, head], check=False).returncode == 0,
    )
    Path(args.output).write_bytes(canonical(auth))
    print(f"MERGE_AUTHORIZED base={auth['base_sha']} head={auth['head_sha']} tree={auth['head_tree_sha']}")


def post(args: argparse.Namespace) -> None:
    evidence_path = Path(args.evidence)
    bundle = load_json(args.evidence)
    authorization = load_json(args.authorization)
    meta = gh_meta(args.pr)
    merge_sha = require_oid(meta.get("mergeCommit", {}).get("oid"), "GitHub merge commit")
    run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    parent_line = run(["git", "rev-list", "--parents", "-n", "1", merge_sha], timeout=30).stdout.split()
    if not parent_line or parent_line[0] != merge_sha:
        die("cannot read merged commit parents")
    result = post_result(
        bundle, authorization,
        evidence_sha256=file_sha(evidence_path),
        pr_meta=meta,
        merge_sha=merge_sha,
        merge_parents=parent_line[1:],
        merge_tree=git_oid(f"{merge_sha}^{{tree}}"),
        merge_is_ancestor=run(
            ["git", "merge-base", "--is-ancestor", merge_sha, "origin/main"], check=False, timeout=30
        ).returncode == 0,
    )
    Path(args.output).write_bytes(canonical(result))
    print(f"MERGED_SHA_VERIFIED merge={merge_sha} tree={result['tree_sha']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("pre", "post"):
        p = sub.add_parser(phase)
        p.add_argument("--pr", required=True)
        p.add_argument("--evidence", required=True)
        p.add_argument("--output", required=True)
        if phase == "post":
            p.add_argument("--authorization", required=True)
    args = parser.parse_args()
    pre(args) if args.phase == "pre" else post(args)


if __name__ == "__main__":
    main()
