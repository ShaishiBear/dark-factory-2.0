"""Claims: desired properties, exploratory assumptions, proof obligations and their identities.

Section 3.3 and C03. A requirement claim is the owner's approved acceptance criterion with a
stable key derived from `(repository_id, project, spec_sha256, acceptance_id)` under its own
schema namespace: the same wording in another scope is a different claim. Requirement
identities exist before any programme or strategy, so programme compilation can bind to
them without a bootstrap cycle. Proof obligations derive from the protected spine policy; a
model cannot invent a weaker authority profile. Implementation bindings proposed by a model
remain proposed until an observer verifies the exact relationship it can actually establish.

Nothing here is a second UNKNOWN->PROVEN state machine. `claim_status` is a derived query over
approval, observations, attestations, their currency and supersession, and keeps those axes
separate. Owner approval establishes intended meaning, never technical truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

from .canonical import sha256_value
from .spine import SpinePolicy

REQUIREMENT_KEY_SCHEMA = "dark-factory/requirement-key"
EXPLORATORY_KEY_SCHEMA = "dark-factory/exploratory-claim-key"
OBLIGATION_KEY_SCHEMA = "dark-factory/proof-obligation-key"
IMPLEMENTATION_KEY_SCHEMA = "dark-factory/implementation-claim-key"
SCHEMA_VERSION = "1.0"
KINDS = ("requirement", "assumption", "implementation-property", "proof-obligation")
RELATIONS = ("refines", "implements", "supported_by", "depends_on")
BINDING_RELATIONS = ("proposed_implementation", "observed_implementation", "depends_on", "measured_at", "counterexample_at")
OBSERVED_RELATIONS = frozenset({"observed_implementation", "measured_at", "counterexample_at"})


class ClaimRefused(ValueError):
    pass


@dataclass(frozen=True)
class ClaimDefinition:
    key: str
    kind: Literal["requirement", "assumption", "implementation-property", "proof-obligation"]
    project: str
    spec_sha256: str
    statement: str
    source_ref: str
    depends_on: tuple[str, ...]
    acceptance_ids: tuple[str, ...]
    authority_profile: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "project": self.project, "spec_sha256": self.spec_sha256,
                "statement": self.statement, "source_ref": self.source_ref, "depends_on": list(self.depends_on),
                "acceptance_ids": list(self.acceptance_ids), "authority_profile": self.authority_profile}


@dataclass(frozen=True)
class Edge:
    relation: str
    source: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {"relation": self.relation, "source": self.source, "target": self.target}


@dataclass(frozen=True)
class RequirementClaimSet:
    repository_id: str
    project: str
    spec_sha256: str
    claims: tuple[ClaimDefinition, ...]

    @property
    def keys(self) -> dict[str, str]:
        return {claim.acceptance_ids[0]: claim.key for claim in self.claims}

    def digest(self) -> str:
        return sha256_value({"schema": REQUIREMENT_KEY_SCHEMA, "schema_version": SCHEMA_VERSION,
                             "claims": [claim.to_dict() for claim in self.claims]})


@dataclass(frozen=True)
class ExploratoryClaimSet:
    claims: tuple[ClaimDefinition, ...]
    edges: tuple[Edge, ...]
    statuses: Mapping[str, str]


@dataclass(frozen=True)
class ClaimSet:
    repository_id: str
    project: str
    spec_sha256: str
    programme_sha256: str
    claims: tuple[ClaimDefinition, ...]
    edges: tuple[Edge, ...]
    item_obligations: Mapping[str, tuple[str, ...]]  # item id -> obligation keys
    item_requirements: Mapping[str, tuple[str, ...]]  # item id -> requirement keys

    def by_key(self) -> dict[str, ClaimDefinition]:
        return {claim.key: claim for claim in self.claims}

    def digest(self) -> str:
        return sha256_value({"schema": "dark-factory/claim-set", "schema_version": SCHEMA_VERSION,
                             "repository_id": self.repository_id, "project": self.project,
                             "spec_sha256": self.spec_sha256, "programme_sha256": self.programme_sha256,
                             "claims": [c.to_dict() for c in self.claims], "edges": [e.to_dict() for e in self.edges]})


@dataclass(frozen=True)
class ClaimBinding:
    claim_key: str
    subject_identity: str
    relation: str
    source: str  # "model" | "author" | "observer:<id>"
    method: str
    coverage: str  # complete-for-profile | partial | unknown
    standing: str  # proposed | observed

    def to_dict(self) -> dict[str, str]:
        return {"claim_key": self.claim_key, "subject_identity": self.subject_identity, "relation": self.relation,
                "source": self.source, "method": self.method, "coverage": self.coverage, "standing": self.standing}


@dataclass(frozen=True)
class ClaimBindings:
    bindings: tuple[ClaimBinding, ...]
    refused: tuple[dict[str, str], ...] = ()

    def for_claim(self, key: str) -> tuple[ClaimBinding, ...]:
        return tuple(b for b in self.bindings if b.claim_key == key)


# ---------- identities ----------

def requirement_key(*, repository_id: str, project: str, spec_sha256: str, acceptance_id: str) -> str:
    return sha256_value({"schema": REQUIREMENT_KEY_SCHEMA, "schema_version": SCHEMA_VERSION,
                         "repository_id": str(repository_id), "project": project,
                         "spec_sha256": spec_sha256, "acceptance_id": acceptance_id})


def _hex(value: Any, name: str) -> str:
    import re
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ClaimRefused(f"{name} must be a SHA-256 hex digest")
    return value


# ---------- compilation ----------

def compile_requirement_claims(approved_spec: Mapping[str, Any], *, repository_id: str, project: str,
                               approval: Mapping[str, Any] | None = None) -> RequirementClaimSet:
    """Stable requirement identities from the approved spec, sorted by acceptance ID.

    `approved_spec` is a compiled spec (programme.compile_spec output). When `approval` is
    given it must be an IntentStore approval record naming the configured owner as actor with
    role `owner` and the same spec hash; approval is a recorded owner decision, never a boolean
    a model supplies.
    """
    if not isinstance(approved_spec, Mapping) or "requirements" not in approved_spec:
        raise ClaimRefused("approved spec must be a compiled specification")
    spec_sha256 = sha256_value(approved_spec)
    if approval is not None:
        actor = approval.get("actor") if isinstance(approval, Mapping) else None
        if (not isinstance(actor, Mapping) or actor.get("role") != "owner" or not actor.get("identity")
                or approval.get("spec_sha256") != spec_sha256 or approval.get("spec") != approved_spec):
            raise ClaimRefused("approval must be the owner's recorded decision for exactly this spec")
    rows = []
    seen = set()
    for req in approved_spec["requirements"]:
        for ac in req["acceptance"]:
            if ac["id"] in seen:
                raise ClaimRefused("duplicate acceptance ID")
            seen.add(ac["id"])
            rows.append((ac["id"], req["id"], ac["text"]))
    claims = tuple(
        ClaimDefinition(
            key=requirement_key(repository_id=repository_id, project=project, spec_sha256=spec_sha256, acceptance_id=ac_id),
            kind="requirement", project=project, spec_sha256=spec_sha256, statement=text,
            source_ref=f"spec:{spec_sha256}#requirements/{req_id}/acceptance/{ac_id}",
            depends_on=(), acceptance_ids=(ac_id,), authority_profile=None)
        for ac_id, req_id, text in sorted(rows))
    return RequirementClaimSet(str(repository_id), project, spec_sha256, claims)


def compile_exploratory_claims(requirements: RequirementClaimSet, recorded_hypotheses: Mapping[str, Mapping[str, Any]]) -> ExploratoryClaimSet:
    """Assumptions recorded by exploration (exploration_records projection `claims`), bound to
    the requirement keys they name. Their existing status/history is retained, not re-judged."""
    keys = requirements.keys
    claims: list[ClaimDefinition] = []
    edges: list[Edge] = []
    statuses: dict[str, str] = {}
    key_of: dict[str, str] = {}
    for hypothesis_id in sorted(recorded_hypotheses):
        row = recorded_hypotheses[hypothesis_id]
        if row.get("spec_sha256") != requirements.spec_sha256:
            raise ClaimRefused(f"hypothesis {hypothesis_id!r} belongs to another spec")
        key_of[hypothesis_id] = sha256_value({"schema": EXPLORATORY_KEY_SCHEMA, "schema_version": SCHEMA_VERSION,
                                              "repository_id": requirements.repository_id, "project": requirements.project,
                                              "spec_sha256": requirements.spec_sha256, "hypothesis_id": hypothesis_id})
    for hypothesis_id in sorted(recorded_hypotheses):
        row = recorded_hypotheses[hypothesis_id]
        acceptance = tuple(sorted(row.get("acceptance", ())))
        if not acceptance or any(ac not in keys for ac in acceptance):
            raise ClaimRefused(f"hypothesis {hypothesis_id!r} must name approved acceptance criteria")
        dependencies = tuple(sorted(row.get("depends_on", ())))
        if any(dep not in key_of for dep in dependencies):
            raise ClaimRefused(f"hypothesis {hypothesis_id!r} depends on an unrecorded hypothesis")
        key = key_of[hypothesis_id]
        depends = tuple(sorted({keys[ac] for ac in acceptance} | {key_of[d] for d in dependencies}))
        claims.append(ClaimDefinition(key, "assumption", requirements.project, requirements.spec_sha256,
                                      str(row.get("statement", "")), f"exploration:{hypothesis_id}", depends, acceptance, None))
        statuses[key] = str(row.get("status", "active"))
        for ac in acceptance:
            edges.append(Edge("refines", key, keys[ac]))
        for dep in dependencies:
            edges.append(Edge("depends_on", key, key_of[dep]))
    _acyclic({c.key: c.depends_on for c in claims}, "exploratory claims")
    return ExploratoryClaimSet(tuple(claims), tuple(edges), statuses)


def _acyclic(graph: Mapping[str, Iterable[str]], what: str) -> None:
    """Three-colour DFS; cycle members reported sorted so the refusal is deterministic."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in graph}
    stack: list[str] = []
    for root in sorted(graph):
        if colour[root] != WHITE:
            continue
        path = [(root, iter(sorted(graph[root])))]
        colour[root] = GREY
        stack = [root]
        while path:
            node, children = path[-1]
            advanced = False
            for child in children:
                if child not in colour:
                    continue  # foreign dependency is checked elsewhere
                if colour[child] == GREY:
                    cycle = stack[stack.index(child):]
                    raise ClaimRefused(f"{what} dependency cycle: {sorted(cycle)}")
                if colour[child] == WHITE:
                    colour[child] = GREY
                    stack.append(child)
                    path.append((child, iter(sorted(graph[child]))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
                path.pop()


def bind_programme_claims(requirements: RequirementClaimSet, programme: Any, strategy: Mapping[str, Any] | None,
                          protected_policy: SpinePolicy) -> ClaimSet:
    """Every accepted criterion has assigned work, every reference is in the same spec/project,
    required obligations come from the protected policy, dependencies are acyclic, no orphan
    work. Never appends hidden requirements. Does not mutate the programme or its hash."""
    spec_sha256 = sha256_value(programme.spec)
    if spec_sha256 != requirements.spec_sha256:
        raise ClaimRefused("programme is bound to a different spec than the requirement claims")
    keys = requirements.keys
    items = {item["id"]: item for item in programme.items}
    owners: dict[str, str] = {}
    item_requirements: dict[str, tuple[str, ...]] = {}
    for item_id, item in sorted(items.items()):
        acceptance = tuple(item["acceptance"])
        if not acceptance:
            raise ClaimRefused(f"programme item {item_id!r} owns no acceptance criterion (orphan work)")
        for ac in acceptance:
            if ac not in keys:
                raise ClaimRefused(f"programme item {item_id!r} names acceptance {ac!r} outside the approved spec")
            if ac in owners:
                raise ClaimRefused(f"acceptance {ac!r} is owned by two programme items")
            owners[ac] = item_id
        item_requirements[item_id] = tuple(sorted(keys[ac] for ac in acceptance))
        for blocker in item["blocked_by"]:
            if blocker not in items:
                raise ClaimRefused(f"programme item {item_id!r} is blocked by unknown item {blocker!r}")
    unowned = sorted(set(keys) - set(owners))
    if unowned:
        raise ClaimRefused(f"acceptance criteria without assigned work: {unowned}")
    _acyclic({item_id: items[item_id]["blocked_by"] for item_id in items}, "programme")
    if strategy is not None:
        if strategy.get("spec_sha256") != spec_sha256:
            raise ClaimRefused("strategy is bound to a different spec")
        if strategy.get("qualification_status") != "UNPROVEN" or strategy.get("proof_reuse_allowed") is not False:
            raise ClaimRefused("strategy cannot certify itself or grant proof reuse")
    if not protected_policy.requirements:
        raise ClaimRefused("protected policy names no obligations")
    policy_ids = {req.claim_id for req in protected_policy.requirements}
    claims: list[ClaimDefinition] = list(requirements.claims)
    edges: list[Edge] = []
    item_obligations: dict[str, tuple[str, ...]] = {}
    obligation_key: dict[tuple[str, str], str] = {}
    for item_id in sorted(items):
        for req in protected_policy.requirements:
            obligation_key[(item_id, req.claim_id)] = sha256_value({
                "schema": OBLIGATION_KEY_SCHEMA, "schema_version": SCHEMA_VERSION,
                "repository_id": requirements.repository_id, "project": requirements.project,
                "spec_sha256": spec_sha256, "programme_sha256": programme.sha256,
                "item_id": item_id, "authority_profile": req.claim_id, "policy_sha256": protected_policy.sha256()})
    for item_id in sorted(items):
        item = items[item_id]
        obligations = []
        for req in protected_policy.requirements:
            for required in req.requires:
                if required not in policy_ids:
                    raise ClaimRefused(f"policy obligation {req.claim_id!r} requires unknown {required!r}")
            key = obligation_key[(item_id, req.claim_id)]
            depends = set(item_requirements[item_id])
            depends |= {obligation_key[(item_id, required)] for required in req.requires}
            for blocker in item["blocked_by"]:
                depends |= {obligation_key[(blocker, other.claim_id)] for other in protected_policy.requirements
                            if other.final_evidence_required}
            claims.append(ClaimDefinition(key, "proof-obligation", requirements.project, spec_sha256,
                                          f"{req.claim_id} obligation for programme item {item_id}",
                                          f"policy:{protected_policy.sha256()}#{req.claim_id}",
                                          tuple(sorted(depends)), item_requirements[item_id] and tuple(items[item_id]["acceptance"]),
                                          req.claim_id))
            obligations.append(key)
            for requirement_key_ in item_requirements[item_id]:
                edges.append(Edge("supported_by", requirement_key_, key))
            for required in req.requires:
                edges.append(Edge("depends_on", key, obligation_key[(item_id, required)]))
        item_obligations[item_id] = tuple(obligations)
    for item_id in sorted(items):
        for blocker in items[item_id]["blocked_by"]:
            for req in protected_policy.requirements:
                if req.final_evidence_required:
                    for own in item_obligations[item_id]:
                        edges.append(Edge("depends_on", own, obligation_key[(blocker, req.claim_id)]))
    claim_set = ClaimSet(requirements.repository_id, requirements.project, spec_sha256, programme.sha256,
                         tuple(claims), tuple(dict.fromkeys(edges)), item_obligations, item_requirements)
    validate_claim_set(claim_set, protected_policy)
    return claim_set


def validate_claim_set(claim_set: ClaimSet, protected_policy: SpinePolicy) -> None:
    by_key = {}
    for claim in claim_set.claims:
        if claim.key in by_key:
            raise ClaimRefused(f"duplicate claim key {claim.key}")
        if claim.kind not in KINDS:
            raise ClaimRefused(f"unknown claim kind {claim.kind!r}")
        if claim.project != claim_set.project or claim.spec_sha256 != claim_set.spec_sha256:
            raise ClaimRefused("claim belongs to another project or spec")
        by_key[claim.key] = claim
    policy_ids = {req.claim_id for req in protected_policy.requirements}
    for claim in claim_set.claims:
        for dep in claim.depends_on:
            if dep not in by_key:
                raise ClaimRefused(f"claim {claim.key} depends on unknown claim {dep}")
        if claim.kind == "proof-obligation" and claim.authority_profile not in policy_ids:
            raise ClaimRefused(f"obligation {claim.key} names an authority profile outside the protected policy")
        if claim.kind != "proof-obligation" and claim.authority_profile is not None:
            raise ClaimRefused("only proof obligations carry an authority profile")
    for edge in claim_set.edges:
        if edge.relation not in RELATIONS or edge.source not in by_key or edge.target not in by_key:
            raise ClaimRefused("claim edge names an unknown relation or claim")
    _acyclic({key: claim.depends_on for key, claim in by_key.items()}, "claim set")
    requirements = {c.key for c in claim_set.claims if c.kind == "requirement"}
    supported = {e.source for e in claim_set.edges if e.relation == "supported_by"}
    if requirements - supported:
        raise ClaimRefused("a requirement has no supporting obligation (uncovered acceptance)")


def bind_implementation_claims(claim_set: ClaimSet, code_subjects: Iterable[Any], observations: Iterable[Mapping[str, Any]],
                               proposals: Iterable[Mapping[str, Any]] = ()) -> ClaimBindings:
    """Bindings between claims and exact code subjects (LINE_LEVEL_CLAIMS 2).

    `proposals` are model/author-proposed `{claim_key, subject_identity, relation, method}`;
    they may only propose (`proposed_implementation`/`depends_on`) and stay `proposed`.
    `observations` come from an observer `{claim_key, subject_identity, relation, observer,
    method, coverage}` and may establish only the relation the observer actually verified.
    Unknown claim keys or subject identities are refused entries, never silent drops.
    """
    known_claims = claim_set.by_key()
    subjects = {}
    for subject in code_subjects:
        identity = subject.identity() if hasattr(subject, "identity") else subject["identity"]
        subjects[identity] = subject
    bindings: list[ClaimBinding] = []
    refused: list[dict[str, str]] = []

    def check(row: Mapping[str, Any], allowed: Iterable[str], source: str) -> ClaimBinding | None:
        relation = row.get("relation")
        if relation not in BINDING_RELATIONS or relation not in allowed:
            refused.append({"claim_key": str(row.get("claim_key")), "reason": f"relation {relation!r} not permitted for {source}"})
            return None
        if row.get("claim_key") not in known_claims:
            refused.append({"claim_key": str(row.get("claim_key")), "reason": "unknown claim key"})
            return None
        if row.get("subject_identity") not in subjects:
            refused.append({"claim_key": str(row.get("claim_key")), "reason": "unknown code subject identity"})
            return None
        subject = subjects[row["subject_identity"]]
        coverage = str(row.get("coverage") or getattr(subject, "dependency_coverage", "unknown"))
        standing = "observed" if source.startswith("observer:") else "proposed"
        return ClaimBinding(row["claim_key"], row["subject_identity"], relation, source, str(row.get("method", "unspecified")),
                            coverage if coverage in {"complete-for-profile", "partial", "unknown"} else "unknown", standing)

    for row in proposals:
        binding = check(row, ("proposed_implementation", "depends_on"), str(row.get("source", "model")))
        if binding is not None:
            bindings.append(binding)
    for row in observations:
        observer = row.get("observer")
        if not isinstance(observer, str) or not observer:
            refused.append({"claim_key": str(row.get("claim_key")), "reason": "observation without an observer identity"})
            continue
        binding = check(row, OBSERVED_RELATIONS | {"depends_on"}, "observer:" + observer)
        if binding is not None:
            bindings.append(binding)
    return ClaimBindings(tuple(dict.fromkeys(bindings)), tuple(refused))


# ---------- derived status ----------

def claim_status(claim_set: ClaimSet, *, approved: bool, exploratory: ExploratoryClaimSet | None = None,
                 attestations: Iterable[Mapping[str, Any]] = (), bindings: ClaimBindings | None = None) -> dict[str, dict[str, Any]]:
    """Derived per-claim view with separate axes; nothing here is persisted or authoritative.

    `attestations`: verified companion results `{claim_key, verdict, currency}` where verdict is
    `pass`/`fail` and currency is `current`/`stale`/`insufficient`/`rejected`. A stale or
    missing predecessor propagates as `insufficient` with the reason named. An unchanged,
    unrelated assumption does not touch other claims' standing.
    """
    by_key = claim_set.by_key()
    latest: dict[str, Mapping[str, Any]] = {}
    for row in attestations:
        if row.get("claim_key") in by_key:
            latest[row["claim_key"]] = row
    exploration_status = dict(exploratory.statuses) if exploratory else {}
    result: dict[str, dict[str, Any]] = {}

    def proof(key: str, seen: tuple[str, ...] = ()) -> tuple[str, str, list[str]]:
        claim = by_key[key]
        reasons: list[str] = []
        if key in seen:
            return "insufficient", "unobserved", ["dependency-cycle"]
        for dep in claim.depends_on:
            if by_key[dep].kind == "proof-obligation":
                dep_proof, dep_currency, _ = proof(dep, seen + (key,))
                if dep_proof != "established" or dep_currency != "current":
                    reasons.append(f"predecessor-{dep_proof}:{dep[:12]}")
        row = latest.get(key)
        if claim.kind == "proof-obligation":
            if row is None:
                return "insufficient", "unobserved", reasons + ["no-attestation"]
            currency = str(row.get("currency", "unobserved"))
            if row.get("verdict") == "pass" and currency == "current" and not reasons:
                return "established", "current", []
            if row.get("verdict") == "fail":
                reasons.append("observed-failure")
            elif currency != "current":
                reasons.append(f"attestation-{currency}")
            return ("rejected" if currency == "rejected" else "insufficient"), currency, reasons
        if claim.kind == "requirement":
            supporting = [e.target for e in claim_set.edges if e.relation == "supported_by" and e.source == key]
            standings = [proof(t, seen + (key,)) for t in supporting]
            if standings and all(s[0] == "established" and s[1] == "current" for s in standings):
                return "established", "current", []
            return "insufficient", "unobserved" if not standings else "mixed", reasons + ["obligations-incomplete"]
        return "not-applicable", "unobserved", reasons

    for key, claim in by_key.items():
        proof_status, currency, reasons = proof(key)
        source_refs = [claim.source_ref]
        if bindings is not None:
            source_refs += [b.subject_identity for b in bindings.for_claim(key)]
        result[key] = {
            "kind": claim.kind,
            "intent_status": "approved" if approved else "unapproved",
            "exploration_status": exploration_status.get(key, "not-explored" if claim.kind != "assumption" else "unknown"),
            "proof_status": proof_status,
            "currency": currency,
            "source_refs": source_refs,
            "reason_codes": sorted(set(reasons)),
        }
    return result


def compile_claims(approved_spec: Mapping[str, Any], programme: Any, strategy: Mapping[str, Any] | None,
                   protected_policy: SpinePolicy, *, repository_id: str, project: str,
                   recorded_hypotheses: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[ClaimSet, ExploratoryClaimSet]:
    """Compatibility composition for callers that already hold a programme. Intake must not
    require it: requirement identities come from `compile_requirement_claims` alone."""
    requirements = compile_requirement_claims(approved_spec, repository_id=repository_id, project=project)
    exploratory = compile_exploratory_claims(requirements, recorded_hypotheses or {})
    return bind_programme_claims(requirements, programme, strategy, protected_policy), exploratory
