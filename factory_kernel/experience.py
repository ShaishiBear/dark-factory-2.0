"""Experience packets for reasoning roles (SPECIFICATION 11.1, C10, WP11): bounded, role-scoped, advisory.

`retrieve(role, task, approved_scope, budget)` returns an `ExperiencePacket`: bounded snippets of
admitted lessons with their provenance, applicability and counter-evidence. The role allowlist
is a code constant: plan, investigate, context, implement and repair may receive a packet.
Every other role, and in particular the blinded holdouts and certifiers, the acceptance-test
author and the conformance authority, gets an empty packet before any record is examined:
`retrieve` never touches the record source for them. That is enforced where the payload is
built (`runtime._experience_context`, the one funnel both `_agent` paths pass through), which
also refuses a caller that tries to hand a blind role learned material inside its context.

A packet is advisory context only. Nothing in it is proof, currency or authority: every item
is the record of an admitted lesson (`lessons.admit`), bound to its proposal by digest, admitted
under a protected policy, currently applicable, and inside the task's applicability; items
carry the lesson's own `not_established` list and the negative evidence of its lineage as
counter-evidence. The no-memory challenger route (`no_memory_packet`) records an empty packet
whose digest says neither a learned packet nor a previous-winner exemplar was in the
generation context; it is not proof the base model has no prior knowledge.

The stage record beside each worker (`experience-<role>.json`) names the items by digest only:
the artifacts directory is readable by every tool-bearing role of the run, including the blind
`test_author` and `conformance`, so lesson text travels in the prompt and nowhere on disk.

Today the only retained lesson records are the investigation records of the run itself
(`lesson_records`), and no protected lesson policy is installed, so every production packet is
empty and says so. The mechanism is exercised end to end by tests over a real admission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .canonical import canonical_bytes, sha256_value
from .lessons import LessonAdmissionPolicy, retrieval_eligible
from .worker_policy import AUTHORITY_ROLES

SCHEMA = "dark-factory/experience-packet"
SCHEMA_VERSION = "1.0"
# The initial allowlist (SPECIFICATION 11.1). The acceptance-test author and the conformance
# authority are deliberately absent until their independence implications are reviewed.
RETRIEVAL_ROLES = frozenset({"plan", "investigate", "context", "implement", "repair"})
BLIND_ROLES = frozenset(AUTHORITY_ROLES) | {"test_author", "conformance"}
DEFAULT_BUDGET = {"max_items": 3, "max_bytes": 4000}
MARKER = "UNPROVEN EXPERIENCE PACKET"  # the only header learned material may arrive under
MODES = ("retrieval", "blind", "no-memory")
SKIP_REASONS = ("unbound", "ineligible", "inapplicable", "over_bound", "malformed")


class ExperienceRefused(ValueError):
    pass


@dataclass(frozen=True)
class ExperiencePacket:
    role: str
    mode: str
    task_digest: str
    items: tuple[dict, ...] = ()
    records_examined: int = 0
    skipped: Mapping[str, int] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    @property
    def bytes(self) -> int:
        return len(canonical_bytes(list(self.items)))

    @property
    def input_digest(self) -> str:
        """What the generation context carried from experience: the items exactly, or nothing."""
        return sha256_value({"role": self.role, "mode": self.mode, "task_digest": self.task_digest, "items": list(self.items)})

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "role": self.role, "mode": self.mode,
                "task_digest": self.task_digest, "items": list(self.items), "item_count": len(self.items), "bytes": self.bytes,
                "records_examined": self.records_examined, "skipped": dict(self.skipped), "input_digest": self.input_digest,
                "limitations": list(self.limitations), "authority": "advisory-context-only"}

    def record(self) -> dict:
        """The stage record: what was retrieved, by reference only. The run's artifacts directory
        is readable by every tool-bearing role of the run, including the blind `test_author` and
        `conformance`, so no lesson text is written there; the prompt is the only carrier, and
        the record names the items by their digests so the prompt's content is still auditable."""
        return {"schema": SCHEMA + "-record", "schema_version": SCHEMA_VERSION, "role": self.role, "mode": self.mode,
                "task_digest": self.task_digest, "item_count": len(self.items), "bytes": self.bytes,
                "item_refs": [{"lesson_id": item.get("lesson_id"), "proposal_sha256": item["provenance"].get("proposal_sha256"),
                               "record_sha256": item["provenance"].get("record_sha256")} for item in self.items],
                "records_examined": self.records_examined, "skipped": dict(self.skipped), "input_digest": self.input_digest,
                "limitations": list(self.limitations), "authority": "advisory-context-only"}


