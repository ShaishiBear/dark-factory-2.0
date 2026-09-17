"""Search allocation for candidate patches (LINE_LEVEL_CLAIMS 5). Pure; no model, no I/O.

A bounded study includes the current implementation as control, a conservative alternative
and, when the budget permits, a mechanism-changing challenger. A capped share of generation is
reserved for a no-memory challenger fixed before the study. Structural differences can reject
obvious duplicate renamings; they cannot certify creativity, and novelty earns only an
evaluation, never acceptance. If no useful alternative fits the cap, that limitation is the
result, not a fabricated tournament.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .canonical import sha256_value

MAX_CANDIDATES = 8


class SearchRefused(ValueError):
    pass


@dataclass(frozen=True)
class SearchProposal:
    claim_key: str
    total_candidates: int
    control_included: bool
    conservative_slots: int
    challenger_slots: int
    no_memory_slots: int
    generation_budget_microusd: int
    limitation: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"claim_key": self.claim_key, "total_candidates": self.total_candidates, "control_included": self.control_included,
                "conservative_slots": self.conservative_slots, "challenger_slots": self.challenger_slots,
                "no_memory_slots": self.no_memory_slots, "generation_budget_microusd": self.generation_budget_microusd,
                "limitation": self.limitation}


def propose_search_allocation(claim_key: str, context: Mapping[str, Any], archive: Iterable[Mapping[str, Any]],
                              allowance: Mapping[str, Any]) -> SearchProposal:
    """Allocate candidate slots from an explicit allowance: `max_candidates`, `microusd`,
    `no_memory_share` as an integer percentage fixed before the study. Control (unchanged
    baseline) is always included and costs no generation."""
    maximum = allowance.get("max_candidates")
    budget = allowance.get("microusd")
    share = allowance.get("no_memory_share_percent")
    for name, value in (("max_candidates", maximum), ("microusd", budget), ("no_memory_share_percent", share)):
        if type(value) is not int or value < 0:
            raise SearchRefused(f"allowance {name} must be a non-negative integer")
    if maximum > MAX_CANDIDATES or share > 100:
        raise SearchRefused("allowance exceeds the study bounds")
    if maximum < 2 or budget == 0:
        return SearchProposal(claim_key, 1 if maximum >= 1 else 0, maximum >= 1, 0, 0, 0, 0,
                              "no useful alternative fits the cap; control only")
    generated = maximum - 1
    no_memory = max(1, (generated * share) // 100) if share > 0 else 0
    conservative = 1
    challenger = max(0, generated - conservative - no_memory)
    limitation = None
    if challenger == 0:
        limitation = "budget permits no mechanism-changing challenger beyond the conservative alternative"
    seen_families = {row.get("mechanism_family") for row in archive}
    if seen_families and challenger:
        limitation = None
    return SearchProposal(claim_key, maximum, True, conservative, challenger, no_memory, budget, limitation)


@dataclass(frozen=True)
class MixReport:
    distinct_structures: int
    duplicate_ids: tuple[str, ...]
    families: Mapping[str, int]
    meets_policy: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"distinct_structures": self.distinct_structures, "duplicate_ids": list(self.duplicate_ids),
                "families": dict(self.families), "meets_policy": self.meets_policy, "reasons": list(self.reasons)}


_IDENT = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")


def structural_signature(diff_bytes: bytes) -> str:
    """Identifiers and whitespace normalised away: two renamings of one edit collide; two
    different mechanisms do not. A similarity aid only."""
    normalised = _IDENT.sub(b"ID", diff_bytes)
    normalised = re.sub(rb"\s+", b" ", normalised)
    return sha256_value(normalised.decode("utf-8", "replace"))


def check_candidate_mix(policy: SearchProposal, candidate_metadata: Iterable[Mapping[str, Any]],
                        observed_diffs: Mapping[str, bytes]) -> MixReport:
    """Does the generated set match the allocation? Duplicate renamings are named; labels a
    model attached (`mechanism_family`) are reported separately from what the diff shows."""
    rows = list(candidate_metadata)
    signatures: dict[str, list[str]] = {}
    families: dict[str, int] = {}
    for row in rows:
        cid = str(row.get("id"))
        diff = observed_diffs.get(cid)
        if diff is None:
            continue
        signatures.setdefault(structural_signature(diff), []).append(cid)
        families[str(row.get("mechanism_family", "unlabelled"))] = families.get(str(row.get("mechanism_family", "unlabelled")), 0) + 1
    duplicates = tuple(sorted(cid for group in signatures.values() if len(group) > 1 for cid in group[1:]))
    reasons = []
    generated = [r for r in rows if not r.get("is_control")]
    if policy.control_included and not any(r.get("is_control") for r in rows):
        reasons.append("control_missing")
    if len(generated) > policy.total_candidates - (1 if policy.control_included else 0):
        reasons.append("more_candidates_than_allocated")
    no_memory = sum(1 for r in generated if r.get("no_memory") is True)
    if no_memory < policy.no_memory_slots:
        reasons.append("no_memory_challenger_missing")
    if duplicates:
        reasons.append("duplicate_renamings")
    if len(signatures) < 2 and policy.total_candidates >= 3:
        reasons.append("insufficient_structural_diversity")
    return MixReport(len(signatures), duplicates, families, not reasons, tuple(reasons))


@dataclass(frozen=True)
class ParentChoice:
    parent_id: str | None
    basis: str
    archive_size: int


def select_next_parent(policy: SearchProposal, public_observations: Iterable[Mapping[str, Any]],
                       archive: Iterable[Mapping[str, Any]]) -> ParentChoice:
    """Next parent from public search observations only: the best eligible by the public
    metric, else the control; confirmation results are never consulted here."""
    rows = [o for o in public_observations if o.get("partition") == "public" and o.get("complete") is True and o.get("hard_pass") is True]
    archive_rows = list(archive)
    if not rows:
        return ParentChoice(None, "no eligible public observation; start from control", len(archive_rows))
    for row in rows:
        if type(row.get("metric")) is not int:
            raise SearchRefused("public observation metric must be an integer")
    best = min(rows, key=lambda r: (r["metric"], str(r["candidate_id"])))
    return ParentChoice(str(best["candidate_id"]), "best eligible public observation (lower metric)", len(archive_rows))


@dataclass(frozen=True)
class PolicyEvaluation:
    policy_wins: int
    baseline_wins: int
    ties: int
    families: int
    verdict: str
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"policy_wins": self.policy_wins, "baseline_wins": self.baseline_wins, "ties": self.ties,
                "families": self.families, "verdict": self.verdict, "limitations": list(self.limitations)}


def evaluate_search_policy(policy_results: Iterable[Mapping[str, Any]], baseline_results: Iterable[Mapping[str, Any]],
                           *, minimum_families: int) -> PolicyEvaluation:
    """Paired comparison of two search policies on held-out task families under equal budgets.
    Each result is `{task_family, task_id, quality}` (integer, higher better). Fewer covered
    families than `minimum_families` is `insufficient`; never a claimed improvement."""
    if type(minimum_families) is not int or minimum_families < 1:
        raise SearchRefused("minimum families must come from the preregistered protocol")
    by_task = {}
    for source, rows in (("policy", policy_results), ("baseline", baseline_results)):
        for row in rows:
            if type(row.get("quality")) is not int:
                raise SearchRefused("quality must be an integer")
            by_task.setdefault((row["task_family"], row["task_id"]), {})[source] = row["quality"]
    paired = {task: v for task, v in by_task.items() if set(v) == {"policy", "baseline"}}
    wins = sum(1 for v in paired.values() if v["policy"] > v["baseline"])
    losses = sum(1 for v in paired.values() if v["policy"] < v["baseline"])
    ties = len(paired) - wins - losses
    families = len({task[0] for task in paired})
    limitations = ("observational pairing on held-out families; no causal claim beyond the measured cohort",)
    if families < minimum_families:
        return PolicyEvaluation(wins, losses, ties, families, "insufficient", limitations + ("family coverage below the preregistered minimum",))
    if wins > losses:
        return PolicyEvaluation(wins, losses, ties, families, "policy_ahead", limitations)
    if losses > wins:
        return PolicyEvaluation(wins, losses, ties, families, "baseline_ahead", limitations)
    return PolicyEvaluation(wins, losses, ties, families, "no_difference_detected", limitations + ("no difference detected is not equivalence",))
