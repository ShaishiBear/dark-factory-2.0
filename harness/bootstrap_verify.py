#!/usr/bin/env python3
"""One-time genesis authority for the pull request that replaces the factory trust root.

Every other gate here is executed by the factory's own machinery. That is sound for an ordinary
change, because the machinery predates the change and the change cannot reach it. It is *not*
sound for the pull request that rewrites the machinery: running the candidate's harness, holdouts
and mutation suite from the candidate's own tree proves self-consistency, not trustworthiness.

Four documents, and only one of them is written by the candidate:

    EXTERNAL GENESIS POLICY   defines what must be proven          (human-held, not in the repo)
    CANDIDATE MANIFEST        describes the exact tree and its inventory
    FINAL-HEAD EVIDENCE       describes what a real run observed
    THIS VERIFIER             proves the relations between them    (human-held, hash-pinned)

The separation matters because a candidate that authors its own standard can satisfy an
artificially weak one honestly. The manifest may therefore say what the tree *is* -- base,
trust-root inventory, policy hashes -- but never what would be *good enough*. Minimums, required
markers, mutation families, trust-root scope and pinned-policy scope all come from the external
policy, and the candidate can only widen them, never narrow them.

Evidence content is likewise separated from evidence provenance. A JSON file asserting "81/81" is
not proof that anything ran 81 mutations. Counts are therefore parsed out of the raw validation
log, whose digest the evidence document commits to and which the authorization records, so the
numbers are recomputed rather than believed.

Properties that make this an authority rather than another component of what it judges:

* it imports nothing from the trust root it certifies, and uses only the standard library and
  ``git``;
* it is short enough to read end to end -- being auditable *is* the security property;
* every repository file is read from the object store at the exact commit, never a working tree;
* it self-hashes and refuses unless the human's pinned value matches;
* the repository never asserts that it was approved. The approver, the reason, the moment, the
  policy and the validating run are supplied at ceremony time, and this program *creates* the
  authorization artifact.

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
import re
import subprocess
import sys

SELF = Path(__file__).resolve()
MANIFEST = ".factory/bootstrap/genesis.json"
SHA256 = 64

# Count extraction lives here, in the human-reviewed external verifier, so neither the candidate
# nor the policy can redefine how an observation is read out of a log.
COUNTERS = {
    "focused_tests": r"FOCUSED_OK tests=(\d+)",
    "unit_tests": r"UNIT_PASSED tests=(\d+)",
    "static_checks": r"STATIC_OK checks=(\d+)",
}
# The manifest describes the tree. Anything that would let it define its own standard is refused.
FORBIDDEN_MANIFEST_KEYS = ("evidence_requirements", "authorization", "approved_by", "minimum")


def fail(message: str) -> None:
    print(f"BOOTSTRAP_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


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


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {label}: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must be a JSON object")
    return value


def string_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    ok = isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)
    require(ok and (allow_empty or value), f"genesis policy {label} is missing or invalid")
    return list(value)  # type: ignore[arg-type]


def check_policy_shape(policy: dict) -> None:
    require(policy.get("version") == "1.0", "genesis policy version must be 1.0")
    for key in ("repository", "base_sha"):
        require(bool(str(policy.get(key) or "").strip()), f"genesis policy states no {key}")
    string_list(policy.get("required_trust_root_prefixes"), "required_trust_root_prefixes")
    string_list(policy.get("required_trust_root_paths"), "required_trust_root_paths")
    string_list(policy.get("required_policy_files"), "required_policy_files")
    string_list(policy.get("required_markers"), "required_markers")
    string_list(policy.get("required_mutation_families"), "required_mutation_families")
    string_list(policy.get("required_external_evidence"), "required_external_evidence", allow_empty=True)
    holdouts = policy.get("required_holdout_classes")
    require(
        isinstance(holdouts, dict) and holdouts
        and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in holdouts.items()),
        "genesis policy required_holdout_classes is missing or invalid",
    )
    minimums = policy.get("minimum")
    require(isinstance(minimums, dict) and minimums, "genesis policy sets no minimums")
    unknown = sorted(set(minimums) - set(COUNTERS))
    require(not unknown, "genesis policy names unmeasurable minimums: " + ", ".join(unknown))
    for key, floor in sorted(minimums.items()):
        require(isinstance(floor, int) and floor > 0, f"genesis policy minimum {key} is invalid")
    families = policy.get("mutation_family_markers")
    require(
        isinstance(families, dict)
        and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in families.items()),
        "genesis policy mutation_family_markers is missing or invalid",
    )
    absent = [f for f in policy["required_mutation_families"] if f not in families]
    require(not absent, "genesis policy requires unmeasurable mutation families: " + ", ".join(absent))


def check_manifest_covers_policy(manifest: dict, policy: dict) -> tuple[list[str], dict, set[str]]:
    """The candidate may widen the genesis scope but never narrow it."""
    for key in FORBIDDEN_MANIFEST_KEYS:
        require(
            key not in manifest,
            f"genesis manifest carries {key!r}: the candidate must not define the standard "
            "it is judged by, nor assert its own authorization",
        )
    prefixes = string_list(manifest.get("trust_root_prefixes"), "manifest trust_root_prefixes")
    files = manifest.get("trust_root")
    require(isinstance(files, dict) and files, "genesis manifest has no trust-root file hashes")
    policies = manifest.get("policy_sha256")
    require(isinstance(policies, dict) and policies, "genesis manifest pins no policies")

    narrowed = [p for p in policy["required_trust_root_prefixes"] if p not in prefixes]
    require(
        not narrowed,
        "genesis manifest narrows the trust-root scope the policy requires: " + ", ".join(narrowed),
    )
    absent_paths = [p for p in policy["required_trust_root_paths"] if p not in files]
    require(
        not absent_paths,
        "genesis manifest omits mandatory trust-root paths: " + ", ".join(absent_paths),
    )
    absent_policies = [p for p in policy["required_policy_files"] if p not in policies]
    require(
        not absent_policies,
        "genesis manifest omits mandatory pinned policies: " + ", ".join(absent_policies),
    )

    # Self-reference trap. If the one-time manifest fell inside a hashed prefix it would have to
    # contain its own hash, and no fixed point exists. It is excluded on principle rather than by
    # accident: the manifest governs nothing after genesis, so it is not part of the persistent
    # trust root that future PRs are judged against.
    covered = [p for p in prefixes if MANIFEST == p or MANIFEST.startswith(p)]
    require(
        not covered,
        f"genesis manifest {MANIFEST} falls inside its own hashed prefixes ({', '.join(covered)}); "
        "the inventory would have to contain its own hash and no fixed point exists",
    )
    require(
        MANIFEST not in files,
        f"genesis manifest lists itself in the trust-root inventory: {MANIFEST}",
    )
    return prefixes, policies, set(files)


def count(log: str, pattern: str, label: str) -> int:
    match = re.search(pattern, log)
    require(match is not None, f"validation log does not report {label}")
    return int(match.group(1))  # type: ignore[union-attr]


def check_evidence(policy: dict, evidence: dict, log: str, commit: str, run: str) -> dict:
    """Observations are read out of the run's own log, not taken from the evidence document."""
    require(
        str(evidence.get("candidate_sha") or "") == commit,
        "final validation evidence is for a different commit than the one being authorized",
    )
    require(
        str(evidence.get("repository") or "") == policy["repository"],
        "final validation evidence is from a different repository than the policy names",
    )
    require(
        str(evidence.get("run_url") or "") == run,
        "final validation evidence does not name the run identity supplied at ceremony time",
    )
    require(
        str(evidence.get("conclusion") or "") == "success",
        "final validation run did not conclude successfully",
    )
    if policy.get("require_exact_final_head", True):
        require(
            re.search(rf"EXACT_HEAD_OK {re.escape(commit)}\b", log) is not None,
            "validation log does not prove the run executed against the authorized head",
        )

    markers = list(policy["required_markers"])
    markers += [policy["required_holdout_classes"][name] for name in sorted(policy["required_holdout_classes"])]
    markers += list(policy["required_external_evidence"])
    absent = [m for m in markers if m not in log]
    require(not absent, "validation log is missing required markers: " + ", ".join(sorted(set(absent))))

    observed: dict[str, object] = {}
    for key, floor in sorted(policy["minimum"].items()):
        actual = count(log, COUNTERS[key], key)
        require(actual >= floor, f"observed {key}={actual} is below the required {floor}")
        observed[key] = actual

    for family in policy["required_mutation_families"]:
        prefix = policy["mutation_family_markers"][family]
        guard = rf"(?<![A-Z_]){re.escape(prefix)}"
        total = count(log, guard + r"_TOTAL=(\d+)", f"{family} total")
        caught = count(log, guard + r"_CAUGHT=(\d+)", f"{family} caught")
        not_injected = count(log, guard + r"_NOT_INJECTED=(\d+)", f"{family} not-injected")
        require(total > 0, f"observed {family} ran nothing")
        require(caught == total, f"observed {family} let a mutation escape")
        require(not_injected == 0, f"observed {family} failed to inject every mutation")
        observed[family] = {"total": total, "caught": caught, "not_injected": not_injected}
    return observed