def task_digest(task: Mapping[str, Any], approved_scope: Iterable[str]) -> str:
    return sha256_value({"task": dict(task), "approved_scope": sorted(str(p) for p in approved_scope)})


def _empty(role: str, mode: str, task: Mapping[str, Any], approved_scope: Iterable[str], *, limitations: tuple[str, ...]) -> ExperiencePacket:
    return ExperiencePacket(role=role, mode=mode, task_digest=task_digest(task, approved_scope), limitations=limitations)


def no_memory_packet(role: str, task: Mapping[str, Any], approved_scope: Iterable[str] = ()) -> ExperiencePacket:
    """The fresh challenger's packet: empty by construction, recorded so the comparison can
    say what the generation context did not contain."""
    return _empty(role, "no-memory", task, approved_scope, limitations=(
        "no learned packet and no previous-winner exemplar were in the generation context",
        "not proof that the base model has no prior knowledge of the task",
    ))


def _applicable(proposal: Mapping[str, Any], plan: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    """Same contamination group (the lesson was learned on this very contract) or a declared
    task family the lesson's mechanism families cover. Nothing else is applicable."""
    group = plan.get("contamination_group_id")
    if isinstance(group, str) and group and group == task.get("contamination_group_id"):
        return True
    families = {f for f in proposal.get("mechanism_families", []) if isinstance(f, str)}
    declared = {f for f in task.get("families", []) if isinstance(f, str)}
    return bool(families & declared)


def retrieve(role: str, task: Mapping[str, Any], approved_scope: Iterable[str], budget: Mapping[str, int] | None = None, *,
             records: Iterable[Mapping[str, Any]] | Callable[[], Iterable[Mapping[str, Any]]], policy: LessonAdmissionPolicy | None,
             current: bool = True) -> ExperiencePacket:
    """The packet for one role and task. A role outside the allowlist gets an empty `blind`
    packet and the record source is never read (a callable source is not called). For an
    allowed role each record is `{proposal, admission, plan}`: the admission must bind the
    proposal by digest, be retrieval-eligible for this role under the policy and currently
    applicable, and the lesson must apply to this task; the packet is bounded by count and bytes."""
    if not isinstance(role, str) or not role:
        raise ExperienceRefused("a packet is retrieved for a named role")
    if not isinstance(task, Mapping):
        raise ExperienceRefused("a task is a mapping")
    scope = tuple(approved_scope)
    if role not in RETRIEVAL_ROLES:
        return _empty(role, "blind", task, scope, limitations=("role receives no learned material; no record was examined",))
    bounds = {**DEFAULT_BUDGET, **(dict(budget) if budget else {})}
    if any(not isinstance(bounds[k], int) or isinstance(bounds[k], bool) or bounds[k] < 0 for k in ("max_items", "max_bytes")):
        raise ExperienceRefused("packet bounds are non-negative integers")
    source = records() if callable(records) else records
    items: list[dict] = []
    skipped = {reason: 0 for reason in SKIP_REASONS}
    examined = 0
    for record in source:
        examined += 1
        if not isinstance(record, Mapping) or not all(isinstance(record.get(k), Mapping) for k in ("proposal", "admission", "plan")):
            skipped["malformed"] += 1
            continue
        proposal, admission, plan = record["proposal"], record["admission"], record["plan"]
        if admission.get("proposal_sha256") != sha256_value(dict(proposal)):
            skipped["unbound"] += 1
            continue
        if not retrieval_eligible(admission, role=role, policy=policy, current=current):
            skipped["ineligible"] += 1
            continue
        if not _applicable(proposal, plan, task):
            skipped["inapplicable"] += 1
            continue
        item = {
            "lesson_id": admission.get("lesson_id"), "hypothesis": str(proposal.get("hypothesis", ""))[:1000],
            "causal_mechanism": str(proposal.get("causal_mechanism", ""))[:1000], "outcome": proposal.get("outcome"),
            "scope": proposal.get("scope"), "mechanism_families": sorted(f for f in proposal.get("mechanism_families", []) if isinstance(f, str)),
            "applicability": "same-contract" if plan.get("contamination_group_id") == task.get("contamination_group_id") else "declared-family",
            "provenance": {"plan_digest": proposal.get("plan_digest"), "proposal_sha256": admission.get("proposal_sha256"),
                           "record_sha256": admission.get("record_sha256"), "policy_sha256": (admission.get("evaluation") or {}).get("policy_sha256"),
                           "evidence_refs": list((admission.get("evaluation") or {}).get("evidence_refs", []))},
            "counter_evidence": {"not_established": list(proposal.get("not_established", [])), "lineage": list(admission.get("lineage", []))},
            "qualification_status": "UNPROVEN",
        }
        if len(items) >= bounds["max_items"] or len(canonical_bytes(items + [item])) > bounds["max_bytes"]:
            skipped["over_bound"] += 1
            continue
        items.append(item)
    return ExperiencePacket(role=role, mode="retrieval", task_digest=task_digest(task, scope), items=tuple(items),
                            records_examined=examined, skipped=skipped, limitations=(
                                "advisory context only: no item is proof, currency or authority",
                                "observational: admitted under the installed policy over its own cohort, not this task",
                            ))


def packet_context(packet: ExperiencePacket) -> str:
    """The text a permitted worker receives: nothing at all for an empty packet, else the
    marked block. The marker is the only header learned material may travel under, which is
    what lets the funnel refuse it for a blind role."""
    if not packet.items:
        return ""
    return ("\n\n" + MARKER + " — ADVISORY ONLY\n"
            "These are admitted lessons from earlier measured comparisons, not proof and not currency. "
            "Recheck every assumption against the current code and acceptance; report contradictions. "
            "Do not expand acceptance or waive any check on their account.\n"
            + json.dumps(packet.to_dict(), sort_keys=True, indent=1) + "\n")


def carries_learned_material(text: str) -> bool:
    return isinstance(text, str) and MARKER in text


def lesson_records(artifacts: Path) -> list[dict]:
    """The lesson records this run retained: every `investigation-*.json` whose proposal and
    admission are both present, with the plan they were made under. Nothing else persists yet."""
    if not isinstance(artifacts, Path) or not artifacts.is_dir():
        return []
    records = []
    for path in sorted(artifacts.glob("investigation-*.json")):
        try:
            outcome = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(outcome, Mapping):
            continue
        proposal, admission, plan = outcome.get("lesson_proposal"), outcome.get("lesson_admission"), outcome.get("plan")
        if isinstance(proposal, Mapping) and isinstance(admission, Mapping) and isinstance(plan, Mapping):
            records.append({"proposal": proposal, "admission": admission, "plan": plan, "source": path.name})
    return records


def task_from_artifacts(artifacts: Path) -> dict:
    """The task identity a run's artifacts establish: the linked issue's contamination group.
    Without a compiled contract there is no task identity and nothing applies."""
    if not isinstance(artifacts, Path):
        return {}
    path = artifacts / "task-contract.json"
    try:
        contract = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError, UnicodeError):
        return {}
    issue = contract.get("issue") if isinstance(contract, Mapping) else None
    number = issue.get("number") if isinstance(issue, Mapping) else None
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        return {"contamination_group_id": f"issue-{number}"}
    return {}


__all__ = ["BLIND_ROLES", "DEFAULT_BUDGET", "ExperiencePacket", "ExperienceRefused", "MARKER", "MODES", "RETRIEVAL_ROLES",
           "SCHEMA", "SCHEMA_VERSION", "carries_learned_material", "lesson_records", "no_memory_packet", "packet_context",
           "retrieve", "task_digest", "task_from_artifacts"]
