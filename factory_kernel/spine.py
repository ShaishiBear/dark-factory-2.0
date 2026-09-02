"""Protected evidence-spine policy and deterministic closure compiler.

The manifest records what happened. This policy decides what *must* have happened. A builder
cannot lower its own required authority level by changing claim data.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from .canonical import sha256_file, sha256_value
from .manifest import ArtifactRef, ClaimRecord, RunManifest


@dataclass(frozen=True)
class ClaimRequirement:
    claim_id: str
    stage: str
    requires: tuple[str, ...]
    deterministic_required: bool
    independent_required: bool
    exact_head_required: bool
    final_evidence_required: bool


@dataclass(frozen=True)
class SpinePolicy:
    version: str
    requirements: tuple[ClaimRequirement, ...]
    raw: Mapping[str, object]

    def sha256(self) -> str:
        return sha256_value(self.raw)

    def requirement(self, claim_id: str) -> ClaimRequirement:
        for requirement in self.requirements:
            if requirement.claim_id == claim_id:
                return requirement
        raise KeyError(f"unknown evidence-spine claim: {claim_id}")


def _require_bool(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"spine requirement {key} must be boolean")
    return value


def load_policy(path: str | Path) -> SpinePolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read evidence-spine policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != "1.0":
        raise ValueError("evidence-spine policy version must be 1.0")
    entries = raw.get("required_claims")
    if not isinstance(entries, list) or not entries:
        raise ValueError("evidence-spine policy must contain required_claims")

    seen: set[str] = set()
    requirements: list[ClaimRequirement] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("evidence-spine requirement must be an object")
        claim_id = entry.get("id")
        stage = entry.get("stage")
        requires = entry.get("requires")
        if not isinstance(claim_id, str) or not claim_id.strip() or claim_id in seen:
            raise ValueError("evidence-spine claim ids must be unique non-empty strings")
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError(f"evidence-spine claim {claim_id} has invalid stage")
        if (
            not isinstance(requires, list)
            or any(not isinstance(item, str) or not item.strip() for item in requires)
            or len(set(requires)) != len(requires)
        ):
            raise ValueError(f"evidence-spine claim {claim_id} has invalid requires")
        unknown_or_forward = [item for item in requires if item not in seen]
        if unknown_or_forward:
            raise ValueError(
                f"evidence-spine claim {claim_id} requires unknown/forward claims: "
                + ", ".join(unknown_or_forward)
            )
        deterministic = _require_bool(entry, "deterministic_required")
        independent = _require_bool(entry, "independent_required")
        if independent and not deterministic:
            raise ValueError(f"evidence-spine claim {claim_id} cannot skip deterministic authority")
        final = _require_bool(entry, "final_evidence_required")
        if not final:
            raise ValueError(f"required spine claim {claim_id} must be bound into final evidence")
        requirements.append(
            ClaimRequirement(
                claim_id=claim_id,
                stage=stage,
                requires=tuple(requires),
                deterministic_required=deterministic,
                independent_required=independent,
                exact_head_required=_require_bool(entry, "exact_head_required"),
                final_evidence_required=final,
            )
        )
        seen.add(claim_id)
    return SpinePolicy(version="1.0", requirements=tuple(requirements), raw=raw)


def _safe_artifact(root: Path, ref: ArtifactRef) -> Path:
    target = (root / ref.path).resolve()
    if root not in (target, *target.parents):
        raise ValueError(f"artifact escapes evidence root: {ref.path}")
    if not target.is_file():
        raise ValueError(f"evidence artifact is missing: {ref.path}")
    actual = sha256_file(target)
    if actual != ref.sha256:
        raise ValueError(f"evidence artifact hash mismatch: {ref.path}")
    return target


def verify_artifact_bytes(manifest: RunManifest, artifact_root: str | Path) -> None:
    root = Path(artifact_root).resolve()
    if not root.is_dir():
        raise ValueError(f"artifact root does not exist: {root}")
    for claim in manifest.claims:
        for ref in claim.referenced_artifacts():
            _safe_artifact(root, ref)


def _required_binding_hashes(
    requirement: ClaimRequirement, manifest: RunManifest
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for predecessor in requirement.requires:
        claim = manifest.claim(predecessor)
        if claim is None:
            continue
        hashes[predecessor] = claim.artifact.sha256
    return hashes


def assess_manifest(
    policy: SpinePolicy,
    manifest: RunManifest,
    *,
    expected_head_sha: str | None = None,
) -> dict:
    """Assess each canonical claim at 0/40/60/80 before final evidence inclusion.

    The 20% "exists" rung belongs to orchestration/workflow assessment. Once a claim enters this
    manifest it must already be a canonical artifact, so its first representable level is 40.
    """
    rows: list[dict] = []
    blockers: list[str] = []
    for requirement in policy.requirements:
        claim = manifest.claim(requirement.claim_id)
        if claim is None:
            rows.append({"claim_id": requirement.claim_id, "level": 0, "status": "missing"})
            blockers.append(f"{requirement.claim_id}: canonical artifact missing")
            continue
        level = 40
        reasons: list[str] = []
        if claim.stage != requirement.stage:
            reasons.append(f"stage {claim.stage!r} != required {requirement.stage!r}")

        predecessor_hashes = _required_binding_hashes(requirement, manifest)
        missing_predecessors = [item for item in requirement.requires if item not in predecessor_hashes]
        if missing_predecessors:
            reasons.append("required predecessor claims missing: " + ", ".join(missing_predecessors))
        else:
            binding_values = set(claim.bindings.values())
            unbound = [
                name for name, digest in predecessor_hashes.items() if digest not in binding_values
            ]
            if unbound:
                reasons.append("required provenance bindings missing: " + ", ".join(unbound))

        deterministic_ok = not requirement.deterministic_required or claim.deterministic is not None
        if deterministic_ok:
            level = 60
        else:
            reasons.append("deterministic certification missing")

        independent_ok = not requirement.independent_required or claim.independent is not None
        if deterministic_ok and independent_ok:
            level = 80
        elif requirement.independent_required:
            reasons.append("independent certification missing")

        if requirement.exact_head_required:
            if expected_head_sha is None:
                reasons.append("expected exact head SHA not supplied")
            elif claim.exact_head_sha != expected_head_sha:
                reasons.append("claim is not bound to expected exact head SHA")

        if reasons:
            blockers.extend(f"{requirement.claim_id}: {reason}" for reason in reasons)
        rows.append(
            {
                "claim_id": requirement.claim_id,
                "stage": requirement.stage,
                "level": level,
                "status": "ready" if not reasons and level == 80 else "incomplete",
                "reasons": reasons,
            }
        )
    return {
        "version": "1.0",
        "policy_sha256": policy.sha256(),
        "manifest_sha256": manifest.sha256(),
        "claims": rows,
        "blockers": blockers,
        "ready_for_evidence": not blockers and all(row["level"] == 80 for row in rows),
    }


def compile_evidence_index(
    policy: SpinePolicy,
    manifest: RunManifest,
    *,
    artifact_root: str | Path,
    head_sha: str,
) -> dict:
    """Compile the 100% spine only after all pre-evidence authorities are satisfied."""
    verify_artifact_bytes(manifest, artifact_root)
    assessment = assess_manifest(policy, manifest, expected_head_sha=head_sha)
    if not assessment["ready_for_evidence"]:
        raise ValueError("evidence spine is incomplete: " + "; ".join(assessment["blockers"]))

    claims: list[dict] = []
    for requirement in policy.requirements:
        claim = manifest.claim(requirement.claim_id)
        if claim is None:  # guarded above; keeps type/runtime behavior fail-closed
            raise ValueError(f"required claim disappeared: {requirement.claim_id}")
        claims.append(
            {
                "claim_id": claim.claim_id,
                "stage": claim.stage,
                "artifact_sha256": claim.artifact.sha256,
                "deterministic_sha256": (
                    claim.deterministic.artifact.sha256 if claim.deterministic is not None else None
                ),
                "independent_sha256": (
                    claim.independent.artifact.sha256 if claim.independent is not None else None
                ),
                "exact_head_sha": claim.exact_head_sha,
                "bindings": dict(sorted(claim.bindings.items())),
                "completion_level": 100,
            }
        )
    return {
        "version": "1.0",
        "base_sha": manifest.base_sha,
        "head_sha": head_sha,
        "policy_sha256": policy.sha256(),
        "manifest_sha256": manifest.sha256(),
        "claims": claims,
        "completion_level": 100,
    }
