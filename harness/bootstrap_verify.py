#!/usr/bin/env python3
"""One-time genesis authority for the pull request that replaces the factory trust root.

Every other gate here is executed by the factory's own machinery. That is sound for an ordinary
change, because the machinery predates the change and the change cannot reach it. It is *not*
sound for the pull request that rewrites the machinery: running the candidate's harness, holdouts
and mutation suite from the candidate's own tree proves self-consistency, not trustworthiness.

Four documents, and only one of them is written by the candidate:

    EXTERNAL GENESIS POLICY   defines what must be proven          (human-held, not in the repo)
                              and pins the workflow, driver, recipe and aggregator identities
    CANDIDATE MANIFEST        describes the exact tree and its inventory
    FINAL-HEAD EVIDENCE       describes what a real run observed
    THIS VERIFIER             proves the relations between them    (human-held, hash-pinned)

The separation matters because a candidate that authors its own standard can satisfy an
artificially weak one honestly. The manifest may therefore say what the tree *is* -- base,
trust-root inventory, policy hashes -- but never what would be *good enough*. Minimums, required
markers, mutation families, trust-root scope and pinned-policy scope all come from the external
policy, and the candidate can only widen them, never narrow them.

Evidence content is likewise separated from evidence provenance, and measurement from parsing.
This program once read its counts out of the aggregated validation log with an unconstrained
regex search. Candidate-controlled programs write to that same log, so one line printed early
decided what the authority believed: a run with six escaped mutations could be recorded as "1/1"
and satisfy the caught-equals-total invariant. Searching a shared stream is not measurement.

Measurements now come from a structured result produced by an external validation driver, which
executes the pinned recipe, judges each stage by its exit status, and reads each count from that
stage's own output while refusing ambiguity. This program never parses candidate output. It binds
the driver and recipe digests the policy pins, the run identity the human names, and the raw log
digest -- the last so a human can independently confirm against GitHub that the run exists,
concluded successfully and executed this commit. It deliberately makes no network calls; a JSON
file plus a text file is not provenance, and pretending otherwise would be worse than saying so.

Properties that make this an authority rather than another component of what it judges:

* it imports nothing from the trust root it certifies, and uses only the standard library and
  ``git``;
* it is short enough to read end to end -- being auditable *is* the security property;
* every repository file is read from the object store at the exact commit, never a working tree,
  including the validation driver, aggregator and recipe whose digests the policy pins -- those
  are recomputed here rather than believed from the result document they produced;
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
import subprocess
import sys

SELF = Path(__file__).resolve()
MANIFEST = ".factory/bootstrap/genesis.json"
SHA256 = 64

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
    string_list(policy.get("required_mutation_families"), "required_mutation_families")
    # A holdout class and an external requirement are each satisfied by a named stage that ran and
    # exited zero, not by a string appearing somewhere in a shared log.
    for key, allow_empty in (("required_holdout_classes", False), ("required_external_evidence", True)):
        block = policy.get(key)
        require(
            isinstance(block, dict) and (block or allow_empty)
            and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in block.items()),
            f"genesis policy {key} is missing or invalid",
        )
    minimums = policy.get("minimum")
    require(isinstance(minimums, dict) and minimums, "genesis policy sets no minimums")
    for key, floor in sorted(minimums.items()):
        require(isinstance(floor, int) and floor > 0, f"genesis policy minimum {key} is invalid")
    string_list(policy.get("required_stages"), "required_stages")
    for key in (
        "validation_driver_sha256", "validation_recipe_sha256",
        "validation_aggregator_sha256", "validation_workflow_sha256",
    ):
        value = str(policy.get(key) or "")
        require(len(value) == SHA256, f"genesis policy does not pin {key}")
    workflow_commit = str(policy.get("validation_workflow_commit_sha") or "")
    require(len(workflow_commit) == 40, "genesis policy does not pin validation_workflow_commit_sha")


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


def check_result(policy: dict, result: dict, commit: str) -> dict:
    """Read measurements from the pinned driver's structured result, never from candidate output."""
    require(result.get("version") == "1.0", "validation result version must be 1.0")
    require(
        str(result.get("driver_sha256") or "") == policy["validation_driver_sha256"],
        "validation result was not produced by the driver the policy pins",
    )
    require(
        str(result.get("recipe_sha256") or "") == policy["validation_recipe_sha256"],
        "validation result did not execute the recipe the policy pins",
    )
    require(
        str(result.get("aggregator_sha256") or "") == policy["validation_aggregator_sha256"],
        "validation result was not assembled by the aggregator the policy pins",
    )
    # Isolation is a property of how the stages were executed, so the assembled result has to
    # state which model produced it rather than leaving it to be assumed.
    require(
        result.get("stage_isolation") == "one-disposable-runner-per-stage",
        "validation result was not produced by one disposable runner per stage",
    )
    # A probe demonstrated that a stage can rewrite its own uploaded result after the driver
    # writes it, so evidence assembled from stage-written files is refused outright: it must be
    # built from GitHub's own job record, on a runner no candidate process outlived.
    require(
        result.get("evidence_source") == "github-actions-job-record",
        "validation result was not assembled from GitHub's own execution record",
    )
    require(
        str(result.get("workflow_commit_sha") or "") == policy["validation_workflow_commit_sha"],
        "validation result was assembled from a run of a different workflow commit",
    )
    require(
        str(result.get("candidate_sha") or "") == commit,
        "validation result is for a different commit than the one being authorized",
    )
    require(result.get("verdict") == "pass", "validation result did not pass")

    stages = result.get("stages")
    require(isinstance(stages, list) and stages, "validation result records no stages")
    names = [str(s.get("name") or "") for s in stages if isinstance(s, dict)]
    require(len(names) == len(stages) and all(names), "validation result has unnamed stages")
    require(len(set(names)) == len(names), "validation result repeats a stage name")
    mandatory = list(policy["required_stages"])
    mandatory += [policy["required_holdout_classes"][k] for k in sorted(policy["required_holdout_classes"])]
    mandatory += [policy["required_external_evidence"][k] for k in sorted(policy["required_external_evidence"])]
    absent = sorted({s for s in mandatory if s not in names})
    require(not absent, "validation result omits mandatory stages: " + ", ".join(absent))

    measurements: dict[str, int] = {}
    for stage in stages:
        require(stage.get("exit") == 0, f"validation stage {stage.get('name')!r} did not succeed")
        for key, value in (stage.get("measurements") or {}).items():
            require(isinstance(value, int), f"measurement {key} is not an integer")
            require(key not in measurements, f"measurement {key} is reported by more than one stage")
            measurements[key] = value

    for key, floor in sorted(policy["minimum"].items()):
        require(key in measurements, f"validation result does not measure {key}")
        require(
            measurements[key] >= floor,
            f"observed {key}={measurements[key]} is below the required {floor}",
        )
    for family in policy["required_mutation_families"]:
        total, caught, not_injected = (
            measurements.get(f"{family}_total"),
            measurements.get(f"{family}_caught"),
            measurements.get(f"{family}_not_injected"),
        )
        require(
            None not in (total, caught, not_injected),
            f"validation result does not measure the {family} family",
        )
        require(total > 0, f"observed {family} ran nothing")
        require(caught == total, f"observed {family} let a mutation escape")
        require(not_injected == 0, f"observed {family} failed to inject every mutation")
    return measurements


