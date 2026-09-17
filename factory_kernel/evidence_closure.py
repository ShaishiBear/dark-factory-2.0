"""Compile the protected 21-claim evidence spine from verified builder and validator evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from .canonical import canonical_bytes, sha256_bytes, sha256_file, sha256_value
from .credential_env import scoped_environment
from .independence import (
    REGISTRY,
    authority_for,
    build_certificate,
    externally_supplied_claims,
    verify_certificate,
)
from .manifest import ArtifactRef, Certification, ClaimRecord, RunManifest
from .provenance import BUILDER_CLAIMS, pack_sha256, verify_pack
from .spine import SpinePolicy, compile_evidence_index, load_policy


PRODUCERS = {
    "contract": "contract-worker",
    "tickets": "github-issue-state",
    "frontier": "github-issue-state",
    "context": "context-worker",
    "architecture-policy": "human-trust-root",
    "design": "context-design-worker",
    "architecture-governor": "architecture-worker",
    "test-plan": "test-author",
    "red-proof": "test-author",
    "green-proof": "implementation-worker",
    "impact": "implementation-worker",
    "architecture-drift": "implementation-worker",
    "architecture-conformance": "conformance-worker",
    "holdout-behavior": "application-runtime",
    "holdout-e2e": "application-runtime",
    "holdout-architecture": "architecture-holdout-model",
    "holdout-code": "blinded-code-holdout-model",
    "holdout-security": "pr-diff",
    "mutation": "mutation-catalogue",
    "ratchet": "quality-policy",
    "immunity": "failure-memory-registry",
}

DETERMINISTIC_AUTHORITIES = {
    "contract": "contract-validator",
    "tickets": "ticket-compiler",
    "frontier": "frontier-compiler",
    "context": "context-validator",
    "architecture-policy": "trust-root-drift-verifier",
    "design": "design-compiler",
    "architecture-governor": "architecture-governor-compiler",
    "test-plan": "red-plan-validator",
    "red-proof": "independent-red-replay",
    "green-proof": "independent-green-replay",
    "impact": "impact-validator",
    "architecture-drift": "architecture-guard-recompute",
    "architecture-conformance": "architecture-conformance-validator",
    "holdout-behavior": "canonical-core-holdout",
    "holdout-e2e": "canonical-browser-e2e",
    "holdout-architecture": "architecture-holdout-validator",
    "holdout-code": "blinded-holdout-validator",
    "holdout-security": "deterministic-security-guard",
    "mutation": "mutation-runner",
    "ratchet": "ratchet-verifier",
    "immunity": "immunity-verifier",
}


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read evidence closure JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"evidence closure JSON must be an object: {path}")
    return value


def _write(root: Path, rel: str, value: Mapping[str, Any]) -> ArtifactRef:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_bytes(dict(value)))
    return ArtifactRef(name=target.stem, path=rel, sha256=sha256_file(target))


def _ref(root: Path, rel: str, name: str) -> ArtifactRef:
    path = root / rel
    if not path.is_file():
        raise ValueError(f"required evidence artifact is missing: {rel}")
    return ArtifactRef(name=name, path=rel, sha256=sha256_file(path))


def _cert(
    root: Path,
    *,
    claim_id: str,
    kind: str,
    authority_id: str,
    subject: ArtifactRef,
    evidence: Mapping[str, Any],
) -> Certification:
    value = {
        "version": "1.0",
        "kind": kind,
        "authority_id": authority_id,
        "claim_id": claim_id,
        "subject_sha256": subject.sha256,
        "verdict": "pass",
        "evidence": dict(evidence),
    }
    ref = _write(root, f"spine/certifications/{claim_id}-{kind}.json", value)
    return Certification(kind=kind, authority_id=authority_id, artifact=ref)


def _require_hash(value: object, expected: str, label: str) -> None:
    if str(value or "") != expected:
        raise ValueError(f"evidence spine {label} hash mismatch")


def _load_immunity(repo_root: Path, artifact_root: Path) -> dict:
    output = artifact_root / "spine" / "validator" / "immunity-verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "harness/immunity.py", "--output", str(output)],
        cwd=repo_root,
        env=scoped_environment(scope="none"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode:
        raise ValueError("immunity verifier failed during evidence closure: " + ((proc.stdout or "") + (proc.stderr or ""))[-1600:])
    return _json(output)


def _derived_validator_artifacts(
    *,
    repo_root: Path,
    artifact_root: Path,
    legacy: Mapping[str, Any],
    holdout: Mapping[str, Any],
    architecture_holdout: Mapping[str, Any],
    head_sha: str,
) -> dict[str, ArtifactRef]:
    observed = legacy.get("observed")
    if not isinstance(observed, Mapping):
        raise ValueError("legacy Evidence Bundle has no observed full-harness evidence")
    harness_hash = str(legacy.get("harness_sha256") or "")
    if len(harness_hash) != 64:
        raise ValueError("legacy Evidence Bundle harness hash is invalid")
    if holdout.get("verdict") != "pass":
        raise ValueError("blinded code holdout did not pass")
    if architecture_holdout.get("verdict") != "pass":
        raise ValueError("architecture holdout did not pass")

    required_numbers = (
        "e2e_steps", "holdout_assertions", "mutations_total", "mutations_caught",
        "mutations_not_injected", "factory_mutations_total", "factory_mutations_caught",
        "factory_mutations_not_injected", "immunity_entries", "immunity_assertions",
    )
    for key in required_numbers:
        if not isinstance(observed.get(key), int):
            raise ValueError(f"full harness observed evidence is missing {key}")
    if observed["e2e_steps"] < 1:
        raise ValueError("browser holdout reported zero E2E steps")
    if observed["mutations_total"] != observed["mutations_caught"] or observed["mutations_not_injected"] != 0:
        raise ValueError("application mutation evidence is incomplete")
    if observed["factory_mutations_total"] != observed["factory_mutations_caught"] or observed["factory_mutations_not_injected"] != 0:
        raise ValueError("factory mutation evidence is incomplete")

    immunity = _load_immunity(repo_root, artifact_root)
    observed_immunity_sha = str(observed.get("immunity_sha256") or "")
    if observed_immunity_sha != immunity.get("registry_sha256"):
        raise ValueError("immunity registry changed between full harness and evidence closure")

    floors = _json(repo_root / ".factory" / "locks" / "floor.json")
    security = legacy.get("security")
    if not isinstance(security, Mapping) or security.get("verdict") != "pass":
        raise ValueError("deterministic security evidence is missing")
    architecture_verified = legacy.get("architecture_holdout")
    if not isinstance(architecture_verified, Mapping) or architecture_verified.get("verdict") != "pass":
        raise ValueError("verified architecture holdout evidence is missing")

    mutation = {
        "version": "1.0", "head_sha": head_sha, "harness_sha256": harness_hash,
        "application": {
            key: observed[key]
            for key in observed
            if key.startswith("mutations_")
        },
        "factory": {
            "total": observed["factory_mutations_total"],
            "caught": observed["factory_mutations_caught"],
            "not_injected": observed["factory_mutations_not_injected"],
        },
    }
    ratchet = {
        "version": "1.0", "head_sha": head_sha, "floors": floors,
        "observed": {
            key: observed[key]
            for key in floors
            if key in observed
        },
        "verdict": "pass",
    }
    immunity_claim = {
        "version": "1.0", "head_sha": head_sha,
        "registry_sha256": immunity["registry_sha256"],
        "active_entries": immunity["active_entries"],
        "assertions": immunity["assertions"],
        "entry_ids": immunity["entry_ids"],
        "verdict": "pass",
    }
    values: dict[str, Mapping[str, Any]] = {
        "holdout-behavior": {
            "version": "1.0", "head_sha": head_sha, "harness_sha256": harness_hash,
            "assertions": observed["holdout_assertions"], "verdict": "pass",
        },
        "holdout-e2e": {
            "version": "1.0", "head_sha": head_sha, "harness_sha256": harness_hash,
            "steps": observed["e2e_steps"], "verdict": "pass",
        },
        "holdout-architecture": dict(architecture_verified),
        "holdout-code": dict(holdout),
        "holdout-security": dict(security),
        "mutation": mutation,
        "ratchet": ratchet,
        "immunity": immunity_claim,
    }
    return {
        claim_id: _write(artifact_root, f"spine/validator/{claim_id}.json", value)
        for claim_id, value in values.items()
    }


def _validate_builder_bindings(
    *,
    pack: Mapping[str, Any],
    legacy: Mapping[str, Any],
    base_sha: str,
    head_sha: str,
) -> None:
    artifacts = pack["artifacts"]
    hashes = {claim_id: record["sha256"] for claim_id, record in artifacts.items()}
    values = {claim_id: record["content"] for claim_id, record in artifacts.items()}
    _require_hash(legacy.get("contract_sha256"), hashes["contract"], "contract")
    _require_hash(legacy.get("design_sha256"), hashes["design"], "design")
    _require_hash(legacy.get("proof_sha256"), hashes["green-proof"], "green proof")

    proof = values["green-proof"]
    red = values["red-proof"]
    for key in (
        "version", "test_commit", "contract_sha256", "design_sha256", "files",
        "checkpoints", "test_plan_sha256",
    ):
        if red.get(key) != proof.get(key):
            raise ValueError(f"RED proof does not match final GREEN proof: {key}")
    _require_hash(proof.get("test_plan_sha256"), hashes["test-plan"], "test plan")
    impact = proof.get("change_impact")
    if not isinstance(impact, Mapping):
        raise ValueError("final GREEN proof lacks change impact")
    _require_hash(impact.get("sha256"), hashes["impact"], "impact")
    architecture_guard = proof.get("architecture_guard")
    if not isinstance(architecture_guard, Mapping):
        raise ValueError("final GREEN proof lacks architecture drift")
    _require_hash(architecture_guard.get("sha256"), hashes["architecture-drift"], "architecture drift")
    _require_hash(proof.get("architecture_builder_sha256"), hashes["architecture-conformance"], "architecture conformance")

    conformance = values["architecture-conformance"]
    if conformance.get("verdict") != "conform" or conformance.get("head_sha") != head_sha:
        raise ValueError("builder architecture conformance is not exact-head conform")
    _require_hash(conformance.get("policy_sha256"), hashes["architecture-policy"], "architecture policy")
    _require_hash(conformance.get("contract_sha256"), hashes["contract"], "architecture contract")
    _require_hash(conformance.get("context_sha256"), hashes["context"], "architecture context")
    _require_hash(conformance.get("design_sha256"), hashes["design"], "architecture design")
    _require_hash(conformance.get("governor_sha256"), hashes["architecture-governor"], "architecture governor")

    governor = values["architecture-governor"]
    if governor.get("decision") != "proceed":
        raise ValueError("builder architecture governor did not authorize implementation")
    _require_hash(governor.get("policy_sha256"), hashes["architecture-policy"], "governor policy")
    _require_hash(governor.get("contract_sha256"), hashes["contract"], "governor contract")
    _require_hash(governor.get("context_sha256"), hashes["context"], "governor context")
    _require_hash(governor.get("design_sha256"), hashes["design"], "governor design")

    drift = values["architecture-drift"]
    if drift.get("base_sha") != base_sha or drift.get("head_sha") != head_sha:
        raise ValueError("architecture drift is not exact base/head bound")
    _require_hash(drift.get("policy_sha256"), hashes["architecture-policy"], "drift policy")
    _require_hash(drift.get("design_sha256"), hashes["design"], "drift design")

    legacy_arch = legacy.get("architecture")
    legacy_guard = legacy.get("architecture_guard")
    if not isinstance(legacy_arch, Mapping) or not isinstance(legacy_guard, Mapping):
        raise ValueError("legacy Evidence Bundle lacks verified architecture authorities")
    _require_hash(legacy_arch.get("sha256"), hashes["architecture-conformance"], "verified conformance")
    _require_hash(legacy_guard.get("sha256"), hashes["architecture-drift"], "verified drift")


def compile_full_spine(
    *,
    repo_root: str | Path,
    artifact_root: str | Path,
    legacy_bundle: Mapping[str, Any],
    builder_pack: Mapping[str, Any],
    holdout: Mapping[str, Any],
    architecture_holdout: Mapping[str, Any],
    pr_number: int,
    independent_certificates: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[RunManifest, dict]:
    repo = Path(repo_root).resolve()
    root = Path(artifact_root).resolve()
    if legacy_bundle.get("version") != "5.0":
        raise ValueError("evidence closure requires Evidence Bundle v5")
    base = str(legacy_bundle.get("base_sha") or "")
    head = str(legacy_bundle.get("head_sha") or "")
    issue = legacy_bundle.get("issue")
    if not isinstance(issue, int) or issue <= 0:
        raise ValueError("Evidence Bundle issue identity is invalid")
    pack = verify_pack(
        dict(builder_pack), expected_head_sha=head, expected_base_sha=base, expected_issue=issue
    )
    _validate_builder_bindings(pack=pack, legacy=legacy_bundle, base_sha=base, head_sha=head)

    builder_refs = {
        claim_id: _ref(root, f"spine/builder/{claim_id}.json", claim_id)
        for claim_id in BUILDER_CLAIMS
    }
    for claim_id, ref in builder_refs.items():
        if ref.sha256 != pack["artifacts"][claim_id]["sha256"]:
            raise ValueError(f"materialized builder artifact disagrees with provenance note: {claim_id}")

    validator_refs = _derived_validator_artifacts(
        repo_root=repo,
        artifact_root=root,
        legacy=legacy_bundle,
        holdout=holdout,
        architecture_holdout=architecture_holdout,
        head_sha=head,
    )
    refs = {**builder_refs, **validator_refs}
    policy = load_policy(repo / ".factory" / "evidence-spine.json")
    manifest = RunManifest.create(
        run_id=f"pr-{pr_number}-evidence-{head[:12]}", issue=issue, base_sha=base
    )

    # Independent authorities are separately executed; closure only verifies their separation.
    # A judgement produced anywhere on the builder path can never fill an independent slot: every
    # certificate below is checked against the exact builder provenance hashes.
    claim_hashes = {claim_id: ref.sha256 for claim_id, ref in refs.items()}
    builder_hashes = {claim_id: ref.sha256 for claim_id, ref in builder_refs.items()}
    external_claims = externally_supplied_claims()
    supplied = dict(independent_certificates or {})
    unexpected = sorted(set(supplied) - external_claims)
    if unexpected:
        raise ValueError(
            "independent certificates supplied for claims that do not accept them: "
            + ", ".join(unexpected)
        )
    # Judgements the validator already executed in-process; the kernel, not a model, binds them.
    in_process_judgements: dict[str, Mapping[str, Any]] = {
        "architecture-drift": architecture_holdout,
        "architecture-conformance": architecture_holdout,
    }

    def _independent_certificate(claim_id: str) -> dict:
        authority_for(claim_id)  # fail closed when policy requires unattainable independence
        if claim_id in external_claims:
            certificate = supplied.get(claim_id)
            if certificate is None:
                raise ValueError(
                    f"policy requires independent certification of {claim_id}, but no "
                    "independent certificate was supplied by the validator"
                )
            return dict(certificate)
        judgement = in_process_judgements.get(claim_id)
        if judgement is None:
            raise ValueError(f"no independent authority input for required claim: {claim_id}")
        return build_certificate(
            claim_id=claim_id,
            claim_hashes=claim_hashes,
            head_sha=head,
            base_sha=base,
            judgement=judgement,
        )

    deterministic_evidence: dict[str, Mapping[str, Any]] = {
        "contract": dict(legacy_bundle["contract"]),
        "tickets": {"issue": issue, "contract_sha256": refs["contract"].sha256},
        "frontier": {"issue": issue, "ready": True, "ticket_sha256": refs["tickets"].sha256},
        "context": {"contract_sha256": refs["contract"].sha256},
        "architecture-policy": {"policy_sha256": refs["architecture-policy"].sha256, "current_with_main": True},
        "design": {"design_sha256": refs["design"].sha256},
        "architecture-governor": {"governor_sha256": refs["architecture-governor"].sha256, "decision": "proceed"},
        "test-plan": {"test_plan_sha256": refs["test-plan"].sha256},
        "red-proof": {"red_replay": legacy_bundle["proof"]["red_replay"]},
        "green-proof": {"green_replay": legacy_bundle["proof"]["green_replay"]},
        "impact": {"impact_sha256": refs["impact"].sha256},
        "architecture-drift": dict(legacy_bundle["architecture_guard"]),
        "architecture-conformance": dict(legacy_bundle["architecture"]),
        "holdout-behavior": {"harness_sha256": legacy_bundle["harness_sha256"], "verdict": "pass"},
        "holdout-e2e": {"harness_sha256": legacy_bundle["harness_sha256"], "verdict": "pass"},
        "holdout-architecture": dict(legacy_bundle["architecture_holdout"]),
        "holdout-code": {"holdout_sha256": sha256_value(holdout), "verdict": "pass"},
        "holdout-security": dict(legacy_bundle["security"]),
        "mutation": {"artifact_sha256": refs["mutation"].sha256, "verdict": "pass"},
        "ratchet": {"artifact_sha256": refs["ratchet"].sha256, "verdict": "pass"},
        "immunity": {"artifact_sha256": refs["immunity"].sha256, "verdict": "pass"},
    }

    for requirement in policy.requirements:
        claim_id = requirement.claim_id
        subject = refs.get(claim_id)
        if subject is None:
            raise ValueError(f"no materialized artifact for required spine claim: {claim_id}")
        det = _cert(
            root,
            claim_id=claim_id,
            kind="deterministic",
            authority_id=DETERMINISTIC_AUTHORITIES[claim_id],
            subject=subject,
            evidence=deterministic_evidence[claim_id],
        ) if requirement.deterministic_required else None
        independent = None
        if requirement.independent_required:
            certificate = _independent_certificate(claim_id)
            evidence = verify_certificate(
                certificate,
                claim_id=claim_id,
                claim_hashes=claim_hashes,
                builder_artifact_hashes=builder_hashes,
                head_sha=head,
                base_sha=base,
            )
            independent = _cert(
                root,
                claim_id=claim_id,
                kind="independent",
                authority_id=evidence["authority_id"],
                subject=subject,
                evidence=evidence,
            )
        bindings = {
            predecessor: manifest.claim(predecessor).artifact.sha256
            for predecessor in requirement.requires
            if manifest.claim(predecessor) is not None
        }
        if len(bindings) != len(requirement.requires):
            raise ValueError(f"cannot bind required predecessor claims for {claim_id}")
        manifest.add(
            ClaimRecord(
                claim_id=claim_id,
                stage=requirement.stage,
                producer=PRODUCERS[claim_id],
                artifact=subject,
                deterministic=det,
                independent=independent,
                exact_head_sha=head if requirement.exact_head_required else None,
                bindings=bindings,
            )
        )

    manifest.write(root / "spine" / "run-manifest.json")
    index = compile_evidence_index(policy, manifest, artifact_root=root, head_sha=head)
    (root / "spine" / "evidence-index.json").write_bytes(canonical_bytes(index))
    return manifest, {
        **index,
        "builder_provenance_sha256": pack_sha256(pack),
    }


# ---------------------------------------------------------------------------------------------
# Typed proof companions (WP05, R05). Everything below is additive: compile_full_spine and the
# legacy spine hashes are untouched. The companions are built from the manifest the closure
# already produced, from identities the trusted wrapper observed itself, and are verified and
# assessed in shadow. They authorise nothing; merge_verify still re-derives the legacy closure.
# ---------------------------------------------------------------------------------------------

ATTESTATION_AUTHORITY = "evidence-spine-closure"
ATTESTATION_INDEX_PATH = "spine/attestations/index.json"
ATTESTATION_STORE_DIR = "spine/attestations/store"
# The trusted program closure whose bytes decide what the companions mean. Read from the kernel
# checkout, never from the candidate tree.
CLOSURE_PROGRAMS = (
    "scripts/factory_evidence_spine.py", "scripts/factory_evidence.py",
    "factory_kernel/evidence_closure.py", "factory_kernel/spine.py", "factory_kernel/independence.py",
    "factory_kernel/manifest.py", "factory_kernel/provenance.py", "factory_kernel/canonical.py",
    "factory_kernel/attestations.py", "factory_kernel/proof_dependencies.py", "factory_kernel/proof_store.py",
)
CONFIGURATION_FILES = (".factory/kernel.json", ".factory/project-profile.json")
LOCK_FILES = ("app/backend/uv.lock", "app/frontend/bun.lock")
JUDGE_CLAIMS = frozenset({"holdout-behavior", "holdout-e2e", "holdout-architecture", "holdout-code", "holdout-security"})
RUNNER_IMAGE_VARIABLES = ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion")


def _git_observe(candidate_root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(candidate_root), *args], capture_output=True, timeout=60)
    if proc.returncode:
        return None
    return proc.stdout.decode("utf-8", "replace").strip()


def observe_issuer(environ: Mapping[str, str], *, source_revision: str) -> dict:
    """The platform identity of this execution, as the trusted wrapper sees it. Anything not run
    by GitHub Actions is `local`: recorded, never authenticated."""
    if environ.get("GITHUB_ACTIONS") == "true":
        try:
            run_id, attempt = int(environ.get("GITHUB_RUN_ID", "")), int(environ.get("GITHUB_RUN_ATTEMPT", ""))
        except ValueError:
            run_id, attempt = 0, 0
        return {"platform": "github-actions",
                "workflow_id": environ.get("GITHUB_WORKFLOW_REF") or environ.get("GITHUB_WORKFLOW") or "unknown",
                "run_id": run_id, "attempt": attempt, "job_id": environ.get("GITHUB_JOB") or "unknown",
                "source_revision": source_revision}
    return {"platform": "local", "workflow_id": "local", "run_id": 0, "attempt": 0, "job_id": "local",
            "source_revision": source_revision}


def observe_closure_environment(*, kernel_root: str | Path, candidate_root: str | Path, head_sha: str,
                                environ: Mapping[str, str]) -> dict:
    """Identities the companions depend on, observed by trusted code: the authority program
    closure and policies from the kernel checkout, the candidate tree and its lock files from
    the candidate repository at the exact head, the interpreter and runner image from the host.
    A lock file absent from the candidate tree is reported absent, not invented."""
    kernel, candidate = Path(kernel_root).resolve(), Path(candidate_root).resolve()
    from .attestations import SCHEMA_VERSION as ATTESTATION_VERSION  # local import: closure lists this module
    from .proof_dependencies import COMPILER_VERSION, load_profiles

    policy = load_policy(kernel / ".factory" / "evidence-spine.json")
    profiles = load_profiles(kernel / ".factory" / "authority-profiles.json",
                             spine_claim_ids=[requirement.claim_id for requirement in policy.requirements])
    program_closure = sha256_value([[rel, sha256_file(kernel / rel)] for rel in CLOSURE_PROGRAMS])
    independence_profile = sha256_value([{"claim_id": e.claim_id, "authority_id": e.authority_id, "subject_claim": e.subject_claim,
                                          "binds": list(e.binds), "sees": list(e.sees), "externally_supplied": e.externally_supplied,
                                          "extra_inputs": list(e.extra_inputs)} for e in REGISTRY])
    configuration = sha256_value([[rel, sha256_file(kernel / rel)] for rel in CONFIGURATION_FILES])
    toolchain = sha256_value({"python": sys.version.split()[0], "platform": sys.platform,
                              "attestation_schema": ATTESTATION_VERSION, "dependency_compiler": COMPILER_VERSION})
    runtime_image = sha256_value({name: environ.get(name, "unobserved") for name in RUNNER_IMAGE_VARIABLES})
    candidate_tree = _git_observe(candidate, "rev-parse", f"{head_sha}^{{tree}}")
    if not candidate_tree:
        raise ValueError(f"candidate tree for {head_sha} cannot be observed in {candidate}")
    locks: dict[str, str] = {}
    for rel in LOCK_FILES:
        proc = subprocess.run(["git", "-C", str(candidate), "show", f"{head_sha}:{rel}"], capture_output=True, timeout=60)
        if proc.returncode == 0:
            locks[rel] = sha256_bytes(proc.stdout)
    return {
        "policy_sha256": policy.sha256(), "authority_profiles_sha256": profiles.sha256, "profiles": profiles,
        "program_closure_sha256": program_closure, "independence_profile_sha256": independence_profile,
        "configuration_digest": configuration, "toolchain_digest": toolchain, "runtime_image_digest": runtime_image,
        "candidate_tree": candidate_tree, "dependency_lock_digests": locks,
    }


def companion_inputs(environment: Mapping[str, Any], *, base_sha: str) -> list[dict]:
    """The declared dependency closure of one companion, one row per identity the protected
    profile can require. Coverage is complete for every observed identity; an unobservable lock
    file simply has no row, which the profile turns into `dependency_missing`."""
    rows = [
        {"kind": "exact-tree", "identity": "candidate_tree", "digest": environment["candidate_tree"], "coverage": "complete"},
        {"kind": "exact-tree", "identity": "base_commit", "digest": base_sha, "coverage": "complete"},
        {"kind": "trusted-authority", "identity": "program_closure", "digest": environment["program_closure_sha256"], "coverage": "complete"},
        {"kind": "trusted-policy", "identity": "spine_policy", "digest": environment["policy_sha256"], "coverage": "complete"},
        {"kind": "trusted-policy", "identity": "independence_profile", "digest": environment["independence_profile_sha256"], "coverage": "complete"},
        {"kind": "trusted-policy", "identity": "authority_profiles", "digest": environment["authority_profiles_sha256"], "coverage": "complete"},
        {"kind": "environment", "identity": "toolchain", "digest": environment["toolchain_digest"], "coverage": "complete"},
        {"kind": "environment", "identity": "configuration", "digest": environment["configuration_digest"], "coverage": "complete"},
    ]
    for rel, digest in sorted(environment["dependency_lock_digests"].items()):
        rows.append({"kind": "environment", "identity": f"lock:{rel}", "digest": digest, "coverage": "complete"})
    return rows


def compile_attestations(*, manifest: RunManifest, index: Mapping[str, Any], policy: SpinePolicy,
                         artifact_root: str | Path, repository_id: int, head_sha: str, environment: Mapping[str, Any],
                         issuer: Mapping[str, Any], created_at: str) -> dict:
    """One companion per spine claim, retained in the proof store, independently verified against
    the artifacts on disk, and assessed for currency in shadow against the very identities just
    observed. Returns the index written to `spine/attestations/index.json` and a summary."""
    from . import proof_store
    from .attestations import Refusal, TrustedExecution, build_attestation, verify_attestation
    from .proof_dependencies import assess_currency, build_dependency_index

    root = Path(artifact_root).resolve()
    store = root / ATTESTATION_STORE_DIR
    profiles = environment["profiles"]
    rows_by_claim = {row["claim_id"]: row for row in index["claims"] if isinstance(row, Mapping)}
    authority = {"id": ATTESTATION_AUTHORITY, "program_closure_sha256": environment["program_closure_sha256"],
                 "policy_sha256": environment["policy_sha256"],
                 "independence_profile_sha256": environment["independence_profile_sha256"]}
    env = {"toolchain_digest": environment["toolchain_digest"], "dependency_lock_digests": dict(environment["dependency_lock_digests"]),
           "runtime_image_digest": environment["runtime_image_digest"], "configuration_digest": environment["configuration_digest"]}
    subject = {"repository_id": repository_id, "base_commit": manifest.base_sha, "candidate_commit": head_sha,
               "candidate_tree": environment["candidate_tree"], "programme_sha256": None}
    inputs = companion_inputs(environment, base_sha=manifest.base_sha)

    records: list[dict] = []
    by_claim: dict[str, str] = {}
    for requirement in policy.requirements:
        claim = manifest.claim(requirement.claim_id)
        if claim is None:
            raise ValueError(f"manifest lacks required claim {requirement.claim_id}")
        row = rows_by_claim.get(requirement.claim_id) or {}
        evidence = []
        for ref in claim.referenced_artifacts():
            data = (root / ref.path).read_bytes()
            if sha256_bytes(data) != ref.sha256:
                raise ValueError(f"materialized artifact {ref.path} differs from the manifest digest")
            proof_store.put_verified_object(store, data, role="judge" if requirement.claim_id in JUDGE_CLAIMS else "public")
            evidence.append({"retained_object_id": ref.path, "sha256": ref.sha256, "media_type": ref.media_type})
        complete = row.get("completion_level") == 100
        reasons = [str(reason) for reason in (row.get("reasons") or []) if isinstance(reason, str)]
        record = build_attestation(TrustedExecution(
            claim_key=requirement.claim_id, obligation_profile_id=profiles.obligations[requirement.claim_id],
            subject=subject, authority=authority, environment=env, inputs=inputs, evidence=evidence,
            outcome={"verdict": "pass" if complete else "incomplete", "reason_codes": reasons},
            issuer=issuer, created_at=created_at))
        records.append(record)
        by_claim[requirement.claim_id] = record["attestation_id"]
    proof_store.index_run(store, manifest.run_id, records)

    registry = {ATTESTATION_AUTHORITY: authority}

    def reader(object_id: str) -> bytes | None:
        path = root / object_id
        return path.read_bytes() if path.is_file() else None

    verification: dict[str, dict] = {}
    verified: dict[str, Any] = {}
    for record in records:
        outcome = verify_attestation(record, issuer, reader, registry)
        if isinstance(outcome, Refusal):
            verification[record["attestation_id"]] = {"verified": False, **outcome.to_dict()}
        else:
            verification[record["attestation_id"]] = {"verified": True, "dependency_digest": outcome.dependency_digest}
            verified[record["attestation_id"]] = outcome

    edges = [{"source": by_claim[requirement.claim_id], "target": by_claim[predecessor], "kind": "depends_on"}
             for requirement in policy.requirements for predecessor in requirement.requires]
    dependency_index = build_dependency_index({aid: item for aid, item in verified.items()}, edges) if verified else None

    observed = {row["identity"]: row["digest"] for row in inputs}
    shadow: dict[str, dict] = {}
    for requirement in policy.requirements:
        attestation_id = by_claim[requirement.claim_id]
        item = verified.get(attestation_id)
        if item is None:
            refusal = verification[attestation_id]
            shadow[attestation_id] = {"status": refusal["status"], "reason_codes": refusal["reason_codes"],
                                      "affected": [], "satisfies_obligation": False, "detail": refusal["detail"]}
            continue
        predecessors = {by_claim[name]: shadow[by_claim[name]]["status"] for name in requirement.requires}
        result = assess_currency(item, {"issuer_valid": True, "retained": True, "dependencies": observed, "coverage": "complete",
                                        "predecessors": predecessors}, profiles.profile_for(requirement.claim_id))
        shadow[attestation_id] = result.to_dict()

    index_record = {
        "schema": "dark-factory/attestation-index", "schema_version": "1.0", "authority": "shadow",
        "proof_reuse_allowed": False, "run_id": manifest.run_id, "head_sha": head_sha, "base_sha": manifest.base_sha,
        "attestations": records, "verification": verification, "dependency_index": dependency_index, "shadow_currency": shadow,
    }
    raw = canonical_bytes(index_record)
    target = root / ATTESTATION_INDEX_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    summary = {
        "index_sha256": sha256_bytes(raw), "count": len(records),
        "verified": sum(1 for row in verification.values() if row["verified"]),
        "shadow_current": sum(1 for row in shadow.values() if row["status"] == "current"),
        "proof_reuse_allowed": False,
    }
    return {"index": index_record, "summary": summary}
