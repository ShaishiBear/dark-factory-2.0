"""Capabilities: deterministic grants for the fixed effects (SPECIFICATION 5, WP06).

`authorize(request, subject, policy, verified_evidence, lease, budget, *, now)` turns one
request for one semantic operation on one exact subject into a `Grant` or a `Refusal`, with no
I/O and no clock of its own. A grant names the operation, the exact subject (repository, PR,
head, base, tree, evidence digest), the resources it may touch, the caller's role and instance,
the source and policy digests it was judged under, the lease bundle it rides on (or the
recorded reason there is none), its maximum uses, expiry and revocation epoch. There is no
capability for an arbitrary argv or an arbitrary API URL: the operations are the fixed names
below and nothing else.

What a grant is NOT: it is not authority on its own. The broker (`effect_broker.py`)
re-observes the subject and the controls before spending, and the existing merge authority
(`harness/merge_verify.py` pre/post) still judges the merge. A grant is the durable, checkable
statement of WHAT was authorised for WHOM, so that a lie about the subject, an expired or
replayed grant, or a wrong role refuses before any credential is touched.

The policy is a code constant reviewed under the trust root (roles, operations, grant lifetime,
and whether the legacy serial route may run without a lease bundle). It is an input, not a
discovery: change it here, with its tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping

from .canonical import sha256_value

FIXED_OPERATIONS = ("commit_acceptance", "commit_implementation", "publish_candidate", "merge_exact_head",
                    "publish_transition_data", "observe")
ROLES = ("merge-executor", "build-executor", "transition-service", "observer")
OID = re.compile(r"[0-9a-f]{40,64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")
SUBJECT_FIELDS = ("repository", "pr_number", "head_sha", "base_sha", "head_tree_sha", "evidence_sha256")

# The preregistered policy. `legacy_serial_route` says a grant may be issued without a lease
# bundle while the coordinator (WP07) does not exist; the grant records that it rode the legacy
# serial route (the workflow concurrency group) so nobody reads it as lease-backed later.
DEFAULT_POLICY: Mapping[str, Any] = {
    "schema": "dark-factory/capability-policy",
    "schema_version": "1.0",
    "roles": {
        "merge-executor": ("merge_exact_head", "observe"),
        "build-executor": ("commit_acceptance", "commit_implementation", "publish_candidate", "observe"),
        "transition-service": ("publish_transition_data", "observe"),
        "observer": ("observe",),
    },
    "grant_ttl_seconds": 900,     # below GitHubClient.DEFAULT_IDENTITY_MAX_AGE_SECONDS (1200): a grant never outlives the identity
    "max_uses": 1,
    "legacy_serial_route": True,
    "revocation_epoch": "epoch-0",
}


class CapabilityRefused(ValueError):
    pass


@dataclass(frozen=True)
class Refusal:
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"status": "refused", "reason_codes": list(self.reason_codes)}


@dataclass(frozen=True)
class Grant:
    grant_id: str
    semantic_operation: str
    subject: Mapping[str, Any]
    resources: tuple[str, ...]
    caller_role: str
    caller_instance: str
    request_id: str
    request_sha256: str
    source_sha: str
    policy_sha256: str
    evidence_sha256: str
    lease: Mapping[str, Any] | None
    lease_route: str
    max_uses: int
    issued_at: int
    expires_at: int
    revocation_epoch: str
    narrowed_from: str | None = None
    schema: str = field(default="dark-factory/capability-grant")
    schema_version: str = field(default="1.0")

    def to_dict(self) -> dict:
        return {"schema": self.schema, "schema_version": self.schema_version, "grant_id": self.grant_id,
                "semantic_operation": self.semantic_operation, "subject": dict(self.subject), "resources": list(self.resources),
                "caller_role": self.caller_role, "caller_instance": self.caller_instance, "request_id": self.request_id,
                "request_sha256": self.request_sha256, "source_sha": self.source_sha, "policy_sha256": self.policy_sha256,
                "evidence_sha256": self.evidence_sha256, "lease": dict(self.lease) if self.lease is not None else None,
                "lease_route": self.lease_route, "max_uses": self.max_uses, "issued_at": self.issued_at,
                "expires_at": self.expires_at, "revocation_epoch": self.revocation_epoch, "narrowed_from": self.narrowed_from,
                "authority": "grant-record-only"}


def policy_digest(policy: Mapping[str, Any]) -> str:
    return sha256_value({k: (dict(v) if isinstance(v, Mapping) else v) for k, v in policy.items()})


def _subject(value: Any) -> dict | None:
    if not isinstance(value, Mapping) or set(value) != set(SUBJECT_FIELDS):
        return None
    if not isinstance(value["repository"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]):
        return None
    if type(value["pr_number"]) is not int or value["pr_number"] <= 0:
        return None
    for key in ("head_sha", "base_sha", "head_tree_sha"):
        if not isinstance(value[key], str) or not OID.fullmatch(value[key]):
            return None
    if not isinstance(value["evidence_sha256"], str) or not SHA256.fullmatch(value["evidence_sha256"]):
        return None
    return {k: value[k] for k in SUBJECT_FIELDS}


def resources_for(operation: str, subject: Mapping[str, Any]) -> tuple[str, ...]:
    """The resources a fixed operation touches, derived from the subject, never supplied."""
    pr = f"pr:{subject['repository']}#{subject['pr_number']}"
    base = f"branch:{subject['repository']}:base:{subject['base_sha']}"
    if operation == "merge_exact_head":
        return (pr, base)
    if operation in ("publish_candidate", "commit_acceptance", "commit_implementation"):
        return (pr,)
    if operation == "publish_transition_data":
        return (base,)
    return ()


def authorize(request: Mapping[str, Any], subject: Mapping[str, Any], policy: Mapping[str, Any], verified_evidence: Mapping[str, Any],
              lease: Mapping[str, Any] | None, budget: Mapping[str, Any] | None, *, now: int) -> Grant | Refusal:
    """Deterministic. Every failing gate is listed; an unknown value fails closed.

    `request`: {"operation", "caller_role", "caller_instance", "request_id", "source_sha"}.
    `verified_evidence`: the authority's own record for this subject (for a merge, the
    `merge-authorization.json` written by `harness/merge_verify.py pre`); its identity fields
    must equal the subject's, or the caller is lying about what was judged.
    `lease`: a lease bundle dict (`lease_store.LeaseBundle.to_dict()`) or None; None is
    permitted only when the policy allows the legacy serial route, and the grant says so.
    `budget`: None for effects that spend no metered call; a dict names the reservation.
    """
    reasons: list[str] = []
    operation = request.get("operation") if isinstance(request, Mapping) else None
    if operation not in FIXED_OPERATIONS:
        reasons.append("operation_not_fixed")
    role = request.get("caller_role") if isinstance(request, Mapping) else None
    roles = policy.get("roles", {}) if isinstance(policy, Mapping) else {}
    if role not in ROLES or role not in roles:
        reasons.append("role_unknown")
    elif operation in FIXED_OPERATIONS and operation not in tuple(roles[role]):
        reasons.append("role_not_permitted")
    instance = request.get("caller_instance") if isinstance(request, Mapping) else None
    if not isinstance(instance, str) or not IDENT.fullmatch(instance):
        reasons.append("caller_instance_invalid")
    request_id = request.get("request_id") if isinstance(request, Mapping) else None
    if not isinstance(request_id, str) or not IDENT.fullmatch(request_id):
        reasons.append("request_id_invalid")
    source_sha = request.get("source_sha") if isinstance(request, Mapping) else None
    if not isinstance(source_sha, str) or not OID.fullmatch(source_sha):
        reasons.append("source_sha_invalid")
    exact = _subject(subject)
    if exact is None:
        reasons.append("subject_inexact")
    else:
        if not isinstance(verified_evidence, Mapping):
            reasons.append("evidence_missing")
        else:
            for key, evidence_key in (("head_sha", "head_sha"), ("base_sha", "base_sha"), ("head_tree_sha", "head_tree_sha"),
                                      ("evidence_sha256", "evidence_sha256")):
                if verified_evidence.get(evidence_key) != exact[key]:
                    reasons.append(f"evidence_{key}_mismatch")
    ttl = policy.get("grant_ttl_seconds") if isinstance(policy, Mapping) else None
    if type(ttl) is not int or ttl <= 0:
        reasons.append("policy_ttl_invalid")
    max_uses = policy.get("max_uses") if isinstance(policy, Mapping) else None
    if type(max_uses) is not int or max_uses < 1:
        reasons.append("policy_max_uses_invalid")
    epoch = policy.get("revocation_epoch") if isinstance(policy, Mapping) else None
    if not isinstance(epoch, str) or not epoch:
        reasons.append("policy_epoch_invalid")
    if type(now) is not int or now < 0:
        reasons.append("clock_invalid")
    if lease is None:
        route = "legacy-serial-route"
        if not (isinstance(policy, Mapping) and policy.get("legacy_serial_route") is True):
            reasons.append("lease_required")
    else:
        route = "lease-bundle"
        if not isinstance(lease, Mapping) or not isinstance(lease.get("lease_id"), str) or not isinstance(lease.get("epoch"), str) \
                or not isinstance(lease.get("resources"), (list, tuple)) or not lease["resources"]:
            reasons.append("lease_malformed")
        elif exact is not None and operation in FIXED_OPERATIONS \
                and not set(resources_for(operation, exact)).issubset(set(lease["resources"])):
            reasons.append("lease_resources_insufficient")
    if budget is not None and (not isinstance(budget, Mapping) or not isinstance(budget.get("bundle_id"), str)):
        reasons.append("budget_malformed")
    if reasons:
        return Refusal(tuple(reasons))
    assert exact is not None
    resources = resources_for(operation, exact)
    request_sha = sha256_value({"operation": operation, "subject": exact, "caller_role": role, "caller_instance": instance,
                                "request_id": request_id, "source_sha": source_sha})
    digest = policy_digest(policy)
    grant_id = sha256_value({"request_sha256": request_sha, "policy_sha256": digest, "issued_at": now, "epoch": epoch,
                             "lease": dict(lease) if lease is not None else None})[:32]
    return Grant(grant_id=grant_id, semantic_operation=operation, subject=exact, resources=resources, caller_role=role,
                 caller_instance=instance, request_id=request_id, request_sha256=request_sha, source_sha=source_sha,
                 policy_sha256=digest, evidence_sha256=exact["evidence_sha256"], lease=dict(lease) if lease is not None else None,
                 lease_route=route, max_uses=max_uses, issued_at=now, expires_at=now + ttl, revocation_epoch=epoch)


def validate_grant(grant: Grant, *, now: int, epoch: str, uses: int, operation: str | None = None,
                   subject: Mapping[str, Any] | None = None, policy: Mapping[str, Any] | None = None) -> Refusal | None:
    """The gates a broker checks before spending: expiry, revocation epoch, uses, and (when
    given) that the operation, the live subject and the policy digest are the ones granted.
    Returns None when every gate holds, else the Refusal listing all that failed."""
    reasons: list[str] = []
    if not isinstance(grant, Grant):
        return Refusal(("grant_malformed",))
    if type(now) is not int or now < 0:
        reasons.append("clock_invalid")
    elif now >= grant.expires_at:
        reasons.append("grant_expired")
    if epoch != grant.revocation_epoch:
        reasons.append("grant_revoked_by_epoch")
    if type(uses) is not int or uses < 0:
        reasons.append("uses_invalid")
    elif uses >= grant.max_uses:
        reasons.append("grant_uses_exhausted")
    if operation is not None and operation != grant.semantic_operation:
        reasons.append("operation_mismatch")
    if subject is not None:
        exact = _subject(subject)
        if exact is None or exact != dict(grant.subject):
            reasons.append("subject_mismatch")
    if policy is not None and policy_digest(policy) != grant.policy_sha256:
        reasons.append("policy_changed")
    return Refusal(tuple(reasons)) if reasons else None


def narrow_grant(grant: Grant, *, resources: tuple[str, ...] | list[str]) -> Grant:
    """A grant over a subset of the resources, same identity and expiry; never wider."""
    wanted = tuple(resources)
    if not wanted or not set(wanted).issubset(set(grant.resources)) or len(set(wanted)) != len(wanted):
        raise CapabilityRefused("a grant can only be narrowed to a nonempty subset of its resources")
    if wanted == grant.resources:
        return grant
    narrowed_id = sha256_value({"narrowed_from": grant.grant_id, "resources": list(wanted)})[:32]
    return replace(grant, grant_id=narrowed_id, resources=wanted, narrowed_from=grant.grant_id)


def grant_from_dict(value: Mapping[str, Any]) -> Grant:
    """Rehydrate a recorded grant (the broker journals grants as dicts)."""
    if not isinstance(value, Mapping) or value.get("schema") != "dark-factory/capability-grant":
        raise CapabilityRefused("not a capability grant record")
    try:
        return Grant(grant_id=str(value["grant_id"]), semantic_operation=str(value["semantic_operation"]), subject=dict(value["subject"]),
                     resources=tuple(value["resources"]), caller_role=str(value["caller_role"]), caller_instance=str(value["caller_instance"]),
                     request_id=str(value["request_id"]), request_sha256=str(value["request_sha256"]), source_sha=str(value["source_sha"]),
                     policy_sha256=str(value["policy_sha256"]), evidence_sha256=str(value["evidence_sha256"]),
                     lease=dict(value["lease"]) if value.get("lease") is not None else None, lease_route=str(value["lease_route"]),
                     max_uses=int(value["max_uses"]), issued_at=int(value["issued_at"]), expires_at=int(value["expires_at"]),
                     revocation_epoch=str(value["revocation_epoch"]), narrowed_from=value.get("narrowed_from"))
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityRefused(f"malformed grant record: {exc}") from exc


__all__ = ["DEFAULT_POLICY", "FIXED_OPERATIONS", "ROLES", "CapabilityRefused", "Grant", "Refusal", "authorize", "grant_from_dict",
           "narrow_grant", "policy_digest", "resources_for", "validate_grant"]
