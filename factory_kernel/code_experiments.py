"""Code experiments: frozen candidate patches compared under a frozen protocol (LINE_LEVEL 3-4, C08).

An implementer that faces a disputed choice may leave its own version in the tree (the
committed baseline) and return a bounded `investigation_request.json` naming alternative exact
patches on top of it. The host, not the model, then: compiles the plan against the design
envelope and the immutable acceptance tests; freezes every candidate (patch applied to the
exact committed baseline, resulting tree derived by Git, tree reverted); executes the frozen
evaluator against each candidate in turn with the baseline included; assesses the comparison
under the protocol; and proposes the selected exact patch to the normal commit authority.
Candidates are evaluated in place and reverted rather than copied, because a checkout's
installed dependencies are what its acceptance tests need; the exact Git tree identity before
and after every step is what makes the in-place stage disposable. No candidate supplies
evaluator code, runner commands, mounts or credentials. Every attempt, including failures and
exclusions, stays in the denominator and in the record. Selection is provisional: the selected
patch still enters ordinary independent qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping

from .canonical import canonical_bytes, sha256_bytes, sha256_value
from .code_subjects import safe_path
from .evaluation_protocol import (
    AdmissibleObservation, ComparisonResult, EvaluationProtocol, Evaluator, ProtocolRefused, freeze_protocol,
    screen_finite, validate_observation,
)

SCHEMA_VERSION = "1.0"
REQUEST_SCHEMA = "dark-factory/investigation-request"
PLAN_SCHEMA = "dark-factory/code-experiment-plan"
CANDIDATE_SCHEMA = "dark-factory/frozen-candidate"
SELECTION_SCHEMA = "dark-factory/selection-record"
OUTCOME_SCHEMA = "dark-factory/investigation-outcome"
MAX_REQUEST_BYTES = 200_000
MAX_CANDIDATES = 3          # alternatives beyond the unchanged baseline
MAX_PATCH_BYTES = 65_536
MAX_STATEMENT = 2000
EVALUATOR_TIMEOUT_SECONDS = 600
PREDICATES = {
    # The only registered predicate for the first vertical slice: the frozen acceptance
    # contract passes deterministically. Performance predicates arrive with registered
    # workload runners (WP08); a natural-language hypothesis is not a predicate.
    "contract-pass-v1": {"adapter": "finite-contract-v1", "description": "every frozen acceptance checkpoint passes"},
}
BASELINE_ID = "baseline"


class InvestigationRefused(ValueError):
    pass


# ---------- records ----------

@dataclass(frozen=True)
class CodeExperimentPlan:
    id: str
    parent_claims: tuple[str, ...]
    decision_id: str
    exact_baseline_tree: str
    editable_subjects: tuple[str, ...]      # repository-relative paths the design permits
    permitted_effects: tuple[str, ...]
    unchanged_contract_refs: tuple[str, ...]  # immutable acceptance test paths (hashed in RED)
    hypothesis: str
    predicate_id: str
    causal_mechanism: str
    candidate_generation_policy_ref: str
    evaluator_closure_digest: str
    protocol: EvaluationProtocol
    environment_digest: str
    budget_ref: str
    candidate_limit: int
    generation_limit: int
    stop_policy: str
    contamination_group_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PLAN_SCHEMA, "schema_version": SCHEMA_VERSION, "id": self.id,
                "parent_claims": list(self.parent_claims), "decision_id": self.decision_id,
                "exact_baseline_tree": self.exact_baseline_tree, "editable_subjects": list(self.editable_subjects),
                "permitted_effects": list(self.permitted_effects), "unchanged_contract_refs": list(self.unchanged_contract_refs),
                "hypothesis": self.hypothesis, "predicate_id": self.predicate_id, "causal_mechanism": self.causal_mechanism,
                "candidate_generation_policy_ref": self.candidate_generation_policy_ref,
                "evaluator_closure_digest": self.evaluator_closure_digest, "protocol_digest": self.protocol.digest(),
                "protocol": self.protocol.to_dict(), "environment_digest": self.environment_digest,
                "budget_ref": self.budget_ref, "candidate_limit": self.candidate_limit,
                "generation_limit": self.generation_limit, "stop_policy": self.stop_policy,
                "contamination_group_id": self.contamination_group_id}

    def digest(self) -> str:
        return sha256_value(self.to_dict())


@dataclass(frozen=True)
class FrozenCandidate:
    id: str
    plan_id: str
    parent_candidate_ids: tuple[str, ...]
    exact_base_tree: str
    patch_object_digest: str
    resulting_tree: str
    changed_subject_refs: tuple[str, ...]
    mechanism_family: str
    generation_method: str
    predicted_effects: tuple[str, ...]
    patch_bytes: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        return {"schema": CANDIDATE_SCHEMA, "schema_version": SCHEMA_VERSION, "id": self.id, "plan_id": self.plan_id,
                "parent_candidate_ids": list(self.parent_candidate_ids), "exact_base_tree": self.exact_base_tree,
                "patch_object_digest": self.patch_object_digest, "resulting_tree": self.resulting_tree,
                "changed_subject_refs": list(self.changed_subject_refs), "mechanism_family": self.mechanism_family,
                "generation_method": self.generation_method, "predicted_effects": list(self.predicted_effects)}


@dataclass(frozen=True)
class SelectionRecord:
    plan_digest: str
    candidate_set_digest: str
    observation_refs: tuple[str, ...]
    comparison: ComparisonResult
    next_required_obligations: tuple[str, ...]

    @property
    def outcome(self) -> str:
        return self.comparison.outcome

    @property
    def selected_candidate_id(self) -> str | None:
        return self.comparison.selected_candidate_id

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SELECTION_SCHEMA, "schema_version": SCHEMA_VERSION, "plan_digest": self.plan_digest,
                "candidate_set_digest": self.candidate_set_digest, "observation_refs": list(self.observation_refs),
                "comparison": self.comparison.to_dict(), "outcome": self.outcome,
                "selected_candidate_id": self.selected_candidate_id, "merge_authorized": False,
                "next_required_obligations": list(self.next_required_obligations)}


@dataclass(frozen=True)
class ProposedPatch:
    candidate_id: str
    patch_bytes: bytes
    expected_base_tree: str
    expected_resulting_tree: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class Refusal:
    reason_codes: tuple[str, ...]
    detail: str = ""
    status: str = "refused"


# ---------- request and plan ----------

def parse_request(raw: bytes) -> dict[str, Any]:
    """Strict, bounded parse of a model-written request. Duplicate keys and NaN refuse."""
    if len(raw) > MAX_REQUEST_BYTES:
        raise InvestigationRefused("investigation request exceeds its size bound")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise InvestigationRefused("duplicate key in investigation request")
            out[key] = value
        return out

    def constant(_value):
        raise InvestigationRefused("non-finite number in investigation request")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvestigationRefused("investigation request is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "schema_version", "acceptance_ids", "hypothesis",
                                                     "predicate_id", "causal_mechanism", "candidates"}:
        raise InvestigationRefused("investigation request must carry exactly the declared fields")
    if value["schema"] != REQUEST_SCHEMA or value["schema_version"] != SCHEMA_VERSION:
        raise InvestigationRefused("unsupported investigation request schema")
    for key in ("hypothesis", "causal_mechanism"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > MAX_STATEMENT:
            raise InvestigationRefused(f"investigation {key} must be bounded nonempty text")
    if value["predicate_id"] not in PREDICATES:
        raise InvestigationRefused("investigation predicate is not registered; a sentence is not a predicate")
    acs = value["acceptance_ids"]
    if not isinstance(acs, list) or not acs or len(acs) > 50 or any(not isinstance(a, str) or not a for a in acs) or len(set(acs)) != len(acs):
        raise InvestigationRefused("investigation must name the acceptance criteria it concerns")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or not candidates or len(candidates) > MAX_CANDIDATES:
        raise InvestigationRefused(f"investigation needs 1 to {MAX_CANDIDATES} alternative candidates")
    ids = set()
    for row in candidates:
        if (not isinstance(row, dict) or set(row) != {"id", "mechanism_family", "patch", "predicted_effects"}
                or not isinstance(row["id"], str) or not row["id"].strip() or row["id"] == BASELINE_ID or row["id"] in ids
                or not safe_path(row["id"]) or "/" in row["id"]
                or not isinstance(row["mechanism_family"], str) or not row["mechanism_family"].strip()
                or not isinstance(row["patch"], str) or not row["patch"].strip() or len(row["patch"].encode("utf-8")) > MAX_PATCH_BYTES
                or not isinstance(row["predicted_effects"], list) or len(row["predicted_effects"]) > 8
                or any(not isinstance(p, str) or len(p) > MAX_STATEMENT for p in row["predicted_effects"])):
            raise InvestigationRefused("candidate entries must carry id, mechanism_family, a bounded unified diff and predicted effects")
        ids.add(row["id"])
    return value


def acceptance_evaluator(red_proof_path: Path, proof_program: Path, argv: tuple[str, ...]) -> Evaluator:
    """The trusted frozen evaluator of the vertical slice: the RED proof's checkpoints replayed
    by the kernel's own proof program. Its closure digest binds the proof bytes and the program
    bytes so a changed test or a changed judge is a different evaluator."""
    closure = sha256_value({"red_proof": sha256_bytes(red_proof_path.read_bytes()),
                            "program": sha256_bytes(proof_program.read_bytes()), "argv": list(argv)})
    return Evaluator("acceptance-green-v1", "1.0", closure, "frozen acceptance contract of this build", argv)


def compile_code_experiment(claim: Mapping[str, Any], subjects: Iterable[str], proposal: Mapping[str, Any],
                            policy: Mapping[str, Any]) -> CodeExperimentPlan:
    """Validate parent claim and exact baseline; resolve editable subjects through the design
    envelope; reject protected evaluator/acceptance paths; validate the predicate; freeze the
    evaluator closure, environment and protocol; validate limits (C08)."""
    for key in ("exact_baseline_tree", "decision_id", "parent_claims"):
        if key not in claim:
            raise InvestigationRefused(f"claim context lacks {key}")
    if not isinstance(claim["exact_baseline_tree"], str) or len(claim["exact_baseline_tree"]) < 40:
        raise InvestigationRefused("exact baseline tree identity required")
    editable = tuple(sorted({p for p in subjects if safe_path(p)}))
    if not editable:
        raise InvestigationRefused("no editable subjects in the design envelope")
    immutable = tuple(sorted(str(p) for p in policy.get("immutable_paths", ())))
    protected = tuple(sorted(str(p) for p in policy.get("protected_paths", ())))
    if set(editable) & (set(immutable) | set(protected)):
        raise InvestigationRefused("editable subjects overlap frozen acceptance tests or protected paths")
    evaluator = policy.get("evaluator")
    if not isinstance(evaluator, Evaluator):
        raise InvestigationRefused("policy must supply the trusted evaluator")
    predicate = PREDICATES[proposal["predicate_id"]]
    limits = policy.get("cost_and_resource_limits", {})
    timeout = limits.get("evaluator_timeout_seconds", EVALUATOR_TIMEOUT_SECONDS)
    try:
        protocol = freeze_protocol({"adapter": predicate["adapter"], "evaluators": [evaluator.authority_closure_digest],
                                    "hard_constraints": ["contract-pass"], "objective_vector": [],
                                    "cost_and_resource_limits": {"evaluator_timeout_seconds": int(timeout),
                                                                 "max_candidates": MAX_CANDIDATES}},
                                   [evaluator])
    except ProtocolRefused as exc:
        raise InvestigationRefused(f"protocol could not be frozen: {exc}") from exc
    identity = {"acceptance_ids": sorted(proposal["acceptance_ids"]), "baseline": claim["exact_baseline_tree"],
                "predicate": proposal["predicate_id"], "hypothesis": proposal["hypothesis"],
                "candidates": sorted(sha256_bytes(c["patch"].encode("utf-8")) for c in proposal["candidates"])}
    return CodeExperimentPlan(
        id=sha256_value(identity), parent_claims=tuple(str(c) for c in claim["parent_claims"]),
        decision_id=str(claim["decision_id"]), exact_baseline_tree=claim["exact_baseline_tree"],
        editable_subjects=editable, permitted_effects=("in-place-apply-evaluate-revert",),
        unchanged_contract_refs=immutable, hypothesis=proposal["hypothesis"], predicate_id=proposal["predicate_id"],
        causal_mechanism=proposal["causal_mechanism"], candidate_generation_policy_ref=str(policy.get("generation_policy", "implementer-proposed")),
        evaluator_closure_digest=evaluator.authority_closure_digest, protocol=protocol,
        environment_digest=str(policy.get("environment_digest", "unknown")), budget_ref=str(policy.get("budget_ref", "stage:implement")),
        candidate_limit=MAX_CANDIDATES, generation_limit=1, stop_policy="fixed-final-analysis",
        contamination_group_id=str(claim.get("contamination_group_id", claim["decision_id"])))


# ---------- the in-place stage ----------

class WorktreeStage:
    """A committed checkout used as a disposable stage: apply one candidate patch, commit it as a
    disposable commit, measure, and hard-reset to the exact baseline commit. Git commit and tree
    identities before and after every step are the proof that nothing leaked between candidates,
    and a committed candidate is what a clean-tree evaluator (the proof program) can judge."""

    COMMITTER = ("-c", "user.name=Dark Factory investigation", "-c", "user.email=factory@invalid")

    def __init__(self, cwd: Path, *, runner: Callable[..., Any] = subprocess.run):
        self.cwd, self.runner = Path(cwd), runner

    def _git(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
        return self.runner(["git", *self.COMMITTER, *args], cwd=self.cwd, input=input, capture_output=True, timeout=120)

    def _out(self, *args: str) -> str:
        proc = self._git(*args)
        if proc.returncode:
            raise InvestigationRefused(f"git {args[0]} failed on the stage: " + proc.stderr.decode("utf-8", "replace")[-300:])
        return proc.stdout.decode("utf-8").strip()

    def head_commit(self) -> str:
        return self._out("rev-parse", "HEAD")

    def head_tree(self) -> str:
        return self._out("rev-parse", "HEAD^{tree}")

    def is_clean(self) -> bool:
        proc = self._git("status", "--porcelain", "--untracked-files=all")
        return proc.returncode == 0 and not proc.stdout.strip()

    def require_baseline(self, baseline_commit: str, baseline_tree: str) -> None:
        if not self.is_clean() or self.head_commit() != baseline_commit or self.head_tree() != baseline_tree:
            raise InvestigationRefused("stage is not the clean exact baseline")

    def touched_paths(self, patch: bytes) -> tuple[str, ...]:
        listing = self._git("apply", "--numstat", "-", input=patch)
        if listing.returncode:
            raise InvestigationRefused("candidate patch could not be listed")
        touched = []
        for line in listing.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                touched.append(parts[2].strip())
        return tuple(sorted(touched))

    def apply(self, patch: bytes) -> tuple[str, ...]:
        check = self._git("apply", "--check", "-", input=patch)
        if check.returncode:
            raise InvestigationRefused("candidate patch does not apply to the exact baseline: " + check.stderr.decode("utf-8", "replace")[-400:])
        touched = self.touched_paths(patch)
        applied = self._git("apply", "-", input=patch)
        if applied.returncode:
            raise InvestigationRefused("candidate patch failed to apply after a clean check")
        return touched

    def commit_disposable(self, label: str) -> str:
        """Stage everything and commit; returns the candidate's exact tree. The commit is
        disposable: `revert` hard-resets to the baseline commit."""
        if self._git("add", "-A", ".").returncode or self._git("commit", "-q", "--no-verify", "-m", f"disposable candidate {label}").returncode:
            raise InvestigationRefused("candidate could not be committed to the disposable stage")
        return self.head_tree()

    def revert(self, baseline_commit: str, baseline_tree: str) -> None:
        if self._git("reset", "-q", "--hard", baseline_commit).returncode or self._git("clean", "-fdq").returncode:
            raise InvestigationRefused("stage could not be reset to the exact baseline")
        self.require_baseline(baseline_commit, baseline_tree)


def freeze_candidate(plan: CodeExperimentPlan, candidate: Mapping[str, Any], stage: WorktreeStage, *,
                     baseline_commit: str) -> FrozenCandidate:
    """Apply the patch to the exact baseline, refuse extra/frozen/escaping paths, derive the
    resulting tree by committing it as a disposable commit, revert. Freezing precedes every
    measurement."""
    stage.require_baseline(baseline_commit, plan.exact_baseline_tree)
    patch = candidate["patch"].encode("utf-8")
    touched = stage.apply(patch)
    try:
        if not touched:
            raise InvestigationRefused("candidate patch changes nothing")
        for path in touched:
            if safe_path(path) is None:
                raise InvestigationRefused(f"candidate patch touches an unsafe path {path!r}")
            if path in plan.unchanged_contract_refs:
                raise InvestigationRefused(f"candidate patch edits frozen acceptance test {path!r}")
            if path not in plan.editable_subjects:
                raise InvestigationRefused(f"candidate patch touches {path!r} outside the design envelope")
            if (stage.cwd / path).is_symlink():
                raise InvestigationRefused("candidate introduced a symlink")
        resulting = stage.commit_disposable(str(candidate["id"]))
        if resulting == plan.exact_baseline_tree:
            raise InvestigationRefused("candidate patch changes nothing")
    finally:
        stage.revert(baseline_commit, plan.exact_baseline_tree)
    return FrozenCandidate(str(candidate["id"]), plan.id, (BASELINE_ID,), plan.exact_baseline_tree, sha256_bytes(patch),
                           resulting, touched, str(candidate["mechanism_family"]), "implementer-proposed",
                           tuple(str(p) for p in candidate["predicted_effects"]), patch)


# ---------- execution ----------

def execute_registered(plan: CodeExperimentPlan, candidates: Iterable[FrozenCandidate], stage: WorktreeStage, *,
                       baseline_commit: str, runner: Callable[..., Any] = subprocess.run,
                       environment: Mapping[str, str] | None = None,
                       check_stop: Callable[[], None] = lambda: None) -> tuple[AdmissibleObservation, ...]:
    """Run the frozen evaluator once per candidate, baseline first, each on the exact baseline
    plus that candidate's patch, reverting between runs. Every attempt is captured; a crash or
    timeout is an incomplete observation, never a number."""
    evaluator = plan.protocol.evaluators[0]
    timeout = plan.protocol.cost_and_resource_limits["evaluator_timeout_seconds"]
    frozen = {c.id: c for c in candidates}
    order = [BASELINE_ID] + sorted(frozen)
    observations = []
    for candidate_id in order:
        check_stop()
        stage.require_baseline(baseline_commit, plan.exact_baseline_tree)
        if candidate_id != BASELINE_ID:
            stage.apply(frozen[candidate_id].patch_bytes)
            if stage.commit_disposable(candidate_id) != frozen[candidate_id].resulting_tree:
                stage.revert(baseline_commit, plan.exact_baseline_tree)
                raise InvestigationRefused("candidate tree differs from its frozen identity")
        started = time.perf_counter_ns()
        try:
            proc = runner(list(evaluator.argv), cwd=stage.cwd, capture_output=True, timeout=timeout, env=environment)
            raw = {"evaluator_id": evaluator.id, "protocol_digest": plan.protocol.digest(), "status": "returned",
                   "exit_code": int(proc.returncode), "duration_ns": time.perf_counter_ns() - started,
                   "output_sha256": sha256_bytes(_bytes(proc.stdout) + _bytes(proc.stderr)), "hard_pass": proc.returncode == 0, "metrics": {}}
        except subprocess.TimeoutExpired:
            raw = {"evaluator_id": evaluator.id, "protocol_digest": plan.protocol.digest(), "status": "timeout",
                   "exit_code": None, "duration_ns": time.perf_counter_ns() - started, "output_sha256": None, "metrics": {},
                   "limitations": [f"bounded workload did not finish within {timeout}s in this environment"]}
        except OSError as exc:
            raw = {"evaluator_id": evaluator.id, "protocol_digest": plan.protocol.digest(), "status": "not_started",
                   "exit_code": None, "duration_ns": None, "output_sha256": None, "metrics": {}, "limitations": [type(exc).__name__]}
        finally:
            stage.revert(baseline_commit, plan.exact_baseline_tree)
        observations.append(validate_observation(plan.protocol, candidate_id, raw))
    return tuple(observations)


def _bytes(value: Any) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else str(value).encode("utf-8", "replace")


# ---------- comparison ----------

def select_finite(baseline_id: str, rows: Iterable[Mapping[str, Any]]) -> ComparisonResult:
    """Deterministic screening over `{id, metric, hard_pass, complete}` rows (C09)."""
    return screen_finite(baseline_id, rows)


def compare_candidates(plan: CodeExperimentPlan, observations: Iterable[AdmissibleObservation],
                       candidates: Iterable[FrozenCandidate] = ()) -> SelectionRecord:
    """Group observations by candidate under the frozen protocol; require the scheduled set
    (baseline plus every frozen candidate); hard constraints first; lower metric wins; ties
    retain the baseline. The finite metric is 0 for a passing contract, 1 otherwise."""
    frozen = {c.id: c for c in candidates}
    scheduled = {BASELINE_ID, *frozen}
    rows: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if observation.protocol_digest != plan.protocol.digest():
            raise InvestigationRefused("observation from another protocol cannot be compared")
        if observation.candidate_id in rows:
            raise InvestigationRefused("duplicate observation for one candidate under one protocol")
        rows[observation.candidate_id] = {"id": observation.candidate_id, "complete": observation.complete,
                                          "hard_pass": observation.hard_pass if observation.complete else False,
                                          "metric": (0 if observation.hard_pass else 1) if observation.complete else None}
    missing = sorted(scheduled - set(rows))
    if missing:
        comparison = ComparisonResult(plan.protocol.adapter, "insufficient", ("measurement_missing",), None, (),
                                      tuple({"id": m, "reason": "not observed"} for m in missing), {}, {}, ("scheduled sample coverage not met",))
    else:
        comparison = select_finite(BASELINE_ID, [rows[k] for k in sorted(rows)])
    candidate_set = sha256_value({"baseline": plan.exact_baseline_tree, "candidates": sorted(c.patch_object_digest for c in frozen.values())})
    refs = tuple(sha256_value(o.to_dict()) for o in observations)
    return SelectionRecord(plan.digest(), candidate_set, refs, comparison,
                           ("fresh-green-gate", "independent-qualification", "unchanged-protected-policy"))


def prepare_selected_patch(selection: SelectionRecord, current_tree: str, plan: CodeExperimentPlan,
                           candidates: Iterable[FrozenCandidate]) -> ProposedPatch | Refusal:
    """Only a provisional, non-baseline selection on the exact original baseline becomes a
    proposal to the normal commit authority. A moved base is `rebase_required`; measurements are
    never reused as current."""
    if selection.plan_digest != plan.digest():
        return Refusal(("plan_mismatch",), "selection belongs to another plan")
    if selection.outcome != "provisional" or selection.selected_candidate_id in (None, BASELINE_ID):
        return Refusal(("nothing_to_apply",), f"outcome {selection.outcome}: baseline retained or no selection")
    if current_tree != plan.exact_baseline_tree:
        return Refusal(("rebase_required",), "tree moved since the plan's exact baseline")
    chosen = next((c for c in candidates if c.id == selection.selected_candidate_id), None)
    if chosen is None or sha256_bytes(chosen.patch_bytes) != chosen.patch_object_digest:
        return Refusal(("candidate_identity_mismatch",), "selected candidate bytes do not match its frozen digest")
    return ProposedPatch(chosen.id, chosen.patch_bytes, plan.exact_baseline_tree, chosen.resulting_tree, chosen.changed_subject_refs)


def lesson_proposal(plan: CodeExperimentPlan, selection: SelectionRecord, candidates: Iterable[FrozenCandidate]) -> dict[str, Any]:
    """A bounded, PROPOSED lesson candidate: what was compared, under which contract, with which
    outcome. Admission is a separate evaluated step (WP11); nothing here is admitted."""
    return {"schema": "dark-factory/lesson-proposal", "schema_version": SCHEMA_VERSION, "status": "proposed",
            "plan_digest": plan.digest(), "predicate_id": plan.predicate_id, "hypothesis": plan.hypothesis,
            "causal_mechanism": plan.causal_mechanism, "outcome": selection.outcome,
            "selected_candidate_id": selection.selected_candidate_id,
            "mechanism_families": sorted({c.mechanism_family for c in candidates}),
            "scope": "this contract, workload, environment and decision policy only",
            "not_established": ["global optimality", "generality beyond the measured cohort", "admission"]}


def write_record(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
