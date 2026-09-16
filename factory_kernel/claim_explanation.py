"""Read-only projection of existing proof records, never a qualification authority.

Recorded dependency currency is not current proof. Legacy bundles do not retain a complete
authority/toolchain/live-world identity or authenticate their own issuer. Even intact records
therefore remain insufficient for proof reuse. Nothing here executes an authority or writes state.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path, PurePosixPath
from typing import Mapping

from .canonical import sha256_bytes, sha256_value
from .evidence_closure import DETERMINISTIC_AUTHORITIES, PRODUCERS
from .independence import authority_for
from .manifest import GIT_OID, SHA256, RunManifest
from .programme import parse_json
from .provenance import BUILDER_CLAIMS
from .spine import assess_manifest, load_policy


def _read(root: Path, relative: str) -> tuple[dict | None, str | None, str | None]:
    """Bound all reads, including Windows drive/ADS paths and symlinked parents."""
    parts = PurePosixPath(relative).parts
    if (not parts or "\\" in relative or ":" in relative
            or PurePosixPath(relative).is_absolute() or ".." in parts):
        return None, None, "unsafe-path"
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            return None, None, "unsafe-path"
    if not path.resolve().is_relative_to(root.resolve()):
        return None, None, "unsafe-path"
    try:
        if not path.exists():
            return None, None, "absent"
        if not path.is_file() or path.stat().st_size > 250000:
            return None, None, "invalid-artifact"
        raw = path.read_bytes()
        value = parse_json(raw.decode("utf-8"))
        if not isinstance(value, dict):
            return None, None, "invalid-artifact"
        return value, sha256_bytes(raw), None
    except (OSError, ValueError, UnicodeError):
        return None, None, "invalid-artifact"


def _digest(value: object) -> str | None:
    return value if isinstance(value, str) and SHA256.fullmatch(value) else None


def explain_run(*, artifact_root: Path, policy_path: Path, expected_head_sha: str,
                expected_base_sha: str, current_claim_hashes: Mapping[str, str] | None = None) -> dict:
    """Explain retained files against caller-observed subject/policy, without network or effects.

    The host selects both paths; never expose arbitrary paths as HTTP request parameters.
    current_claim_hashes contains optional freshly observed claim artifact identities, not
    replacement attestations. Absent identities cannot establish complete semantic currency.
    """
    if not all(isinstance(x, str) and GIT_OID.fullmatch(x)
               for x in (expected_head_sha, expected_base_sha)):
        raise ValueError("full expected base and head revisions required")
    policy = load_policy(policy_path)
    current = dict(current_claim_hashes or {})
    known = {req.claim_id for req in policy.requirements}
    if not current.keys() <= known or any(_digest(x) is None for x in current.values()):
        raise ValueError("current dependencies must name known claims and SHA256 identities")
    root = Path(artifact_root)
    if root.is_symlink():
        raise ValueError("evidence root cannot be a symlink")
    bundle, bundle_digest, bundle_gap = _read(root, "evidence-bundle.json")
    raw_manifest, manifest_digest, manifest_gap = _read(root, "spine/run-manifest.json")
    manifest = None
    if raw_manifest is not None:
        try:
            manifest = RunManifest.from_dict(raw_manifest)
        except (ValueError, TypeError, AttributeError):
            manifest_gap = "invalid-manifest"
    index = (bundle or {}).get("spine")
    global_gaps = []
    if bundle_gap:
        global_gaps.append("bundle-" + bundle_gap)
    if manifest_gap:
        global_gaps.append("manifest-" + manifest_gap)
    recorded = {}
    if isinstance(index, dict) and index.get("version") == "1.0":
        entries = index.get("claims")
        if (isinstance(entries, list) and len(entries) <= 100
                and all(isinstance(x, dict) and isinstance(x.get("claim_id"), str)
                        and x["claim_id"] in known for x in entries)
                and len({x["claim_id"] for x in entries}) == len(entries)):
            recorded = {x["claim_id"]: x for x in entries}
        else:
            global_gaps.append("invalid-or-duplicate-index-claims")
    else:
        index = {}
        global_gaps.append("index-unavailable")
    comparisons = []

    def compare(name, recorded_value, expected_value):
        state = "unknown" if recorded_value is None else (
            "current" if recorded_value == expected_value else "stale")
        comparisons.append({"dependency": name, "recorded": recorded_value,
                            "expected": expected_value, "status": state})

    compare("subject.head", (bundle or {}).get("head_sha"), expected_head_sha)
    compare("subject.base", (bundle or {}).get("base_sha"), expected_base_sha)
    compare("index.head", index.get("head_sha"), expected_head_sha)
    compare("index.base", index.get("base_sha"), expected_base_sha)
    compare("policy.spine", _digest(index.get("policy_sha256")), policy.sha256())
    if manifest:
        compare("manifest.base", manifest.base_sha, expected_base_sha)
        compare("index.manifest", _digest(index.get("manifest_sha256")), manifest.sha256())
        compare("bundle.manifest", _digest((bundle or {}).get("run_manifest_sha256")), manifest.sha256())
        if (bundle or {}).get("issue") != manifest.issue:
            global_gaps.append("issue-identity-mismatch")
    assessment = assess_manifest(policy, manifest, expected_head_sha=expected_head_sha) if manifest else None
    assessed = {x["claim_id"]: x for x in assessment["claims"]} if assessment else {}
    rows = {}
    for requirement in policy.requirements:
        key = requirement.claim_id
        claim = manifest.claim(key) if manifest else None
        retained = recorded.get(key)
        gaps = list(global_gaps)
        changed = [x["dependency"] for x in comparisons if x["status"] == "stale"]
        gaps.extend(x["dependency"] + "-unknown" for x in comparisons if x["status"] == "unknown")
        dependencies = []
        subject = claim.artifact.sha256 if claim else _digest((retained or {}).get("artifact_sha256"))
        if key not in current:
            gaps.append("current-claim-identity-unavailable")
        if key in current and subject != current[key]:
            changed.append("claim." + key)
        for predecessor in requirement.requires:
            prior = rows[predecessor]
            observed = (claim.bindings if claim else (retained or {}).get("bindings", {}))
            bound = observed.get(predecessor) if isinstance(observed, dict) else None
            expected = current.get(predecessor, prior["subject_sha256"])
            state = "unknown" if bound is None or expected is None else (
                "current" if bound == expected else "stale")
            dependencies.append({"claim_id": predecessor, "recorded_sha256": _digest(bound),
                                 "expected_sha256": expected, "status": state})
            if state == "stale" or prior["recorded_dependency_status"] == "stale":
                changed.append("predecessor." + predecessor)
            if state == "unknown" or prior["record_integrity"] != "intact":
                gaps.append("predecessor-unavailable-or-insufficient:" + predecessor)
        refs = []
        if claim:
            if assessed[key]["status"] != "ready":
                gaps.append("spine-obligation-incomplete")
            if claim.producer != PRODUCERS.get(key):
                gaps.append("unexpected-producer")
            if requirement.exact_head_required and claim.exact_head_sha != expected_head_sha:
                changed.append("claim.exact-head")
            expected_index = {"artifact_sha256": claim.artifact.sha256,
                              "deterministic_sha256": claim.deterministic.artifact.sha256 if claim.deterministic else None,
                              "independent_sha256": claim.independent.artifact.sha256 if claim.independent else None,
                              "exact_head_sha": claim.exact_head_sha, "bindings": dict(claim.bindings),
                              "stage": claim.stage, "completion_level": 100}
            if retained is None or any(retained.get(k) != v for k, v in expected_index.items()):
                gaps.append("index-claim-mismatch")
            for ref in claim.referenced_artifacts():
                value, digest, error = _read(root, ref.path)
                status = error or ("intact" if digest == ref.sha256 else "hash-mismatch")
                refs.append({"path": ref.path, "sha256": ref.sha256, "status": status})
                if status != "intact":
                    gaps.append("artifact-" + status)
                for certification in (claim.deterministic, claim.independent):
                    if certification is None or certification.artifact != ref or status != "intact":
                        continue
                    required = (DETERMINISTIC_AUTHORITIES.get(key) if certification.kind == "deterministic"
                                else authority_for(key).authority_id)
                    if (certification.authority_id != required or value.get("authority_id") != required
                            or value.get("kind") != certification.kind or value.get("claim_id") != key
                            or value.get("subject_sha256") != subject or value.get("verdict") != "pass"
                            or value.get("version") != "1.0"):
                        gaps.append("authority-envelope-mismatch")
                    if certification.kind == "independent":
                        evidence = value.get("evidence", {})
                        judgement = evidence.get("judgement_sha256") if isinstance(evidence, dict) else None
                        if _digest(judgement) is None or judgement in {
                            c.artifact.sha256 for c in manifest.claims if c.claim_id in BUILDER_CLAIMS}:
                            gaps.append("independent-origin-unestablished")
                        spec = authority_for(key)
                        if (not isinstance(evidence, dict) or evidence.get("authority_id") != spec.authority_id
                                or evidence.get("subject_sha256") != subject
                                or evidence.get("head_sha") != expected_head_sha
                                or evidence.get("base_sha") != expected_base_sha
                                or evidence.get("verdict") not in {"pass", "conform"}
                                or not isinstance(evidence.get("bindings"), dict)
                                or any(evidence["bindings"].get(name) != (
                                    manifest.claim(name).artifact.sha256 if manifest.claim(name) else None)
                                       for name in spec.binds)):
                            gaps.append("independent-binding-mismatch")
        else:
            gaps.append("claim-manifest-unavailable")
        present = claim is not None or retained is not None
        currency = "stale" if changed else ("unknown" if gaps else "current")
        # No legacy record can prove these omitted semantic inputs or authenticate itself.
        proof_gaps = ["issuer-provenance-not-authenticated", "authority-program-identity-unavailable",
                      "toolchain-identity-unavailable", "live-world-replay-not-assessed"]
        rows[key] = {"claim_id": key, "record_id": "claim_" + sha256_value({
                         "manifest": index.get("manifest_sha256"), "claim": key,
                         "subject": subject, "base": index.get("base_sha"),
                         "head": index.get("head_sha")}),
                     "obligation": asdict(requirement),
                     "required_authorities": {"deterministic": DETERMINISTIC_AUTHORITIES.get(key),
                         "independent": authority_for(key).authority_id if requirement.independent_required else None},
                     "subject_sha256": subject, "recorded_claim": claim.to_dict() if claim else None,
                     "artifact_refs": refs, "dependencies": dependencies,
                     "record_integrity": "intact" if not gaps else "incomplete",
                     "recorded_dependency_status": currency,
                     "evidence_status": "absent" if not present else ("stale" if changed else "insufficient"),
                     "changed_dependencies": sorted(set(changed)), "gaps": sorted(set(gaps + proof_gaps)),
                     "proof_status": "not-established"}
    return {"schema": "dark-factory/claim-explanation", "schema_version": "1.0",
            "authority": "observation-only", "proof_reuse_allowed": False,
            "currency_scope": "recorded-dependencies-only", "subject": {
                "base_sha": expected_base_sha, "head_sha": expected_head_sha},
            "sources": {"bundle_sha256": bundle_digest, "manifest_file_sha256": manifest_digest,
                        "policy_sha256": policy.sha256()},
            "dependency_comparisons": comparisons, "claims": list(rows.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    print(json.dumps(explain_run(artifact_root=args.artifacts, policy_path=args.policy,
                                 expected_head_sha=args.head, expected_base_sha=args.base), indent=2))


if __name__ == "__main__":
    main()