def verify(args: argparse.Namespace) -> dict:
    # 1. This verifier and the external policy: both pinned by a human, neither from the candidate.
    self_sha = digest(SELF.read_bytes())
    require(
        self_sha == args.expect_verifier,
        f"this verifier is not the one that was reviewed (actual {self_sha})",
    )
    policy_path = Path(args.policy).resolve()
    require(policy_path.is_file(), f"genesis policy file does not exist: {policy_path}")
    policy_sha = digest(policy_path.read_bytes())
    require(
        policy_sha == args.expect_policy,
        f"genesis policy is not the one that was reviewed (actual {policy_sha})",
    )
    repo = Path(args.repo).resolve()
    require(
        policy_path.is_absolute() and repo not in policy_path.parents,
        "genesis policy must be supplied from outside the repository under test",
    )
    policy = read_json(policy_path, "genesis policy")
    check_policy_shape(policy)

    require(repo.is_dir(), f"repository path does not exist: {repo}")
    commit = git(repo, "rev-parse", "--verify", f"{args.commit}^{{commit}}").strip()
    require(commit == args.commit, f"the named commit does not resolve to itself (got {commit})")
    tree = git(repo, "rev-parse", f"{commit}^{{tree}}").strip()

    # 2. The manifest, pinned out of band, describing this exact tree.
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
    require(manifest.get("version") == "3.0", "genesis manifest version must be 3.0")
    require(
        str(manifest.get("verifier_sha256") or "") == self_sha,
        "genesis manifest does not name this verifier",
    )
    require(
        str(manifest.get("base_sha") or "") == policy["base_sha"],
        "genesis manifest names a different base than the policy authorizes",
    )
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", policy["base_sha"], commit],
        timeout=60,
    )
    require(ancestor.returncode == 0, "authorized base is not an ancestor of the candidate")

    # 3. Manifest covers everything the external policy demands, and cannot narrow it.
    prefixes, policies, listed = check_manifest_covers_policy(manifest, policy)

    # 4. The inventory is complete and exact at this commit. Omission is the attack this closes.
    actual = tracked(repo, commit, prefixes)
    missing, extra = sorted(actual - listed), sorted(listed - actual)
    require(not missing, "trust-root files present but unlisted: " + ", ".join(missing[:8]))
    require(not extra, "trust-root files listed but absent: " + ", ".join(extra[:8]))
    files = manifest["trust_root"]
    for path in sorted(listed):
        recorded = str(files[path] or "")
        require(len(recorded) == SHA256, f"trust-root hash is malformed: {path}")
        require(digest(blob(repo, commit, path)) == recorded, f"trust-root file does not match: {path}")
    for path, recorded in sorted(policies.items()):
        require(path in listed, f"pinned policy is outside the trust root: {path}")
        require(digest(blob(repo, commit, path)) == str(recorded or ""), f"pinned policy changed: {path}")

    # 5. Evidence for this exact commit, measured from the run's own log.
    evidence_path = Path(args.evidence).resolve()
    evidence = read_json(evidence_path, "final validation evidence")
    log_path = Path(args.evidence_log).resolve()
    require(log_path.is_file(), f"validation log does not exist: {log_path}")
    log_bytes = log_path.read_bytes()
    log_sha = digest(log_bytes)
    require(
        str(evidence.get("log_sha256") or "") == log_sha,
        f"validation log does not match the digest the evidence commits to (actual {log_sha})",
    )
    observed = check_evidence(
        policy, evidence, log_bytes.decode("utf-8", "replace"), commit, args.evidence_run
    )

    return {
        "version": "1.0",
        "verdict": "genesis-authorized",
        "scope": "one-time-genesis",
        "repository": policy["repository"],
        "candidate_sha": commit,
        "candidate_tree": tree,
        "base_sha": policy["base_sha"],
        "genesis_policy_sha256": policy_sha,
        "verifier_sha256": self_sha,
        "manifest_sha256": manifest_sha,
        "trust_root_files": len(listed),
        "pinned_policies": dict(sorted(policies.items())),
        "evidence_run": args.evidence_run,
        "evidence_sha256": digest(evidence_path.read_bytes()),
        "evidence_log_sha256": log_sha,
        "observed": observed,
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
    parser.add_argument("--policy")
    parser.add_argument("--expect-policy", dest="expect_policy")
    parser.add_argument("--expect-verifier", dest="expect_verifier")
    parser.add_argument("--expect-manifest", dest="expect_manifest")
    parser.add_argument("--evidence")
    parser.add_argument("--evidence-log", dest="evidence_log")
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
        args.commit, args.policy, args.expect_policy, args.expect_verifier, args.expect_manifest,
        args.evidence, args.evidence_log, args.evidence_run, args.approver, args.reason,
    )
    if not all(str(value or "").strip() for value in supplied):
        fail(
            "genesis authorization requires --commit, --policy, --expect-policy, "
            "--expect-verifier, --expect-manifest, --evidence, --evidence-log, --evidence-run, "
            "--approver and --reason; every one of them is supplied by a human at ceremony time, "
            "not by the repository"
        )
    result = verify(args)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(
        f"BOOTSTRAP_OK candidate={result['candidate_sha']} tree={result['candidate_tree']} "
        f"policy={result['genesis_policy_sha256'][:12]} trust_root_files={result['trust_root_files']} "
        f"approver={result['approver']!r}"
    )


if __name__ == "__main__":
    main()
