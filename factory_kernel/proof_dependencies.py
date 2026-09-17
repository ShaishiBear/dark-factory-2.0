"""Dependency currency for verified attestations (SPECIFICATION 3.4, contract C04, WP05).

`assess_currency` never executes missing work. It answers one question about one already
verified attestation: given what trusted observers see now, is that judgement still about the
present? The order is fixed by C04: record validity → issuer and exact subject → retained
artifacts → protected dependency profile → required dependency values → live observation
validity → predecessor propagation. Verdicts are `current`, `stale`, `insufficient`, `rejected`.
Missing or unknown dependencies are insufficient; a mismatched known value is stale; forged
material is rejected and nothing after it is consulted. A failed evaluation can be a perfectly
current observation of failure; it still never satisfies a success obligation.

The dependency profile is protected policy (`.factory/authority-profiles.json`). A profile can
only be narrowed by a protected migration; unknown coverage forbids narrowing at assessment time.
`invalidated_frontier` walks explicit `depends_on` edges only, with a visited set, and refuses an
index that is incomplete or inconsistent: an invalid graph never yields an empty all-clear.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from .attestations import VerifiedAttestation, dependency_digest
from .canonical import sha256_value

PROFILE_SCHEMA = "dark-factory/authority-profiles"
PROFILE_SCHEMA_VERSION = "1.0"
COMPILER_VERSION = "1.0"
STATUSES = ("current", "stale", "insufficient", "rejected")
SEVERITY = {status: rank for rank, status in enumerate(STATUSES)}
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,119}")
MAX_ITEMS = 256

# Reason code → the currency class it implies. Rejection short-circuits; the others accumulate.
REASONS = {
    "record_invalid": "rejected", "issuer_invalid": "rejected", "subject_mismatch": "rejected",
    "profile_unknown": "rejected", "profile_mismatch": "rejected", "predecessor_rejected": "rejected",
    "artifact_missing": "insufficient", "dependency_missing": "insufficient",
    "dependency_coverage_unknown": "insufficient", "live_observation_missing": "insufficient",
    "predecessor_insufficient": "insufficient",
    "dependency_changed": "stale", "live_observation_expired": "stale", "predecessor_stale": "stale",
}


class ProfileRefused(ValueError):
    """The protected authority-profile policy is malformed or does not match the spine."""


class DependencyIndexInvalid(ValueError):
    """A dependency index that cannot be trusted to enumerate what depends on what."""


@dataclass(frozen=True)
class DependencyProfile:
    profile_id: str
    version: str
    required_identities: tuple[str, ...]
    live_observers: tuple[str, ...]
    narrowing_allowed: bool = False


@dataclass(frozen=True)
class AuthorityProfiles:
    compiler_version: str
    profiles: Mapping[str, DependencyProfile]
    obligations: Mapping[str, str]
    sha256: str

    def profile_for(self, obligation_id: str) -> DependencyProfile:
        profile_id = self.obligations.get(obligation_id)
        if profile_id is None:
            raise ProfileRefused(f"no dependency profile is registered for obligation {obligation_id!r}")
        return self.profiles[profile_id]


def _identities(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise ProfileRefused(f"{name} must be a bounded list")
    rows = []
    for item in raw:
        if not isinstance(item, str) or not IDENTITY.fullmatch(item):
            raise ProfileRefused(f"{name} contains a malformed identity")
        if item in rows:
            raise ProfileRefused(f"{name} repeats {item!r}")
        rows.append(item)
    return tuple(rows)


def load_profiles(path: str | Path, *, spine_claim_ids: Sequence[str] | None = None) -> AuthorityProfiles:
    """Strict loader. With `spine_claim_ids`, the obligation map must name exactly the protected
    spine's claims: the profile file cannot add an obligation the spine does not require, nor
    drop one it does (existing-policy equivalence, WP04)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileRefused(f"cannot read authority profiles: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != PROFILE_SCHEMA or raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProfileRefused("authority profiles schema is not supported")
    if set(raw) != {"schema", "schema_version", "compiler_version", "profiles", "obligations"}:
        raise ProfileRefused("authority profiles must contain exactly schema, schema_version, compiler_version, profiles, obligations")
    if raw["compiler_version"] != COMPILER_VERSION:
        raise ProfileRefused("authority profiles were written for another compiler version")
    profiles_raw, obligations_raw = raw["profiles"], raw["obligations"]
    if not isinstance(profiles_raw, dict) or not profiles_raw or len(profiles_raw) > MAX_ITEMS:
        raise ProfileRefused("authority profiles must define at least one bounded profile")
    profiles: dict[str, DependencyProfile] = {}
    for profile_id, entry in profiles_raw.items():
        if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id) or not isinstance(entry, dict):
            raise ProfileRefused("authority profile ids and entries are malformed")
        if set(entry) != {"version", "required_identities", "live_observers", "narrowing_allowed"}:
            raise ProfileRefused(f"profile {profile_id!r} must contain exactly version, required_identities, live_observers, narrowing_allowed")
        if not isinstance(entry["version"], str) or not entry["version"].strip():
            raise ProfileRefused(f"profile {profile_id!r} version is malformed")
        if entry["narrowing_allowed"] is not False:
            raise ProfileRefused(f"profile {profile_id!r} may not permit narrowing; only a protected migration narrows a profile")
        required = _identities(entry["required_identities"], f"profile {profile_id!r} required_identities")
        if not required:
            raise ProfileRefused(f"profile {profile_id!r} requires no dependencies; an empty closure is not a profile")
        profiles[profile_id] = DependencyProfile(
            profile_id=profile_id, version=entry["version"], required_identities=required,
            live_observers=_identities(entry["live_observers"], f"profile {profile_id!r} live_observers"),
            narrowing_allowed=False)
    if not isinstance(obligations_raw, dict) or len(obligations_raw) > MAX_ITEMS:
        raise ProfileRefused("authority profile obligations must be a bounded object")
    obligations: dict[str, str] = {}
    for obligation_id, profile_id in obligations_raw.items():
        if not isinstance(obligation_id, str) or not obligation_id.strip():
            raise ProfileRefused("obligation ids must be non-empty strings")
        if profile_id not in profiles:
            raise ProfileRefused(f"obligation {obligation_id!r} names an unregistered profile {profile_id!r}")
        obligations[obligation_id] = profile_id
    if spine_claim_ids is not None and set(obligations) != set(spine_claim_ids):
        raise ProfileRefused("authority profile obligations do not match the protected evidence-spine claims exactly")
    return AuthorityProfiles(compiler_version=COMPILER_VERSION, profiles=profiles, obligations=obligations,
                             sha256=sha256_value(raw))


