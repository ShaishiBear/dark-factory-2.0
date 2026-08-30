#!/usr/bin/env python3
"""One-time genesis authority for the pull request that replaces the factory trust root.

Every other gate here is executed by the factory's own machinery. That is sound for an ordinary
change, because the machinery predates the change and the change cannot reach it. It is *not*
sound for the pull request that rewrites the machinery: running the candidate's harness, holdouts
and mutation suite from the candidate's own tree proves self-consistency, not trustworthiness.

This verifier is the smallest thing that closes that gap. It is designed to be run by a human
from a clean external checkout -- their own reviewed copy of this file -- against an explicit
repository path and an explicit commit:

    python bootstrap_verify.py \\
        --repo /path/to/a/fresh/clone --commit <exact final SHA> \\
        --expect-verifier <sha256 of this file> --expect-manifest <sha256 of genesis.json> \\
        --evidence final-head-evidence.json --evidence-run <url or id> \\
        --approver "<who>" --reason "<why>" --output genesis-authorization.json

Properties that make it an authority rather than another component of what it judges:

* it imports nothing from ``factory_kernel``, ``scripts/factory_*`` or the rest of the trust root,
  so the machinery under certification cannot steer the verdict;
* stdlib and ``git`` only, and short enough to read end to end -- being auditable *is* the
  security property;
* every file is read from the object store at the exact commit, never from a working tree;
* it self-hashes and refuses unless the human's pinned value matches, so the candidate cannot
  edit its own certifier;
* the repository never asserts that it was approved. The manifest states only what the candidate
  can honestly state -- base, trust-root inventory, policy hashes, and the evidence it claims
  should be required. The approver, the reason, the moment and the identity of the validating run
  are supplied by the human at ceremony time, and this program *creates* the authorization
  artifact. There is no string in the tree saying "a human approved this" for a candidate to
  write for itself.

Scope. The authorization names exactly one commit, supplied by the human -- a manifest cannot
contain the hash of the commit that contains it, and requiring the human to name it is also what
stops the exception being carried silently to a later tree. The evidence must itself declare the
same commit, so validation of a parent commit can never be presented as evidence for the
manifest-bearing child.

Nothing in the merge path reads this program or its output. It is evidence for the human who
performs the one-time genesis merge, so an ordinary trust-root change can never route around the
deterministic guards by invoking this exception.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SELF = Path(__file__).resolve()
MANIFEST = ".factory/bootstrap/genesis.json"
SHA256 = 64


def fail(message: str) -> None:
    print(f"BOOTSTRAP_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180
    )
    if proc.returncode:
        fail(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(repo: Path, commit: str, path: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
        capture_output=True, timeout=180,
    )
    if proc.returncode:
        fail(f"trust-root file is missing at the authorized commit: {path}")
    return proc.stdout


def tracked(repo: Path, commit: str, prefixes: list[str]) -> set[str]:
    listing = git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
    return {p for p in listing if any(p == x or p.startswith(x) for x in prefixes)}


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {label}: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must be a JSON object")
    return value


def check_evidence(requirements: dict, evidence: dict, commit: str) -> None:
    """The candidate declares what must be proven; the human supplies what was observed."""
    require(
        str(evidence.get("candidate_sha") or "") == commit,
        "final validation evidence is for a different commit than the one being authorized",
    )
    markers = requirements.get("required_markers")
    observed_markers = evidence.get("markers")
    require(isinstance(markers, list) and markers, "genesis manifest requires no evidence markers")
    require(isinstance(observed_markers, list), "evidence lists no markers")
    absent = [m for m in markers if m not in observed_markers]
    require(not absent, "final validation evidence is missing required markers: " + ", ".join(absent))

    minimums = requirements.get("minimum")
    require(isinstance(minimums, dict) and minimums, "genesis manifest sets no evidence minimums")
    for key, floor in sorted(minimums.items()):
        actual = evidence.get(key)
        require(isinstance(floor, int) and floor > 0, f"required minimum {key} is invalid")
        require(isinstance(actual, int), f"evidence does not report {key}")
        require(actual >= floor, f"evidence {key}={actual} is below the required {floor}")

    families = requirements.get("mutation_families")
    require(isinstance(families, list) and families, "genesis manifest requires no mutation families")
    for family in families:
        block = evidence.get(family)
        require(isinstance(block, dict), f"evidence does not report {family}")
        total, caught = block.get("total"), block.get("caught")
        not_injected = block.get("not_injected")
        require(
            isinstance(total, int) and isinstance(caught, int) and isinstance(not_injected, int),
            f"evidence {family} counts are invalid",
        )
        require(total > 0, f"evidence {family} ran nothing")
        require(caught == total, f"evidence {family} let a mutation escape")
        require(not_injected == 0, f"evidence {family} failed to inject every mutation")


def verify(args: argparse.Namespace) -> dict:
    # 1. This verifier's identity, pinned by a human out of band.
    self_sha = digest(SELF.read_bytes())
    require(
        self_sha == args.expect_verifier,
        f"this verifier is not the one that was reviewed (actual {self_sha})",
    )

    repo = Path(args.repo).resolve()
    require(repo.is_dir(), f"repository path does not exist: {repo}")
    commit = git(repo, "rev-parse", "--verify", f"{args.commit}^{{commit}}").strip()
    require(
        commit == args.commit,
        f"the named commit does not resolve to itself in this repository (got {commit})",
    )
    tree = git(repo, "rev-parse", f"{commit}^{{tree}}").strip()

    # 2. The manifest's identity, likewise pinned out of band.
    raw = blob(repo, commit, MANIFEST)
    manifest_sha = digest(raw)
    require(
        manifest_sha == args.expect_manifest,
        f"genesis manifest is not the one that was reviewed (actual {manifest_sha})",
    )
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"genesis manifest is not valid JSON: {exc}")
    require(isinstance(manifest, dict), "genesis manifest must be an object")
    require(manifest.get("version") == "2.0", "genesis manifest version must be 2.0")
    require(
        str(manifest.get("verifier_sha256") or "") == self_sha,
        "genesis manifest does not name this verifier",
    )
    require(
        "authorization" not in manifest and "approved_by" not in manifest,
        "genesis manifest asserts its own authorization; the human act must create it",
    )

    base = str(manifest.get("base_sha") or "")
    require(len(base) == 40, "genesis manifest has no base commit")
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, commit], timeout=60
    )
    require(ancestor.returncode == 0, "authorized base is not an ancestor of the candidate")

    # 3. The trust root is complete. Omission is the attack this closes.
    prefixes = manifest.get("trust_root_prefixes")
    files = manifest.get("trust_root")
    require(
        isinstance(prefixes, list) and prefixes and all(isinstance(x, str) for x in prefixes),
        "genesis manifest has no trust-root prefixes",
    )
    require(isinstance(files, dict) and files, "genesis manifest has no trust-root file hashes")
    actual, listed = tracked(repo, commit, list(prefixes)), set(files)
    missing, extra = sorted(actual - listed), sorted(listed - actual)
    require(not missing, "trust-root files present but unlisted: " + ", ".join(missing[:8]))
    require(not extra, "trust-root files listed but absent: " + ", ".join(extra[:8]))
    for path in sorted(listed):
        recorded = str(files[path] or "")
        require(len(recorded) == SHA256, f"trust-root hash is malformed: {path}")
        require(digest(blob(repo, commit, path)) == recorded, f"trust-root file does not match: {path}")

    # 4. The policies that will govern every future PR, pinned at genesis.
    policies = manifest.get("policy_sha256")
    require(isinstance(policies, dict) and policies, "genesis manifest pins no policies")
    for path, recorded in sorted(policies.items()):
        require(path in listed, f"pinned policy is outside the trust root: {path}")
        require(digest(blob(repo, commit, path)) == str(recorded or ""), f"pinned policy changed: {path}")

    # 5. Evidence from a validation run of THIS commit. This verifier deliberately does not run
    #    the candidate's own suites -- that is the circularity it exists to break.
    requirements = manifest.get("evidence_requirements")
    require(isinstance(requirements, dict) and requirements, "genesis manifest states no evidence requirements")
    evidence_path = Path(args.evidence).resolve()
    evidence = read_json(evidence_path, "final validation evidence")
    check_evidence(requirements, evidence, commit)

    return {
        "version": "1.0",
        "verdict": "genesis-authorized",
        "scope": "one-time-genesis",
        "candidate_sha": commit,
        "candidate_tree": tree,
        "base_sha": base,
        "verifier_sha256": self_sha,
        "manifest_sha256": manifest_sha,
        "trust_root_files": len(listed),
        "policies": dict(sorted(policies.items())),
        "evidence_run": args.evidence_run,
        "evidence_sha256": digest(evidence_path.read_bytes()),
        "approver": args.approver,
        "reason": args.reason,
        "authorized_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
    }


def inventory(repo: Path, commit: str, prefixes: list[str]) -> dict:
    """Regenerate the trust-root inventory so a human can diff it against the manifest."""
    return {
        "candidate_sha": commit,
        "candidate_tree": git(repo, "rev-parse", f"{commit}^{{tree}}").strip(),
        "trust_root_prefixes": sorted(prefixes),
        "trust_root": {p: digest(blob(repo, commit, p)) for p in sorted(tracked(repo, commit, prefixes))},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--commit")
    parser.add_argument("--expect-verifier", dest="expect_verifier")
    parser.add_argument("--expect-manifest", dest="expect_manifest")
    parser.add_argument("--evidence")
    parser.add_argument("--evidence-run", dest="evidence_run")
    parser.add_argument("--approver")
    parser.add_argument("--reason")
    parser.add_argument("--output")
    parser.add_argument("--emit-inventory", nargs="+", metavar="PREFIX")
    args = parser.parse_args()

    if args.emit_inventory:
        repo = Path(args.repo).resolve()
        commit = git(repo, "rev-parse", args.commit or "HEAD").strip()
        print(json.dumps(inventory(repo, commit, list(args.emit_inventory)), indent=2, sort_keys=True))
        return

    supplied = (
        args.commit, args.expect_verifier, args.expect_manifest,
        args.evidence, args.evidence_run, args.approver, args.reason,
    )
    if not all(str(value or "").strip() for value in supplied):
        fail(
            "genesis authorization requires --commit, --expect-verifier, --expect-manifest, "
            "--evidence, --evidence-run, --approver and --reason; every one of them is supplied "
            "by a human at ceremony time, not by the repository"
        )
    result = verify(args)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(
        f"BOOTSTRAP_OK candidate={result['candidate_sha']} tree={result['candidate_tree']} "
        f"trust_root_files={result['trust_root_files']} approver={result['approver']!r}"
    )


if __name__ == "__main__":
    main()
