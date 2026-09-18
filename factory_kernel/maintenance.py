"""Governed self-maintenance, proposals only (SPECIFICATION 11.2, C12, WP12, R10).

Inputs are measured incidents (an authenticated outcome classification or a lesson admission
record, verified by their own digests) plus an explicit bounded objective and the paths a change
would touch. `classify_change` is deterministic against the protected path and effect policy:
the tier a change lands in is the highest of the tiers its paths and its effects derive, a
generated description can never lower it, and facts the classifier cannot see (a floor value, a
capability grant) leave the classification unknown, which refuses autonomous activation and
retains a maintainer proposal. `propose_change` records a reviewable proposal with the actor
roles spelled out; `prepare_shadow` names the old authority as the judge of a candidate judge
or policy, never the candidate itself; `compare_shadow` stores both results and treats any
disagreement as a blocker; `request_cutover` is refused while no autonomous maintenance lane is
activated, which is the policy this repository installs.

Nothing here changes a file, opens a pull request, alters a judge, lowers a floor, grants a
capability or suppresses evidence: those are exactly the changes it classifies as trust changes
for the maintainer lane. A chat agent's maintainer PR is not relabelled as autonomous
self-improvement; the proposal record names who proposed, who established and who delivered.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

from .canonical import canonical_bytes, sha256_value

SCHEMA_POLICY = "dark-factory/maintenance-policy"
SCHEMA_CLASSIFICATION = "dark-factory/maintenance-classification"
SCHEMA_PROPOSAL = "dark-factory/maintenance-proposal"
SCHEMA_SHADOW = "dark-factory/maintenance-shadow"
SCHEMA_VERSION = "1.0"
POLICY_PATH = Path(".factory") / "maintenance-policy.json"
TIERS = ("product", "application-security", "protected-deploy", "trust-root-authority")  # ascending
EFFECTS = ("product", "security", "deploy", "judge", "floor", "capability", "evidence", "policy", "prompt")
INCIDENT_SCHEMAS = ("dark-factory/outcome-classification", "dark-factory/lesson-admission")
REFUSALS = ("floor_lowered", "capability_granted", "detector_removed", "evidence_suppressed")
MAX_OBJECTIVE = 2000
MAX_PATHS = 400
_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/@+-]{0,399}")  # a leading dot names `.factory/...`, `.github/...`, `.env`

# The path-derived minimum tier. This mirrors the security guard's protected set
# (`scripts/factory_security.protected_path`); a test pins the two together so they cannot drift.
APPLICATION_SECURITY_PATHS = frozenset({
    "app/backend/config.py", "app/backend/main.py", "app/backend/rate_limit.py", "app/backend/signup_rate_limit.py",
    "app/backend/db/repository.py", "app/backend/db/signup_attempts_repo.py", "app/backend/db/user_messages_repo.py",
    "app/backend/db/users_repo.py", "app/backend/routes/admin.py", "app/backend/routes/auth.py",
    "app/backend/routes/conversations.py", "app/backend/routes/messages.py",
})
TRUST_ROOT_FILES = frozenset({
    "FACTORY_RULES.md", "MISSION.md", "CLAUDE.md", "PROGRAMME.md", ".factory/kernel.json", ".factory/evidence-spine.json",
    ".factory/architecture.json", ".factory/locks/floor.json", ".factory/project-profile.json", ".factory/authority-profiles.json",
    ".factory/tcb.json", "scripts/frontier_filter.py",
})
TRUST_ROOT_PREFIXES = ("factory_kernel/", ".factory/prompts/", ".factory/programmes/", ".factory/methods/", ".factory/holdout/",
                       ".factory/benchmark/", ".github/", "harness/", "tests/factory/", "scripts/factory_")
JUDGE_PREFIXES = ("factory_kernel/", "harness/", "scripts/factory_", "tests/factory/", ".factory/holdout/", ".factory/benchmark/", ".github/")
DETECTOR_PREFIXES = ("tests/factory/", ".factory/holdout/", "harness/factory_mutations/", "harness/mutations/")


class MaintenanceRefused(ValueError):
    pass


def path_tier(path: str) -> str:
    """The minimum tier a path derives, from the protected path policy alone."""
    name = Path(path).name
    if (path in TRUST_ROOT_FILES or any(path.startswith(p) for p in TRUST_ROOT_PREFIXES)):
        return "trust-root-authority"
    if path.startswith("deploy/systemd/") or name == "Dockerfile" or re.fullmatch(r"docker-compose(?:\.[^.]+)?\.ya?ml", name) or name.startswith(".env"):
        return "protected-deploy"
    if path in APPLICATION_SECURITY_PATHS or path.startswith("app/backend/auth/"):
        return "application-security"
    return "product"


def path_effects(path: str) -> set[str]:
    """The effects a path implies, before any description is read."""
    effects: set[str] = set()
    if path == ".factory/locks/floor.json":
        effects.add("floor")
    if path in ("factory_kernel/capabilities.py", "factory_kernel/effect_broker.py", "factory_kernel/lease_store.py"):
        effects.add("capability")
    if path in ("factory_kernel/evidence_retention.py", "factory_kernel/evidence_retention_archive.py", "factory_kernel/evidence_closure.py",
                ".factory/evidence-spine.json", "factory_kernel/spine.py"):
        effects.add("evidence")
    if path.startswith(".factory/prompts/") or path.startswith(".factory/methods/"):
        effects.add("prompt")
    if path.endswith(".json") and path.startswith(".factory/") or path in ("FACTORY_RULES.md", "MISSION.md", "CLAUDE.md"):
        effects.add("policy")
    if any(path.startswith(p) for p in JUDGE_PREFIXES):
        effects.add("judge")
    tier = path_tier(path)
    if tier == "protected-deploy":
        effects.add("deploy")
    elif tier == "application-security":
        effects.add("security")
    elif tier == "product":
        effects.add("product")
    return effects


# ---- policy ---------------------------------------------------------------------------------------


def parse_policy(raw: Any) -> dict:
    """The protected maintenance policy: a lane and an ACP requirement per tier, an effect ->
    tier map, and the (initially empty) list of activated autonomous lanes. Shape only."""
    fields = {"schema", "schema_version", "policy_id", "version", "tiers", "effects", "autonomous_lanes", "note"}
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise MaintenanceRefused("maintenance policy must carry exactly the policy fields")
    if raw["schema"] != SCHEMA_POLICY or raw["schema_version"] != SCHEMA_VERSION:
        raise MaintenanceRefused("unknown maintenance policy schema")
    for key in ("policy_id", "version", "note"):
        if not isinstance(raw[key], str) or not raw[key]:
            raise MaintenanceRefused(f"maintenance policy {key} must be a non-empty string")
    tiers = raw["tiers"]
    if not isinstance(tiers, Mapping) or set(tiers) != set(TIERS):
        raise MaintenanceRefused("maintenance policy must name every tier exactly once")
    for name, tier in tiers.items():
        if not isinstance(tier, Mapping) or set(tier) != {"minimum_lane", "acp_required"} or not isinstance(tier["minimum_lane"], str) \
                or not tier["minimum_lane"] or not isinstance(tier["acp_required"], bool):
            raise MaintenanceRefused(f"tier {name} needs a minimum_lane and an acp_required flag")
    effects = raw["effects"]
    if not isinstance(effects, Mapping) or set(effects) != set(EFFECTS) or not all(v in TIERS for v in effects.values()):
        raise MaintenanceRefused("maintenance policy must map every effect to a tier")
    lanes = raw["autonomous_lanes"]
    if not isinstance(lanes, list) or not all(isinstance(lane, str) and lane for lane in lanes):
        raise MaintenanceRefused("autonomous_lanes must be a list of lane names")
    record = {k: raw[k] for k in sorted(fields)}
    record["sha256"] = sha256_value({k: raw[k] for k in sorted(fields)})
    return record


def load_policy(path: str | Path) -> dict:
    try:
        return parse_policy(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError) as exc:
        raise MaintenanceRefused("maintenance policy cannot be read") from exc


# ---- classification -------------------------------------------------------------------------------


def _paths(paths: Iterable[Any]) -> list[str]:
    given = list(paths)
    if not given or len(given) > MAX_PATHS or not all(isinstance(p, str) and _PATH.fullmatch(p) and ".." not in p.split("/") for p in given):
        raise MaintenanceRefused("a change names between one and 400 repository-relative paths")
    return sorted(set(given))


def _floor_refusal(facts: Mapping[str, Any]) -> str | None:
    before, after = facts.get("floor_before"), facts.get("floor_after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    for key, value in before.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            new = after.get(key)
            if not isinstance(new, (int, float)) or isinstance(new, bool) or new < value:
                return "floor_lowered"
    return None


def _capability_refusal(facts: Mapping[str, Any]) -> str | None:
    before, after = facts.get("capability_roles_before"), facts.get("capability_roles_after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    for role, operations in after.items():
        allowed = set(before.get(role, ())) if isinstance(before.get(role), (list, tuple)) else set()
        if not isinstance(operations, (list, tuple)) or not set(operations) <= allowed:
            return "capability_granted"
    return None


def classify_change(paths: Iterable[Any], *, policy: Mapping[str, Any], declared_effects: Iterable[str] = (),
                    declared_tier: str | None = None, facts: Mapping[str, Any] | None = None) -> dict:
    """Deterministic classification of a described change. The tier is the maximum of the
    path-derived tiers and the policy's tier for every effect (path-derived or declared); a
    declared tier below that is recorded as a downgrade attempt and ignored. Facts, when
    supplied, can only add refusals (a lowered floor, a granted capability, a removed detector,
    suppressed evidence); facts the classifier needs and does not have make the classification
    unknown. Activation is refused unless the lane is in the policy's activated autonomous
    lanes, which is never the case under the installed policy."""
    changed = _paths(paths)
    declared = sorted({str(e) for e in declared_effects})
    if not set(declared) <= set(EFFECTS):
        raise MaintenanceRefused("declared effects must be from the effect vocabulary")
    tiers = {path: path_tier(path) for path in changed}
    effects: set[str] = set(declared)
    for path in changed:
        effects |= path_effects(path)
    rank = {name: index for index, name in enumerate(TIERS)}
    path_minimum = max(tiers.values(), key=lambda t: rank[t])
    effect_minimum = max((policy["effects"][e] for e in effects), key=lambda t: rank[t], default="product")
    tier = max((path_minimum, effect_minimum), key=lambda t: rank[t])
    downgrade = declared_tier is not None and (declared_tier not in rank or rank[declared_tier] < rank[tier])
    facts = facts if isinstance(facts, Mapping) else {}
    refusals: list[str] = []
    unknown: list[str] = []
    if "floor" in effects:
        refusal = _floor_refusal(facts)
        if refusal:
            refusals.append(refusal)
        elif not (isinstance(facts.get("floor_before"), Mapping) and isinstance(facts.get("floor_after"), Mapping)):
            unknown.append("floor_facts_missing")
    if "capability" in effects:
        refusal = _capability_refusal(facts)
        if refusal:
            refusals.append(refusal)
        elif not (isinstance(facts.get("capability_roles_before"), Mapping) and isinstance(facts.get("capability_roles_after"), Mapping)):
            unknown.append("capability_facts_missing")
    removed = [p for p in facts.get("removed_paths", []) if isinstance(p, str)] if isinstance(facts.get("removed_paths"), list) else []
    if any(any(p.startswith(prefix) for prefix in DETECTOR_PREFIXES) for p in removed):
        refusals.append("detector_removed")
    if facts.get("evidence_removed") is True:
        refusals.append("evidence_suppressed")
    lane = policy["tiers"][tier]["minimum_lane"]
    activation = "refused"
    if not refusals and not unknown and lane in policy["autonomous_lanes"]:
        activation = "permitted"
    record = {
        "schema": SCHEMA_CLASSIFICATION, "schema_version": SCHEMA_VERSION, "policy_sha256": policy["sha256"],
        "paths": changed, "path_tiers": tiers, "effects": sorted(effects), "declared_effects": declared,
        "tier": tier, "path_minimum_tier": path_minimum, "effect_minimum_tier": effect_minimum,
        "declared_tier": declared_tier, "downgrade_attempted": bool(downgrade),
        "lane": lane, "acp_required": bool(policy["tiers"][tier]["acp_required"]),
        "refusals": sorted(set(refusals)), "unknown": unknown, "classification": "unknown" if unknown else "trust-change" if tier != "product" else "ordinary-change",
        "activation": activation, "authority": "classification-record-only",
    }
    return {**record, "identity": sha256_value(record)}


# ---- proposals ------------------------------------------------------------------------------------


def verify_incident(incident: Any) -> dict:
    """A measured incident is one of two records the kernel itself produced, verified by the
    digest it carries. Free text, a log line or an unsigned mapping is refused."""
    if not isinstance(incident, Mapping) or incident.get("schema") not in INCIDENT_SCHEMAS:
        raise MaintenanceRefused("an incident is an outcome classification or a lesson admission record")
    if incident["schema"] == "dark-factory/outcome-classification":
        expected = sha256_value({k: v for k, v in incident.items() if k != "identity"})
        if incident.get("identity") != expected:
            raise MaintenanceRefused("outcome classification identity does not verify")
        return {"kind": "outcome-classification", "identity": incident.get("identity"), "classification": incident.get("classification")}
    expected = sha256_value({k: v for k, v in incident.items() if k != "record_sha256"})
    if incident.get("record_sha256") != expected:
        raise MaintenanceRefused("lesson admission record digest does not verify")
    return {"kind": "lesson-admission", "identity": incident["record_sha256"], "status": incident.get("status"),
            "lesson_id": incident.get("lesson_id")}


def propose_change(incident: Any, objective: str, paths: Iterable[Any], *, policy: Mapping[str, Any], proposed_by: str,
                   declared_effects: Iterable[str] = (), declared_tier: str | None = None,
                   facts: Mapping[str, Any] | None = None) -> dict:
    """A reviewable maintainer proposal: the verified incident, the bounded objective, the paths,
    the deterministic classification, and the actor roles (who proposed; established and delivered
    are empty until tests and existing authority fill them). It activates nothing."""
    verified = verify_incident(incident)
    if not isinstance(objective, str) or not objective.strip() or len(objective) > MAX_OBJECTIVE:
        raise MaintenanceRefused("a maintenance objective is bounded non-empty text")
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise MaintenanceRefused("a proposal names who proposed it")
    classification = classify_change(paths, policy=policy, declared_effects=declared_effects, declared_tier=declared_tier, facts=facts)
    record = {
        "schema": SCHEMA_PROPOSAL, "schema_version": SCHEMA_VERSION, "status": "maintainer-proposal",
        "incident": verified, "objective": objective.strip(), "classification": classification,
        "required_route": ("architecture-change-proposal" if classification["acp_required"] else "ordinary-review") + " via " + classification["lane"],
        "actor_roles": {"proposed_by": proposed_by.strip(), "established_by": None, "delivered_by": None},
        "activation": "none", "authority": "proposal-only",
        "limitations": ["a proposal, not a patch: no file is changed and no pull request is opened",
                        "the classification reads paths, declared effects and supplied facts, not the semantics of a diff",
                        "the old authority judges any installation; this record never does"],
    }
    return {**record, "identity": sha256_value(record)}


# ---- shadow qualification and cutover ---------------------------------------------------------


def prepare_shadow(proposal: Mapping[str, Any], *, old_authority: str, candidate_ref: str) -> dict:
    """The shadow plan: the old authority evaluates the candidate as data. The candidate never
    judges its own installation, so the two references must differ."""
    if not isinstance(proposal, Mapping) or proposal.get("schema") != SCHEMA_PROPOSAL:
        raise MaintenanceRefused("shadow qualification needs a maintenance proposal")
    if not isinstance(old_authority, str) or not old_authority or not isinstance(candidate_ref, str) or not candidate_ref:
        raise MaintenanceRefused("shadow qualification names the old authority and the candidate")
    if old_authority == candidate_ref:
        raise MaintenanceRefused("the candidate cannot judge its own installation")
    record = {"schema": SCHEMA_SHADOW, "schema_version": SCHEMA_VERSION, "proposal_identity": proposal["identity"],
              "judge": old_authority, "subject": candidate_ref, "candidate_judges_itself": False, "status": "prepared",
              "results": {"old": None, "candidate": None}}
    return {**record, "identity": sha256_value(record)}


def compare_shadow(shadow: Mapping[str, Any], old_result: Any, candidate_result: Any) -> dict:
    """Store both results verbatim. Agreement on the verdict is the only passing outcome; a
    disagreement is a blocker, never averaged away; a missing result is insufficient."""
    if not isinstance(shadow, Mapping) or shadow.get("schema") != SCHEMA_SHADOW:
        raise MaintenanceRefused("compare a prepared shadow plan")
    results = {"old": old_result, "candidate": candidate_result}
    verdicts = {k: v.get("verdict") if isinstance(v, Mapping) else None for k, v in results.items()}
    if verdicts["old"] is None or verdicts["candidate"] is None:
        status, blocker = "insufficient", "shadow-result-missing"
    elif verdicts["old"] != verdicts["candidate"]:
        status, blocker = "blocked", "shadow-disagreement"
    else:
        status, blocker = "agreed", None
    record = {**{k: v for k, v in shadow.items() if k != "identity"}, "results": results, "verdicts": verdicts, "status": status,
              "blocker": blocker, "shadow_plan_identity": shadow["identity"]}
    return {**record, "identity": sha256_value(record)}


def request_cutover(proposal: Mapping[str, Any], shadow_comparison: Mapping[str, Any] | None, *, policy: Mapping[str, Any]) -> dict:
    """Refused unless every gate holds and the lane is an activated autonomous lane. Under the
    installed policy no lane is activated, so every request is refused with the route that
    would be needed; refusal is the recorded outcome, not an exception."""
    if not isinstance(proposal, Mapping) or proposal.get("schema") != SCHEMA_PROPOSAL:
        raise MaintenanceRefused("cutover is requested for a maintenance proposal")
    classification = proposal["classification"]
    reasons = []
    if classification["refusals"]:
        reasons.append("classification-refusals:" + ",".join(classification["refusals"]))
    if classification["unknown"]:
        reasons.append("classification-unknown")
    if classification["tier"] != "product" and (not isinstance(shadow_comparison, Mapping) or shadow_comparison.get("status") != "agreed"):
        reasons.append("shadow-qualification-not-agreed")
    if classification["lane"] not in policy["autonomous_lanes"]:
        reasons.append("no-autonomous-lane-activated")
    record = {"schema": SCHEMA_PROPOSAL + "-cutover", "schema_version": SCHEMA_VERSION, "proposal_identity": proposal["identity"],
              "status": "refused" if reasons else "eligible", "reasons": reasons,
              "route": proposal["required_route"], "authority": "cutover-request-record-only"}
    return {**record, "identity": sha256_value(record)}


# ---- command line ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed maintenance: classify a described change or record a maintainer proposal (proposals only).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("classify", "propose"):
        p = sub.add_parser(name)
        p.add_argument("--policy", type=Path, default=POLICY_PATH)
        p.add_argument("--path", action="append", required=True, dest="paths")
        p.add_argument("--effect", action="append", default=[], dest="effects")
        p.add_argument("--declared-tier")
        p.add_argument("--facts", type=Path)
        if name == "propose":
            p.add_argument("--incident", type=Path, required=True)
            p.add_argument("--objective", required=True)
            p.add_argument("--proposed-by", required=True)
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else None
        if args.command == "classify":
            record = classify_change(args.paths, policy=policy, declared_effects=args.effects, declared_tier=args.declared_tier, facts=facts)
        else:
            incident = json.loads(args.incident.read_text(encoding="utf-8"))
            record = propose_change(incident, args.objective, args.paths, policy=policy, proposed_by=args.proposed_by,
                                    declared_effects=args.effects, declared_tier=args.declared_tier, facts=facts)
        sys.stdout.write(canonical_bytes(record).decode("utf-8"))
        return 0
    except (MaintenanceRefused, ValueError, OSError, KeyError, TypeError) as exc:
        sys.stderr.write(f"MAINTENANCE_REFUSED: {type(exc).__name__}\n")
        return 2


__all__ = ["APPLICATION_SECURITY_PATHS", "EFFECTS", "INCIDENT_SCHEMAS", "MaintenanceRefused", "POLICY_PATH", "REFUSALS", "SCHEMA_CLASSIFICATION",
           "SCHEMA_POLICY", "SCHEMA_PROPOSAL", "SCHEMA_SHADOW", "TIERS", "TRUST_ROOT_FILES", "TRUST_ROOT_PREFIXES", "classify_change",
           "compare_shadow", "load_policy", "main", "parse_policy", "path_effects", "path_tier", "prepare_shadow", "propose_change",
           "request_cutover", "verify_incident"]


if __name__ == "__main__":
    raise SystemExit(main())
