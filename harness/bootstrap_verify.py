#!/usr/bin/env python3
"""One-time genesis authority for the PR that replaces the factory trust root.

Every other gate in this repository is executed by the factory's own machinery. That is sound
for an ordinary change, because the machinery predates the change and the change cannot reach
it. It is *not* sound for the pull request that rewrites the machinery itself: running the
candidate's harness, holdouts and mutation suite from the candidate's own tree proves the
candidate is self-consistent, not that the new judge deserves authority.

This verifier is the smallest thing that can close that gap. It deliberately:

* imports nothing from ``factory_kernel``, ``scripts/factory_*`` or the rest of the trust root,
  so the machinery under certification cannot influence the verdict;
* uses only the standard library and ``git``, so it can be read end to end by a human in one
  sitting -- being auditable *is* the security property here;
* reads every file from the Git object store at the exact candidate commit, never from the
  working tree, so a dirty checkout cannot smuggle content past it;
* refuses to run unless a human supplies, out of band, three values: the expected SHA-256 of
  this file, the expected SHA-256 of the genesis manifest, and the exact commit being
  authorized. Those three are the trust anchor, and none of them lives in the repository. The
  candidate can rewrite either file, but doing so changes its hash and the human's pin then
  fails closed.

The commit identity is supplied rather than stored because a manifest cannot contain the hash of
the commit that contains it. Naming the commit is therefore part of the human's act, which is
also what stops the authorization being carried silently to a later tree.

The authorization it produces is scoped to exactly one commit. It is evidence for the human who
performs the one-time genesis merge; nothing in the merge path consumes it, so an ordinary
trust-root change can never route around the deterministic guards by claiming this exception.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SELF = Path(__file__).resolve()
ROOT = SELF.parent.parent
MANIFEST = ".factory/bootstrap/genesis.json"
SHA256 = 64


def fail(message: str) -> None:
    print(f"BOOTSTRAP_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    if proc.returncode:
        fail(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(commit: str, path: str) -> bytes:
    """Read a file from the object store, never from the working tree."""
    proc = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"],
        cwd=ROOT, capture_output=True, timeout=120,
    )
    if proc.returncode:
        fail(f"trust-root file is missing at the candidate commit: {path}")
    return proc.stdout


def tracked_files(commit: str, prefixes: list[str]) -> set[str]:
    listing = git("ls-tree", "-r", "--name-only", commit).splitlines()
    return {p for p in listing if any(p == x or p.startswith(x) for x in prefixes)}


def load_manifest(commit: str) -> tuple[dict, str]:
    raw = blob(commit, MANIFEST)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"genesis manifest is not valid JSON: {exc}")
    if not isinstance(value, dict):
        fail("genesis manifest must be an object")
    return value, digest(raw)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def verify(args: argparse.Namespace) -> dict:
    # 1. The verifier's own identity, pinned by a human out of band.
    self_sha = digest(SELF.read_bytes())
    require(
        self_sha == args.expect_verifier,
        "this verifier is not the one that was authorized "
        f"(actual {self_sha}, authorized {args.expect_verifier})",
    )

    head = git("rev-parse", "HEAD").strip()
    tree = git("rev-parse", "HEAD^{tree}").strip()

    # 2. Exactly one commit, named by the human. The exception cannot be carried to another tree
    #    without a fresh human act, because nothing in the repository asserts which commit is
    #    authorized.
    require(
        head == args.expect_candidate,
        f"HEAD is not the authorized candidate (actual {head})",
    )
    manifest, manifest_sha = load_manifest(head)

    # 3. The manifest's identity, likewise pinned out of band.
    require(
        manifest_sha == args.expect_manifest,
        f"genesis manifest is not the one that was authorized (actual {manifest_sha})",
    )
    require(manifest.get("version") == "1.0", "genesis manifest version must be 1.0")
    require(
        str(manifest.get("verifier_sha256") or "") == self_sha,
        "genesis manifest does not name this verifier",
    )
    base = str(manifest.get("base_sha") or "")
    require(len(base) == 40, "genesis manifest has no base commit")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, head], cwd=ROOT, timeout=60
    )
    require(ancestor.returncode == 0, "authorized base is not an ancestor of the candidate")

    # 4. The trust root is complete: every protected file is listed, and nothing protected is
    #    absent from the list. Omission is the attack this closes.
    prefixes = manifest.get("trust_root_prefixes")
    files = manifest.get("trust_root")
    require(
        isinstance(prefixes, list) and prefixes and all(isinstance(x, str) for x in prefixes),
        "genesis manifest has no trust-root prefixes",
    )
    require(isinstance(files, dict) and files, "genesis manifest has no trust-root file hashes")
    actual = tracked_files(head, list(prefixes))
    listed = set(files)
    missing, extra = sorted(actual - listed), sorted(listed - actual)
    require(not missing, "trust-root files present at the candidate but unlisted: " + ", ".join(missing[:8]))
    require(not extra, "trust-root files listed but absent from the candidate: " + ", ".join(extra[:8]))
    for path in sorted(listed):
        recorded = str(files[path] or "")
        require(len(recorded) == SHA256, f"trust-root hash is malformed: {path}")
        require(digest(blob(head, path)) == recorded, f"trust-root file does not match: {path}")

    # 5. The policies that will govern every future PR, pinned at genesis.
    policies = manifest.get("policy_sha256")
    require(isinstance(policies, dict) and policies, "genesis manifest pins no policies")
    for path, recorded in sorted(policies.items()):
        require(path in listed, f"pinned policy is outside the trust root: {path}")
        require(digest(blob(head, path)) == str(recorded or ""), f"pinned policy changed: {path}")

    # 6. The evidence a human weighed. This verifier does not re-run the candidate's own suites --
    #    that would be the very circularity it exists to break -- it records what was approved and
    #    refuses figures that do not even agree with themselves.
    observed = manifest.get("observed")
    require(isinstance(observed, dict) and observed, "genesis manifest records no observed evidence")
    for key in ("focused_tests", "unit_tests", "static_checks"):
        require(isinstance(observed.get(key), int) and observed[key] > 0, f"observed {key} is invalid")
    for family in ("factory_mutations", "application_mutations"):
        block = observed.get(family)
        require(isinstance(block, dict), f"observed {family} is missing")
        total, caught = block.get("total"), block.get("caught")
        not_injected = block.get("not_injected")
        require(
            isinstance(total, int) and isinstance(caught, int) and isinstance(not_injected, int),
            f"observed {family} counts are invalid",
        )
        require(total > 0, f"observed {family} ran nothing")
        require(caught == total, f"observed {family} let a mutation escape")
        require(not_injected == 0, f"observed {family} failed to inject every mutation")

    # 7. The one-time human authorization itself.
    auth = manifest.get("authorization")
    require(isinstance(auth, dict), "genesis manifest carries no authorization")
    require(auth.get("one_time") is True, "genesis authorization is not marked one-time")
    require(bool(str(auth.get("reason") or "").strip()), "genesis authorization states no reason")
    require(bool(str(auth.get("approved_by") or "").strip()), "genesis authorization names no approver")

    return {
        "version": "1.0",
        "verdict": "bootstrap-authorized",
        "candidate_sha": head,
        "candidate_tree": tree,
        "base_sha": base,
        "verifier_sha256": self_sha,
        "manifest_sha256": manifest_sha,
        "trust_root_files": len(listed),
        "policies": dict(sorted(policies.items())),
        "observed": observed,
        "authorization": auth,
    }


def emit_manifest(prefixes: list[str]) -> dict:
    """Regenerate the trust-root inventory so a human can diff it against the manifest."""
    head = git("rev-parse", "HEAD").strip()
    files = {path: digest(blob(head, path)) for path in sorted(tracked_files(head, prefixes))}
    return {
        "candidate_sha": head,
        "candidate_tree": git("rev-parse", "HEAD^{tree}").strip(),
        "trust_root_prefixes": sorted(prefixes),
        "trust_root": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-verifier", dest="expect_verifier")
    parser.add_argument("--expect-manifest", dest="expect_manifest")
    parser.add_argument("--expect-candidate", dest="expect_candidate")
    parser.add_argument("--output")
    parser.add_argument("--emit-inventory", nargs="+", metavar="PREFIX")
    args = parser.parse_args()

    if args.emit_inventory:
        print(json.dumps(emit_manifest(list(args.emit_inventory)), indent=2, sort_keys=True))
        return
    if not (args.expect_verifier and args.expect_manifest and args.expect_candidate):
        fail(
            "genesis verification requires --expect-verifier, --expect-manifest and "
            "--expect-candidate; all three are supplied by a human, not by the repository"
        )
    result = verify(args)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(
        f"BOOTSTRAP_OK candidate={result['candidate_sha']} tree={result['candidate_tree']} "
        f"trust_root_files={result['trust_root_files']} manifest={result['manifest_sha256']}"
    )


if __name__ == "__main__":
    main()
