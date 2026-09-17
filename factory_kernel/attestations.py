"""Typed proof companions (SPECIFICATION 3.4, WP05).

An attestation is an immutable envelope around one authority execution: the exact claim and
subject it judged, the authority and policy identities that judged it, the environment the
judgement ran in, every dependency it declares, the retained evidence objects it produced, its
outcome and the platform issuer that ran it. Every value comes from trusted launch or authority
code (`build_attestation` takes a `TrustedExecution` the caller observed); nothing is accepted
from a builder, and the identity of the record is the digest of its own canonical body.

`verify_attestation` trusts none of that until it has re-derived it: the body digest, the issuer
against an independent observation, the authority identities against the protected registry, the
bytes of every retained object, and the completeness of the declared dependency closure. A
signature, a workflow-success bit or a green badge alone is not evidence here.

Companions never replace the legacy 21-claim spine: `harness/merge_verify.py` still re-derives
merge authority from the protected policy. This module only records what happened precisely
enough for `proof_dependencies.assess_currency` to say, later, whether it is still true.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping, Sequence

from .canonical import sha256_bytes, sha256_value

SCHEMA = "dark-factory/attestation"
SCHEMA_VERSION = "1.0"
INPUT_KINDS = ("exact-tree", "trusted-authority", "trusted-policy", "environment", "live-world")
REQUIRED_INPUT_KINDS = ("exact-tree", "trusted-authority", "trusted-policy")
COVERAGES = ("complete", "partial", "unknown")
VERDICTS = ("pass", "fail", "incomplete")
PLATFORMS = ("github-actions", "local")
AUTHENTICATED_PLATFORMS = ("github-actions",)
SUBJECT_FIELDS = ("repository_id", "base_commit", "candidate_commit", "candidate_tree", "programme_sha256")
AUTHORITY_FIELDS = ("id", "program_closure_sha256", "policy_sha256", "independence_profile_sha256")
ENVIRONMENT_FIELDS = ("toolchain_digest", "dependency_lock_digests", "runtime_image_digest", "configuration_digest")
ISSUER_FIELDS = ("platform", "workflow_id", "run_id", "attempt", "job_id", "source_revision")
LIVE_FIELDS = ("observer_id", "subject", "value_digest", "observed_at", "validity_policy_id")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
OBJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,119}){0,7}")
MAX_ITEMS = 256


class AttestationRefused(ValueError):
    """The record is not a well-formed attestation. Never a verdict about its claim."""


@dataclass(frozen=True)
class Refusal:
    """Why a record cannot be a verified attestation. `status` is the currency class the refusal
    implies: `rejected` for forged or mismatched material, `insufficient` for material that is
    merely missing or undeclared."""

    status: str
    reason_codes: tuple[str, ...]
    detail: str

    def to_dict(self) -> dict:
        return {"status": self.status, "reason_codes": list(self.reason_codes), "detail": self.detail}


@dataclass(frozen=True)
class TrustedExecution:
    """What trusted authority code observed about one execution. Building an attestation from it
    validates every field; the caller supplies observations, not conclusions."""

    claim_key: str
    obligation_profile_id: str
    subject: Mapping[str, Any]
    authority: Mapping[str, Any]
    environment: Mapping[str, Any]
    inputs: Sequence[Mapping[str, Any]]
    evidence: Sequence[Mapping[str, Any]]
    outcome: Mapping[str, Any]
    issuer: Mapping[str, Any]
    created_at: str
    live_observations: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class VerifiedAttestation:
    attestation_id: str
    claim_key: str
    obligation_profile_id: str
    subject: Mapping[str, Any]
    inputs: tuple[Mapping[str, Any], ...]
    dependency_digest: str
    outcome: Mapping[str, Any]
    record: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {"attestation_id": self.attestation_id, "claim_key": self.claim_key,
                "obligation_profile_id": self.obligation_profile_id, "dependency_digest": self.dependency_digest,
                "outcome": dict(self.outcome)}


def _text(value: Any, name: str, *, limit: int = 400) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AttestationRefused(f"{name} must be a non-empty string of at most {limit} characters")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise AttestationRefused(f"{name} must be a sha256 digest")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise AttestationRefused(f"{name} must be a git object id or sha256 digest")
    return value


def _count(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AttestationRefused(f"{name} must be a non-negative integer")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AttestationRefused(f"{name} must be an object")
    return value


def _sequence(value: Any, name: str) -> list:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AttestationRefused(f"{name} must be a list")
    if len(value) > MAX_ITEMS:
        raise AttestationRefused(f"{name} exceeds {MAX_ITEMS} entries")
    return list(value)


def _exact_keys(value: Mapping[str, Any], fields: Sequence[str], name: str) -> None:
    if set(value) != set(fields):
        raise AttestationRefused(f"{name} must contain exactly {', '.join(fields)}")


def _subject(raw: Any) -> dict:
    value = _mapping(raw, "subject")
    _exact_keys(value, SUBJECT_FIELDS, "subject")
    repository_id = value["repository_id"]
    if not isinstance(repository_id, int) or isinstance(repository_id, bool) or repository_id <= 0:
        raise AttestationRefused("subject.repository_id must be a positive integer")
    programme = value["programme_sha256"]
    return {
        "repository_id": repository_id,
        "base_commit": _digest(value["base_commit"], "subject.base_commit"),
        "candidate_commit": _digest(value["candidate_commit"], "subject.candidate_commit"),
        "candidate_tree": _digest(value["candidate_tree"], "subject.candidate_tree"),
        "programme_sha256": None if programme is None else _sha256(programme, "subject.programme_sha256"),
    }


def _authority(raw: Any) -> dict:
    value = _mapping(raw, "authority")
    _exact_keys(value, AUTHORITY_FIELDS, "authority")
    return {"id": _text(value["id"], "authority.id", limit=120),
            **{name: _sha256(value[name], f"authority.{name}") for name in AUTHORITY_FIELDS[1:]}}


def _environment(raw: Any) -> dict:
    value = _mapping(raw, "environment")
    _exact_keys(value, ENVIRONMENT_FIELDS, "environment")
    locks = _mapping(value["dependency_lock_digests"], "environment.dependency_lock_digests")
    if len(locks) > MAX_ITEMS:
        raise AttestationRefused("environment.dependency_lock_digests exceeds bound")
    return {
        "toolchain_digest": _sha256(value["toolchain_digest"], "environment.toolchain_digest"),
        "dependency_lock_digests": {_text(path, "lock path", limit=200): _sha256(digest, f"lock digest {path}")
                                    for path, digest in sorted(locks.items())},
        "runtime_image_digest": _sha256(value["runtime_image_digest"], "environment.runtime_image_digest"),
        "configuration_digest": _sha256(value["configuration_digest"], "environment.configuration_digest"),
    }


def _inputs(raw: Any) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for item in _sequence(raw, "inputs"):
        value = _mapping(item, "input")
        _exact_keys(value, ("kind", "identity", "digest", "coverage"), "input")
        kind, coverage = value["kind"], value["coverage"]
        if kind not in INPUT_KINDS:
            raise AttestationRefused(f"input kind {kind!r} is not a registered dependency class")
        if coverage not in COVERAGES:
            raise AttestationRefused(f"input coverage {coverage!r} is not registered")
        identity = value["identity"]
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise AttestationRefused("input identity is malformed")
        if identity in seen:
            raise AttestationRefused(f"input identity {identity!r} declared twice")
        seen.add(identity)
        rows.append({"kind": kind, "identity": identity, "digest": _digest(value["digest"], f"input {identity}"),
                     "coverage": coverage})
    return sorted(rows, key=lambda row: (row["kind"], row["identity"]))


def _evidence(raw: Any) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for item in _sequence(raw, "evidence"):
        value = _mapping(item, "evidence")
        _exact_keys(value, ("retained_object_id", "sha256", "media_type"), "evidence")
        object_id = value["retained_object_id"]
        if not isinstance(object_id, str) or not OBJECT_ID.fullmatch(object_id):
            raise AttestationRefused("evidence retained_object_id is malformed")
        if object_id in seen:
            raise AttestationRefused(f"evidence object {object_id!r} listed twice")
        seen.add(object_id)
        rows.append({"retained_object_id": object_id, "sha256": _sha256(value["sha256"], f"evidence {object_id}"),
                     "media_type": _text(value["media_type"], "evidence media_type", limit=120)})
    return sorted(rows, key=lambda row: row["retained_object_id"])


def _outcome(raw: Any) -> dict:
    value = _mapping(raw, "outcome")
    _exact_keys(value, ("verdict", "reason_codes"), "outcome")
    if value["verdict"] not in VERDICTS:
        raise AttestationRefused(f"outcome verdict {value['verdict']!r} is not registered")
    codes = sorted({_text(code, "reason code", limit=120) for code in _sequence(value["reason_codes"], "reason_codes")})
    return {"verdict": value["verdict"], "reason_codes": codes}


def _issuer(raw: Any) -> dict:
    value = _mapping(raw, "issuer")
    _exact_keys(value, ISSUER_FIELDS, "issuer")
    if value["platform"] not in PLATFORMS:
        raise AttestationRefused(f"issuer platform {value['platform']!r} is not registered")
    return {"platform": value["platform"], "workflow_id": _text(value["workflow_id"], "issuer.workflow_id"),
            "run_id": _count(value["run_id"], "issuer.run_id"), "attempt": _count(value["attempt"], "issuer.attempt"),
            "job_id": _text(value["job_id"], "issuer.job_id"),
            "source_revision": _digest(value["source_revision"], "issuer.source_revision")}


def _live(raw: Any) -> list[dict]:
    rows = []
    for item in _sequence(raw, "live_observations"):
        value = _mapping(item, "live observation")
        _exact_keys(value, LIVE_FIELDS, "live observation")
        rows.append({"observer_id": _text(value["observer_id"], "observer_id", limit=120),
                     "subject": _text(value["subject"], "live subject"),
                     "value_digest": _sha256(value["value_digest"], "live value_digest"),
                     "observed_at": _text(value["observed_at"], "observed_at", limit=64),
                     "validity_policy_id": _text(value["validity_policy_id"], "validity_policy_id", limit=120)})
    return sorted(rows, key=lambda row: (row["observer_id"], row["subject"]))


def attestation_body(execution: TrustedExecution) -> dict:
    """The canonical, fully validated body. `attestation_id` is its digest and nothing else."""
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "claim_key": _text(execution.claim_key, "claim_key", limit=200),
        "obligation_profile_id": _text(execution.obligation_profile_id, "obligation_profile_id", limit=120),
        "subject": _subject(execution.subject),
        "authority": _authority(execution.authority),
        "environment": _environment(execution.environment),
        "inputs": _inputs(execution.inputs),
        "evidence": _evidence(execution.evidence),
        "outcome": _outcome(execution.outcome),
        "issuer": _issuer(execution.issuer),
        "live_observations": _live(execution.live_observations),
        "created_at": _text(execution.created_at, "created_at", limit=64),
    }


def build_attestation(execution: TrustedExecution) -> dict:
    body = attestation_body(execution)
    return {**body, "attestation_id": sha256_value(body)}


def execution_from_record(record: Mapping[str, Any]) -> TrustedExecution:
    value = _mapping(record, "attestation")
    fields = ("schema", "schema_version", "claim_key", "obligation_profile_id", "subject", "authority", "environment",
              "inputs", "evidence", "outcome", "issuer", "live_observations", "created_at", "attestation_id")
    if set(value) != set(fields):
        raise AttestationRefused("attestation record has unexpected or missing fields")
    if value["schema"] != SCHEMA or value["schema_version"] != SCHEMA_VERSION:
        raise AttestationRefused("attestation schema is not supported")
    return TrustedExecution(
        claim_key=value["claim_key"], obligation_profile_id=value["obligation_profile_id"], subject=value["subject"],
        authority=value["authority"], environment=value["environment"], inputs=value["inputs"],
        evidence=value["evidence"], outcome=value["outcome"], issuer=value["issuer"],
        created_at=value["created_at"], live_observations=value["live_observations"])


def dependency_digest(inputs: Sequence[Mapping[str, Any]]) -> str:
    """Identity of the declared dependency closure: what a later currency check compares."""
    return sha256_value([{"kind": row["kind"], "identity": row["identity"], "digest": row["digest"],
                          "coverage": row["coverage"]} for row in inputs])


def verify_attestation(record: Mapping[str, Any], observed_issuer: Mapping[str, Any],
                       object_reader: Callable[[str], bytes | None],
                       authority_registry: Mapping[str, Mapping[str, str]]) -> VerifiedAttestation | Refusal:
    """Re-derive everything the record claims about itself. Order: shape → digest → issuer →
    authority → retained objects → dependency completeness. A refusal names the first class of
    failure and every affected identity of that class."""
    try:
        body = attestation_body(execution_from_record(record))
    except AttestationRefused as exc:
        return Refusal("rejected", ("record_invalid",), str(exc))
    if sha256_value(body) != record.get("attestation_id"):
        return Refusal("rejected", ("record_digest_mismatch",), "attestation_id is not the digest of the body")

    issuer = body["issuer"]
    try:
        observed = _issuer(observed_issuer)
    except AttestationRefused as exc:
        return Refusal("rejected", ("issuer_unobserved",), f"issuer observation is invalid: {exc}")
    if observed != issuer:
        differing = sorted(name for name in ISSUER_FIELDS if observed[name] != issuer[name])
        return Refusal("rejected", ("issuer_mismatch",), "issuer differs from the independent observation: " + ", ".join(differing))
    if issuer["platform"] not in AUTHENTICATED_PLATFORMS:
        return Refusal("rejected", ("issuer_unauthenticated",), f"issuer platform {issuer['platform']!r} cannot be authenticated")

    authority = body["authority"]
    registered = authority_registry.get(authority["id"]) if isinstance(authority_registry, Mapping) else None
    if not isinstance(registered, Mapping):
        return Refusal("rejected", ("authority_unknown",), f"authority {authority['id']!r} is not registered")
    drifted = sorted(name for name in AUTHORITY_FIELDS[1:] if registered.get(name) != authority[name])
    if drifted:
        return Refusal("rejected", ("authority_mismatch",), "authority identities differ from the registry: " + ", ".join(drifted))

    missing, tampered = [], []
    for row in body["evidence"]:
        data = object_reader(row["retained_object_id"])
        if data is None:
            missing.append(row["retained_object_id"])
        elif not isinstance(data, (bytes, bytearray)) or sha256_bytes(bytes(data)) != row["sha256"]:
            tampered.append(row["retained_object_id"])
    if tampered:
        return Refusal("rejected", ("artifact_tampered",), "retained objects differ from their attested digests: " + ", ".join(tampered))
    if missing:
        return Refusal("insufficient", ("artifact_missing",), "retained objects are unavailable: " + ", ".join(missing))

    kinds = {row["kind"] for row in body["inputs"]}
    absent = [kind for kind in REQUIRED_INPUT_KINDS if kind not in kinds]
    if absent:
        return Refusal("insufficient", ("dependency_class_missing",), "declared inputs lack dependency classes: " + ", ".join(absent))
    incomplete = [row["identity"] for row in body["inputs"] if row["coverage"] != "complete"]
    if incomplete:
        return Refusal("insufficient", ("dependency_coverage_unknown",), "declared inputs without complete coverage: " + ", ".join(incomplete))

    return VerifiedAttestation(
        attestation_id=str(record["attestation_id"]), claim_key=body["claim_key"],
        obligation_profile_id=body["obligation_profile_id"], subject=body["subject"],
        inputs=tuple(body["inputs"]), dependency_digest=dependency_digest(body["inputs"]),
        outcome=body["outcome"], record=body | {"attestation_id": str(record["attestation_id"])})
