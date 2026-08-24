#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("FACTORY_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
HEX64 = __import__("re").compile(r"^[0-9a-f]{64}$")


def die(message: str) -> None:
    print(f"MERGE_VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


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


def require_sha(value: object, name: str) -> str:
    text = str(value or "")
    if not HEX64.fullmatch(text):
        die(f"{name} is not a SHA-256-shaped git oid")
    return text


def bundle_fields(bundle: dict) -> tuple[str, str, str, str]:
    if bundle.get("version") != "6.0":
        die("merged-SHA verifier requires Evidence Bundle v6")
    base_ref = bundle.get("base_ref")
    if base_ref != "main":
        die("factory auto-merge is only authorized onto main")
    base = require_sha(bundle.get("base_sha"), "bundle base_sha")
    head = require_sha(bundle.get("head_sha"), "bundle head_sha")
    tree = require_sha(bundle.get("head_tree_sha"), "bundle head_tree_sha")
    return base_ref, base, head, tree


def pre_authorization(bundle: dict, *, evidence_sha256: str, pr_meta: dict,
                      local_head: str, current_base: str, head_tree: str,
                      base_is_ancestor: bool) -> dict:
    base_ref, base, head, expected_tree = bundle_fields(bundle)
    if pr_meta.get("baseRefName") != base_ref:
        die("PR base branch changed after evidence")
    if pr_meta.get("baseRefOid") != base or current_base != base:
        die("main moved after evidence; rebase/revalidate before merge")
    if pr_meta.get("headRefOid") != head or local_head != head:
        die("PR head moved after evidence")
    if head_tree != expected_tree:
        die("current PR head tree disagrees with Evidence Bundle")
    if not base_is_ancestor:
        die("evidenced base is not an ancestor of evidenced head")
    if not HEX64.fullmatch(evidence_sha256):
        die("evidence bundle byte hash is invalid")
    return {
        "version": "1.0",
        "evidence_sha256": evidence_sha256,
        "base_ref": base_ref,
        "base_sha": base,
        "head_sha": head,
        "head_tree_sha": expected_tree,
    }


def post_result(bundle: dict, authorization: dict, *, evidence_sha256: str,
                pr_meta: dict, current_base: str, merge_sha: str,
                merge_parents: list[str], merge_tree: str) -> dict:
    base_ref, base, head, expected_tree = bundle_fields(bundle)
    expected_auth = {
        "version": "1.0", "evidence_sha256": evidence_sha256,
        "base_ref": base_ref, "base_sha": base,
        "head_sha": head, "head_tree_sha": expected_tree,
    }
    if authorization != expected_auth:
        die("merge authorization does not exactly match current Evidence Bundle")
    if pr_meta.get("baseRefName") != base_ref or pr_meta.get("headRefOid") != head:
        die("PR identity changed between authorization and merged-SHA verification")
    if not pr_meta.get("mergedAt") or pr_meta.get("mergeCommit", {}).get("oid") != merge_sha:
        die("GitHub does not report the expected merged commit")
    if current_base != merge_sha:
        die("merged commit is not the current origin/main tip")
    if len(merge_parents) != 1 or merge_parents[0] != base:
        die("squash merge parent is not the evidenced base SHA")
    if merge_tree != expected_tree:
        die("merged commit tree is not byte-identical to the evidenced PR head tree")
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
    return run(["git", "rev-parse", rev], timeout=30).stdout.strip()


def pre(args: argparse.Namespace) -> None:
    evidence_path = Path(args.evidence)
    bundle = load_json(args.evidence)
    run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    meta = gh_meta(args.pr)
    base = str(bundle.get("base_sha") or "")
    head = str(bundle.get("head_sha") or "")
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
    merge_sha = str(meta.get("mergeCommit", {}).get("oid") or "")
    require_sha(merge_sha, "GitHub merge commit")
    run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    parent_line = run(["git", "rev-list", "--parents", "-n", "1", merge_sha], timeout=30).stdout.split()
    if not parent_line or parent_line[0] != merge_sha:
        die("cannot read merged commit parents")
    result = post_result(
        bundle, authorization,
        evidence_sha256=file_sha(evidence_path),
        pr_meta=meta,
        current_base=git_oid("origin/main"),
        merge_sha=merge_sha,
        merge_parents=parent_line[1:],
        merge_tree=git_oid(f"{merge_sha}^{{tree}}"),
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
