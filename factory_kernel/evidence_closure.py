"""Compile the protected 21-claim evidence spine from verified builder and validator evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from .canonical import canonical_bytes, sha256_file, sha256_value
from .credential_env import scoped_environment
from .independence import (
    authority_for,
    build_certificate,
    externally_supplied_claims,
    verify_certificate,
)
from .manifest import ArtifactRef, Certification, ClaimRecord, RunManifest
from .provenance import BUILDER_CLAIMS, pack_sha256, verify_pack
from .spine import compile_evidence_index, load_policy


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
        "contract": holdout,
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