@dataclass(frozen=True)
class DependencySet:
    profile_id: str
    profile_version: str
    compiler_version: str
    identities: Mapping[str, str]
    missing: tuple[str, ...]

    @property
    def coverage(self) -> str:
        return "complete" if not self.missing else "unknown"

    def sha256(self) -> str:
        return sha256_value({"profile_id": self.profile_id, "profile_version": self.profile_version,
                             "compiler_version": self.compiler_version, "identities": dict(sorted(self.identities.items())),
                             "missing": list(self.missing)})

    def to_dict(self) -> dict:
        return {"profile_id": self.profile_id, "profile_version": self.profile_version,
                "compiler_version": self.compiler_version, "identities": dict(sorted(self.identities.items())),
                "missing": list(self.missing), "coverage": self.coverage, "sha256": self.sha256()}


def collect_dependencies(authority_id: str, subject: Mapping[str, Any],
                         trusted_reader: Callable[[str], str | None], *, profile: DependencyProfile) -> DependencySet:
    """Observe every identity the profile requires. `subject` supplies exact-tree identities;
    `trusted_reader` supplies the rest (authority closure, policy, toolchain, locks). An identity
    the reader cannot observe is recorded as missing: coverage becomes unknown, never assumed."""
    if not isinstance(authority_id, str) or not authority_id.strip():
        raise ValueError("authority_id must be non-empty")
    identities: dict[str, str] = {}
    missing: list[str] = []
    for identity in profile.required_identities:
        value = subject.get(identity) if isinstance(subject, Mapping) and identity in subject else trusted_reader(identity)
        if isinstance(value, str) and value.strip():
            identities[identity] = value
        else:
            missing.append(identity)
    return DependencySet(profile_id=profile.profile_id, profile_version=profile.version,
                         compiler_version=COMPILER_VERSION, identities=identities, missing=tuple(missing))


@dataclass(frozen=True)
class CurrencyResult:
    status: str
    reason_codes: tuple[str, ...]
    affected: tuple[str, ...]
    satisfies_obligation: bool
    detail: str

    def to_dict(self) -> dict:
        return {"status": self.status, "reason_codes": list(self.reason_codes), "affected": list(self.affected),
                "satisfies_obligation": self.satisfies_obligation, "detail": self.detail}