def check_evidence_identity(policy: dict, evidence: dict, commit: str, run: str) -> None:
    """What the human attests about the run, so their independent GitHub check is auditable."""
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
    # The workflow is an authority too: a run of some other workflow, however green, is not the
    # authoritative ladder the policy froze.
    require(
        str(evidence.get("workflow_commit_sha") or "") == policy["validation_workflow_commit_sha"],
        "final validation run is not the workflow commit the policy pins",
    )
    require(
        str(evidence.get("workflow_sha256") or "") == policy["validation_workflow_sha256"],
        "final validation run did not use the workflow content the policy pins",
    )


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

    # 5. The pinned validation artifacts, recomputed from the candidate's own blobs. Their
    #    identities are also stated inside the validation result, but that document is produced
    #    downstream of the very programs it names, so it corroborates rather than proves. This
    #    check reads the object store directly and is the one that binds.
    for rel, key in (
        ("harness/genesis_validate.py", "validation_driver_sha256"),
        ("harness/genesis_collect.py", "validation_aggregator_sha256"),
        ("harness/genesis-recipe.json", "validation_recipe_sha256"),
    ):
        require(rel in listed, f"pinned validation artifact is outside the trust root: {rel}")
        actual = digest(blob(repo, commit, rel))
        require(
            actual == policy[key],
            f"{rel} at the candidate does not match the {key} the policy pins (actual {actual})",
        )

    # 5. Evidence for this exact commit: identity attested by the human, measurements produced by
    #    the pinned driver, and the raw log bound so the GitHub check can be made independently.
    evidence_path = Path(args.evidence).resolve()
    evidence = read_json(evidence_path, "final validation evidence")
    check_evidence_identity(policy, evidence, commit, args.evidence_run)

    result_path = Path(args.result).resolve()
    result_bytes = result_path.read_bytes() if result_path.is_file() else b""
    require(bool(result_bytes), f"validation result does not exist: {result_path}")
    result_sha = digest(result_bytes)
    require(
        str(evidence.get("result_sha256") or "") == result_sha,
        f"validation result does not match the digest the evidence commits to (actual {result_sha})",
    )
    observed = check_result(policy, read_json(result_path, "validation result"), commit)

    log_path = Path(args.evidence_log).resolve()
    require(log_path.is_file(), f"validation log does not exist: {log_path}")
    log_sha = digest(log_path.read_bytes())
    require(
        str(evidence.get("log_sha256") or "") == log_sha,
        f"validation log does not match the digest the evidence commits to (actual {log_sha})",
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
        "validation_result_sha256": result_sha,
        "validation_driver_sha256": policy["validation_driver_sha256"],
        "validation_recipe_sha256": policy["validation_recipe_sha256"],
        "validation_aggregator_sha256": policy["validation_aggregator_sha256"],
        "validation_workflow_commit_sha": policy["validation_workflow_commit_sha"],
        "validation_workflow_sha256": policy["validation_workflow_sha256"],
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
    parser.add_argument("--result")
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
        args.evidence, args.evidence_log, args.result, args.evidence_run,
        args.approver, args.reason,
    )
    if not all(str(value or "").strip() for value in supplied):
        fail(
            "genesis authorization requires --commit, --policy, --expect-policy, "
            "--expect-verifier, --expect-manifest, --evidence, --evidence-log, --result, "
            "--evidence-run, --approver and --reason; every one of them is supplied by a human "
            "at ceremony time, not by the repository"
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