def _required_inputs(attestation: Any) -> tuple[dict, str | None, str | None]:
    """The declared dependencies of the attestation under assessment, its obligation profile and
    its verdict. Accepts a VerifiedAttestation or a mapping shaped like one."""
    if isinstance(attestation, VerifiedAttestation):
        rows, profile_id, verdict = attestation.inputs, attestation.obligation_profile_id, attestation.outcome.get("verdict")
    elif isinstance(attestation, Mapping):
        rows = attestation.get("inputs")
        profile_id = attestation.get("obligation_profile_id")
        outcome = attestation.get("outcome")
        verdict = outcome.get("verdict") if isinstance(outcome, Mapping) else None
    else:
        raise ValueError("attestation must be a VerifiedAttestation or mapping")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("attestation inputs must be a list")
    required: dict = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("attestation input must be an object")
        identity, digest = row.get("identity"), row.get("digest")
        if not isinstance(identity, str) or not identity or not isinstance(digest, str) or not digest:
            raise ValueError("attestation input needs identity and digest")
        required[identity] = {"digest": digest, "coverage": row.get("coverage", "complete")}
    return required, (profile_id if isinstance(profile_id, str) else None), (verdict if isinstance(verdict, str) else None)


def _status_of(codes: Sequence[str]) -> str:
    return max((REASONS[code] for code in codes), key=SEVERITY.__getitem__, default="current")


def assess_currency(attestation: Any, current_observations: Mapping[str, Any],
                    profile: DependencyProfile | None) -> CurrencyResult:
    """See the module docstring for the order. `current_observations` carries observer results,
    never conclusions: `record_valid`, `issuer_valid`, `subject_match`, `retained` (bool or a
    mapping object id → bool), `dependencies` (identity → digest now), `coverage`
    (`complete`|`unknown` for the observer's own analysis), `live` (observer id → {"valid": bool}),
    `predecessors` (attestation id → status)."""
    required, declared_profile, verdict = _required_inputs(attestation)
    observations = current_observations if isinstance(current_observations, Mapping) else {}
    codes: list[str] = []
    affected: list[str] = []
    details: list[str] = []

    def finish() -> CurrencyResult:
        status = _status_of(codes)
        return CurrencyResult(status=status, reason_codes=tuple(sorted(set(codes))), affected=tuple(sorted(set(affected))),
                              satisfies_obligation=status == "current" and verdict == "pass", detail="; ".join(details))

    if observations.get("record_valid", True) is not True:
        codes.append("record_invalid"); details.append("record shape or digest failed verification")
        return finish()
    if observations.get("issuer_valid") is not True:
        codes.append("issuer_invalid"); details.append("issuer could not be authenticated independently")
        return finish()
    if observations.get("subject_match", True) is not True:
        codes.append("subject_mismatch"); details.append("attestation subject is not the exact subject under query")
        return finish()

    retained = observations.get("retained")
    if isinstance(retained, Mapping):
        gone = sorted(str(key) for key, present in retained.items() if present is not True)
        if gone:
            codes.append("artifact_missing"); affected.extend(gone); details.append("retained artifacts unavailable: " + ", ".join(gone))
    elif retained is not True:
        codes.append("artifact_missing"); details.append("required retained artifacts are not all available")

    if profile is None:
        codes.append("profile_unknown"); details.append("dependency profile is not registered")
        return finish()
    if declared_profile is not None and declared_profile != profile.profile_id:
        codes.append("profile_mismatch"); details.append(f"attestation declares profile {declared_profile!r}, assessed under {profile.profile_id!r}")
        return finish()

    observed = observations.get("dependencies")
    observed = observed if isinstance(observed, Mapping) else {}
    for identity in sorted(set(profile.required_identities) | set(required)):
        now = observed.get(identity)
        then = required.get(identity)
        if then is None or not isinstance(now, str) or not now:
            codes.append("dependency_missing"); affected.append(identity)
        elif then["digest"] != now:
            codes.append("dependency_changed"); affected.append(identity)
    if any(row["coverage"] != "complete" for row in required.values()) or observations.get("coverage", "complete") != "complete":
        codes.append("dependency_coverage_unknown"); details.append("dependency analysis coverage is not complete; narrowing is forbidden")

    live = observations.get("live")
    live = live if isinstance(live, Mapping) else {}
    for observer in profile.live_observers:
        result = live.get(observer)
        if not isinstance(result, Mapping):
            codes.append("live_observation_missing"); affected.append(observer)
        elif result.get("valid") is not True:
            codes.append("live_observation_expired"); affected.append(observer)

    predecessors = observations.get("predecessors")
    predecessors = predecessors if isinstance(predecessors, Mapping) else {}
    for predecessor, status in sorted(predecessors.items()):
        if status == "rejected":
            codes.append("predecessor_rejected"); affected.append(str(predecessor))
        elif status == "insufficient":
            codes.append("predecessor_insufficient"); affected.append(str(predecessor))
        elif status == "stale":
            codes.append("predecessor_stale"); affected.append(str(predecessor))
        elif status != "current":
            codes.append("predecessor_insufficient"); affected.append(str(predecessor))
    if affected and not details:
        details.append("affected identities: " + ", ".join(sorted(set(affected))))
    return finish()


def build_dependency_index(verified: Mapping[str, Any], edges: Iterable[Mapping[str, str]]) -> dict:
    """A complete index over verified attestations: identity → attestation ids, plus typed edges
    (source depends on target). Only `depends_on` edges carry proof dependence."""
    identities: dict[str, list[str]] = {}
    for attestation_id, item in sorted(verified.items()):
        for row in _inputs_of(item):
            identities.setdefault(row["identity"], []).append(attestation_id)
    rows = []
    for edge in edges:
        rows.append({"source": str(edge["source"]), "target": str(edge["target"]), "kind": str(edge["kind"])})
    return {"version": "1.0", "complete": True, "identities": {key: sorted(value) for key, value in sorted(identities.items())},
            "edges": sorted(rows, key=lambda row: (row["source"], row["target"], row["kind"]))}


def _inputs_of(item: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(item, VerifiedAttestation):
        return item.inputs
    if isinstance(item, Mapping) and isinstance(item.get("inputs"), Sequence):
        return item["inputs"]
    raise DependencyIndexInvalid("verified attestations must carry their declared inputs")


def invalidated_frontier(changes: Iterable[str], verified_attestations: Mapping[str, Any],
                         dependency_index: Mapping[str, Any]) -> tuple[str, ...]:
    """Attestation ids invalidated by the changed dependency identities, transitively along
    `depends_on` edges. Raises DependencyIndexInvalid rather than returning an all-clear when the
    index is incomplete or disagrees with the attestations."""
    changed = {str(item) for item in (changes.keys() if isinstance(changes, Mapping) else changes)}
    if not isinstance(dependency_index, Mapping) or dependency_index.get("version") != "1.0" or dependency_index.get("complete") is not True:
        raise DependencyIndexInvalid("dependency index is missing, unversioned or incomplete")
    identities, edges = dependency_index.get("identities"), dependency_index.get("edges")
    if not isinstance(identities, Mapping) or not isinstance(edges, Sequence) or isinstance(edges, (str, bytes)):
        raise DependencyIndexInvalid("dependency index segments are missing")
    declared: dict[str, set[str]] = {aid: {row["identity"] for row in _inputs_of(item)} for aid, item in verified_attestations.items()}
    for identity, ids in identities.items():
        if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
            raise DependencyIndexInvalid("identity segment is malformed")
        for aid in ids:
            if aid not in declared or identity not in declared[aid]:
                raise DependencyIndexInvalid(f"index attributes {identity!r} to an attestation that does not declare it")
    for aid, names in declared.items():
        for identity in names:
            if aid not in (identities.get(identity) or ()):
                raise DependencyIndexInvalid(f"index lacks the segment for {identity!r} of {aid}")
    dependents: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping) or edge.get("source") not in declared or edge.get("target") not in declared:
            raise DependencyIndexInvalid("dependency edge references an unknown attestation")
        if not isinstance(edge.get("kind"), str) or not edge["kind"]:
            raise DependencyIndexInvalid("dependency edge lacks a relation kind")
        if edge["kind"] == "depends_on":
            dependents.setdefault(edge["target"], []).append(edge["source"])
    frontier: set[str] = set()
    queue = [aid for identity in sorted(changed) for aid in identities.get(identity, ())]
    while queue:
        node = queue.pop()
        if node in frontier:
            continue
        frontier.add(node)
        queue.extend(dependents.get(node, ()))
    return tuple(sorted(frontier))


__all__ = ["AuthorityProfiles", "CurrencyResult", "DependencyIndexInvalid", "DependencyProfile", "DependencySet",
           "ProfileRefused", "assess_currency", "build_dependency_index", "collect_dependencies", "dependency_digest",
           "invalidated_frontier", "load_profiles"]
