#!/usr/bin/env python3
"""Mutation-test copied factory trust-root code without touching the live worktree.

Every defect gets its own copy of the trust root, so the defects are independent by
construction and are evaluated CONCURRENTLY (D-073). Each copy's focused suite stops at its
first VALID KILL: a detector that was green on the unmutated baseline, in this same
environment, and went red by reporting test failures rather than by failing to load.

What changed on 2026-09-18: the runner used to read a returncode and call any red "caught".
Three different things produce that returncode -- an assertion failing, a module failing to
import, and a process running out of time -- and only the first is evidence about the defect.
The third one aborted the entire family: `subprocess.TimeoutExpired` from a single file
propagated out of the worker pool and discarded 903 verdicts
(https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/35324653207 at 73d557c).

So this runner now records what it observed, per mutant, in its own immutable JSON file, and
publishes a manifest naming every mutant the catalogue expects. `harness/factory_mutations/
outcomes.py` owns the vocabulary: caught, survived, infra_error, timeout, not_run. A run with
any of the last three is INCOMPLETE -- neither green nor a bypass -- and says which mutants
it failed to observe. Shards are a deterministic split of the same catalogue against one
baseline, aggregated only when between them they measured every expected mutant exactly once.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from budget import budget_seconds, deadline, drift_warning, load, remaining  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outcomes import (  # noqa: E402
    CAUGHT, FAILED, INFRA_ERROR, NOT_RUN, PASSED, SURVIVED, TIMEOUT, MutationResult,
    aggregate_manifests, digest_record, digest_text, run_detector, shard_members,
    write_atomic,
)

MUTATION_DIR = Path(__file__).resolve().parent
DEFECT_FILES = (
    MUTATION_DIR / "defects.json",
    MUTATION_DIR / "native_ci_defects.json",
    MUTATION_DIR / "post_merge_defects.json",
    MUTATION_DIR / "benchmark_defects.json",
    MUTATION_DIR / "spine_defects.json",
    MUTATION_DIR / "independence_defects.json",
    MUTATION_DIR / "bootstrap_defects.json",
    MUTATION_DIR / "genesis_driver_defects.json",
)
IMMUNITY = ROOT / "harness" / "immunity.py"
COPY_DIRS = ("factory_kernel", ".factory/methods")
COPY_FILES = (
    ".github/workflows/dark-factory-ci.yml",
    ".github/workflows/dark-factory-worker.yml",
    ".github/workflows/dark-factory-trust-root.yml",
    ".github/workflows/dark-factory-main-regression.yml",
    ".github/workflows/dark-factory-branch-cleanup.yml",
    ".github/workflows/dark-factory-identity-canary.yml",
    ".github/workflows/dark-factory-trajectory.yml",
    ".github/workflows/dark-factory-owner-stop.yml",
    ".github/workflows/dark-factory-frontdoor-prepare.yml",
    ".github/workflows/dark-factory-programme-publish.yml",
    ".github/workflows/dark-factory-test-author-probe.yml",
    "scripts/factory_test_author_probe.py",
    # The stop script's own words are what the read-only planner pins as "unreadable" (WP00);
    # the planner's detector reads the script, so the copy must carry it.
    "scripts/factory-stop.sh",
    ".factory/architecture.json",
    ".factory/tcb.json",
    ".factory/prompts/holdout.md",
    ".factory/prompts/investigate.md",
    ".factory/prompts/contract.md",
    "factory_kernel/git_authority.py",
    ".factory/methods/diagnosing-bugs.md",
    ".factory/prompts/test-author.md",
    ".factory/prompts/review-spec.md",
    ".factory/prompts/repair.md",
    ".factory/prompts/architecture.md",
    ".factory/prompts/plan.md",
    ".factory/prompts/review-standards.md",
    ".factory/prompts/context.md",
    ".factory/prompts/conformance.md",
    ".factory/evidence-spine.json",
    ".factory/kernel.json",
    # WP01: the trusted project profile every publication/execution constant is read from.
    ".factory/project-profile.json",
    ".factory/authority-profiles.json",
    ".factory/locks/floor.json",
    "app/backend/main.py",
    "scripts/frontier_filter.py",
    "scripts/factory_security.py",
    "scripts/factory_evidence.py",
    "scripts/factory_evidence_spine.py",
    "scripts/factory_provenance.py",
    "scripts/factory_lease.py",
    "scripts/factory_protocol.py",
    "scripts/factory_artifacts.py",
    "scripts/factory_shapes.py",
    "scripts/factory_impact.py",
    "scripts/factory_architecture.py",
    "scripts/factory_proof.py",
    "scripts/factory_architecture_guard.py",
    "harness/bootstrap_e2e.py",
    "harness/harness.config.json",
    "harness/serve.py",
    "harness/e2e.py",
    "harness/appproc.py",
    "harness/merge_verify.py",
    "harness/post_merge.py",
    "harness/observe.py",
    "harness/immunity.py",
    "harness/bootstrap_verify.py",
    "harness/genesis_validate.py",
    "harness/genesis_collect.py",
    "harness/rehearsal.py",
    "harness/genesis-recipe.json",
    "harness/mutations/run.py",
    "harness/mutations/defects.json",
    "harness/budgets.json",
    "harness/budget.py",
    "harness/ci.py",
    "harness/static.py",
    "harness/unit.py",
    # The check that keeps this catalogue honest, and its own detector. Both are copied so a
    # mutation of the anchor check can be injected and caught like any other (D-076).
    "harness/mutation_anchors.py",
    "tests/factory/test_factory_mutation_anchors.py",
    # A base that moved is noticed before anything is paid for, and recovered from without a
    # maintainer deleting a marker by hand (D-077).
    "tests/factory/test_factory_base_move.py",
    # The detector four re-head defects name. It existed, and was not here, so those
    # defects escaped every run of the family that was supposed to catch them (D-078).
    "tests/factory/test_factory_rehead_guard_files.py",
    # The wrapper must accept every call the kernel makes of the program it wraps; the
    # routing that makes that necessary lives one class above the rehearsal (D-079).
    "tests/factory/test_factory_spine_routing.py",
    # This runner, in the copy: a mutation of its own concurrency or its own accounting has to
    # be injectable somewhere its detector can read it. The copy is never executed as a runner.
    "harness/factory_mutations/run.py",
    # ...and the manifests it reads. `load_defects()` requires all eight, so without them the
    # runner cannot be loaded from inside a copy at all, and any test that asks it a question
    # dies with `required factory mutation manifest missing` -- which is what turned the whole
    # family's baseline red in run 34125312694 (D-081). They are data, never injected.
    "harness/factory_mutations/defects.json",
    "harness/factory_mutations/native_ci_defects.json",
    "harness/factory_mutations/post_merge_defects.json",
    "harness/factory_mutations/benchmark_defects.json",
    "harness/factory_mutations/spine_defects.json",
    "harness/factory_mutations/independence_defects.json",
    "harness/factory_mutations/bootstrap_defects.json",
    "harness/factory_mutations/genesis_driver_defects.json",
    "tests/factory/test_factory_mutation_budget.py",
    "tests/factory/test_factory_ladder_budget.py",
    "harness/focused.py",
    "tests/factory/test_factory_security.py",
    "tests/factory/test_e2e_contract.py",
    "tests/factory/test_e2e_stream_evidence.py",
    "tests/factory/test_factory_scripts_import.py",
    "tests/factory/test_factory_contract_shape.py",
    "tests/factory/test_factory_single_publisher.py",
    "tests/factory/test_factory_prompt_contracts.py",
    "tests/factory/test_factory_downstream_contracts.py",
    ".factory/methods/code-review-standards.md",
    ".factory/methods/code-review-spec.md",
    "tests/factory/fixtures/contracts/run-33912650468-issue-49-keyed.json",
    "tests/factory/test_factory_artifact_shapes.py",
    "tests/factory/fixtures/context/run-33916377607-issue-49-context.raw.json",
    "tests/factory/fixtures/context/run-33916377607-issue-49-design.raw.json",
    "tests/factory/fixtures/context/run-33916377607-issue-49-task-contract.json",
    "tests/factory/fixtures/context/run-33914596611-issue-49-architecture-governor.raw.json",
    "tests/factory/fixtures/context/run-33914596611-issue-49-context.json",
    "tests/factory/fixtures/context/run-33914596611-issue-49-design.json",
    "tests/factory/fixtures/context/run-33914596611-issue-49-task-contract.json",
    "tests/factory/test_factory_workflow_hygiene.py",
    "tests/factory/test_factory_worker_throughput.py",
    # The rule that keeps this suite runnable from the copy this runner builds: a test that
    # only passes where the tree around it is a repository makes the whole family unusable,
    # because every copy runs the suite (D-074).
    "tests/factory/test_factory_suite_hermetic.py",
    "tests/factory/test_factory_provider_retry.py",
    "tests/factory/test_factory_failed_stage_telemetry.py",
    "tests/factory/fixtures/provider/run-33933101233-test-author-stream-closed.json",
    "tests/factory/test_factory_resume.py",
    "tests/factory/test_factory_pack_base.py",
    "tests/factory/test_factory_rehead_red.py",
    "tests/factory/test_factory_static_gate.py",
    "tests/factory/test_factory_trusted_programs.py",
    "tests/factory/test_factory_methods.py",
    "tests/factory/test_factory_review_axes.py",
    "tests/factory/test_factory_repro_loop.py",
    "tests/factory/test_factory_attached_round_trip.py",
    "tests/factory/fixtures/proof/pr74-proof-block.txt",
    "tests/factory/fixtures/proof/run10-final-green-proof.json",
    "tests/factory/test_factory_prompt_paths.py",
    "tests/factory/test_factory_repro_boundary.py",
    "tests/factory/test_factory_issue_snapshot.py",
    "tests/factory/test_factory_triage.py",
    "tests/factory/test_factory_trust_root_authority.py",
    "tests/factory/test_factory_holdout_blind.py",
    "tests/factory/test_factory_lifecycle_authority.py",
    "tests/factory/test_factory_lease_authority.py",
    "tests/factory/test_factory_dependency_justification.py",
    "tests/factory/test_factory_commit_identity.py",
    "tests/factory/test_factory_config_consumed.py",
    "tests/factory/test_factory_triage_window.py",
    "tests/factory/test_factory_security_evidence.py",
    "tests/factory/test_factory_evidence.py",
    "tests/factory/test_factory_architecture_guard.py",
    "tests/factory/test_factory_worker_authority.py",
    "tests/factory/test_factory_merge_handoff.py",
    "tests/factory/test_factory_programme.py",
    "tests/factory/test_factory_trajectory.py",
    "tests/factory/test_frontdoor_intent.py",
    "tests/factory/test_frontdoor_programme.py",
    "tests/factory/test_frontdoor_control.py",
    "tests/factory/test_control_issue.py",
    "tests/factory/test_frontdoor_http.py",
    "tests/factory/test_frontdoor_exploration.py",
    "tests/factory/test_frontdoor_prepare.py",
    "tests/factory/test_frontdoor_repository.py",
    "tests/factory/test_frontdoor_synthesis.py",
    "tests/factory/test_publication_request.py",
    "tests/factory/test_publication_source.py",
    "tests/factory/test_publication_currency.py",
    "tests/factory/test_publication_http.py",
    "tests/factory/test_factory_test_author_probe.py",
    "tests/factory/test_publication_admission.py",
    "tests/factory/test_publication_observation.py",
    "tests/factory/test_publication_client.py",
    "tests/factory/test_publication_guard.py",
    "tests/factory/test_publication_effects.py",
    "tests/factory/test_publication_worker.py",
    "tests/factory/test_publication_dispatch.py",
    "tests/factory/test_publication_dispatch_http.py",
    "tests/factory/test_publication_strategy.py",
    "tests/factory/test_programme_replan.py",
    "tests/factory/test_programme_turnover.py",
    "tests/factory/test_execution_fence.py",
    "tests/factory/test_execution_budget.py",
    "tests/factory/test_execution_exchange.py",
    "tests/factory/test_execution_worker.py",
    # WP00: read-only dispatch planning and typed diagnostic retention, with their mutants.
    "tests/factory/test_dispatch_plan.py",
    "tests/factory/test_execution_probe.py",
    # WP01: canonical journal primitive, project profile, and the baseline-recorded flows the
    # journal must reproduce byte for byte (the recorder is a test helper, never production).
    "tests/factory/test_project_events.py",
    "tests/factory/test_project_profile.py",
    "tests/factory/project_events_fixture.py",
    "tests/factory/fixtures/project-events/intake.json",
    "tests/factory/fixtures/project-events/budget.json",
    "tests/factory/fixtures/project-events/exploration.json",
    "tests/factory/fixtures/project-events/replacement.json",
    # R02 source subjects and WP04 claims, with their detectors.
    "tests/factory/test_code_subjects.py",
    "tests/factory/test_claims.py",
    # R02 vertical slice: frozen protocols, candidate lifecycle and search policy.
    "tests/factory/test_evaluation_protocol.py",
    "tests/factory/test_code_experiments.py",
    "tests/factory/test_search_policy.py",
    "tests/factory/test_factory_workflow_contexts.py",
    "tests/factory/test_attestations.py",
    "tests/factory/test_proof_dependencies.py",
    "tests/factory/test_proof_store.py",
    "tests/factory/test_factory_attestation_companions.py",
    # WP02 accounting: the meter, the gateway contract, reconciliation and the probe bundle.
    "tests/factory/test_validation_meter.py",
    "tests/factory/test_provider_gateway.py",
    "tests/factory/test_billing_reconciliation.py",
    "tests/factory/test_probe_bundle.py",
    "tests/factory/test_claim_scheduler.py",
    "tests/factory/test_project_graph.py",
    "tests/factory/test_claim_views.py",
    "tests/factory/test_lease_store.py",
    "tests/factory/test_programme_transition.py",
    "tests/factory/test_transition_journal.py",
    "tests/factory/test_transition_views.py",
    "tests/factory/test_replacement_intent.py",
    "tests/factory/test_frontdoor_hosted.py",
    "tests/factory/test_factory_continuation.py",
    "tests/factory/test_factory_github_e2e_bootstrap.py",
    "tests/factory/test_factory_merge_verify.py",
    "tests/factory/test_factory_post_merge.py",
    "tests/factory/test_factory_post_merge_runtime.py",
    "tests/factory/test_factory_benchmark.py",
    "tests/factory/test_factory_immunity.py",
    "tests/factory/test_factory_provenance.py",
    "tests/factory/test_factory_note_identity.py",
    "tests/factory/test_factory_evidence_closure.py",
    "tests/factory/test_claim_explanation.py",
    "tests/factory/test_evidence_retention.py",
    "tests/factory/test_preflight.py",
    "tests/factory/test_preflight_prepare.py",
    "tests/factory/test_exploration.py",
    "tests/factory/test_reconsideration.py",
    "tests/factory/test_factory_feedback.py",
    "tests/factory/test_strategy_rejection.py",
    "tests/factory/test_exploration_reasoner.py",
    "tests/factory/test_exploration_repository.py",
    "tests/factory/test_programme_strategy.py",
    "tests/factory/test_decision_history.py",
    "tests/factory/test_factory_independence.py",
    "tests/factory/test_factory_bootstrap.py",
    "tests/factory/test_factory_genesis_driver.py",
    "tests/factory/test_factory_genesis_collect.py",
    "tests/factory/test_factory_validation_rehearsal.py",
    "tests/factory/test_factory_refusals.py",
    "tests/factory/test_factory_mutation_ownership.py",
    "tests/factory/test_factory_spine.py",
    "tests/factory/test_factory_evidence_spine_runtime.py",
    "tests/factory/test_factory_holdout_evidence.py",
    "tests/factory/test_factory_validation_stage_telemetry.py",
    "tests/factory/test_factory_authority_bounds.py",
    "tests/factory/test_factory_stream_timeouts.py",
    "tests/factory/test_factory_effort_and_stream_logs.py",
    "tests/factory/test_factory_red_evidence_and_stop.py",
    "scripts/factory_effort_probe.py",
    ".factory/prompts/implement.md",
    "scripts/factory_read_scope_probe.py",
    "tests/factory/test_factory_read_scope_and_draft_deadline.py",
    "tests/factory/test_factory_guard_checkpoints.py",
    "tests/factory/test_factory_stage_runs.py",
    "scripts/factory_thinking_cap_probe.py",
    "tests/factory/test_factory_thinking_cap.py",
    "tests/factory/fixtures/contracts/run-34015187797-issue-49-guard-ids.json",
    "tests/factory/test_factory_guard_behavior_ids.py",
    "scripts/factory_models.py",
    "tests/factory/test_factory_model_overrides.py",
    "tests/factory/test_factory_guard_files_untouched.py",
    "FACTORY_RULES.md",
    "tests/factory/test_factory_cap_ends_the_loop.py",
    "tests/factory/test_factory_red_handback.py",
    "FACTORY.md",
    ".factory/decisions.md",
    "tests/factory/test_factory_carry.py",
    # Merged from the replayed branch during rebase.
    "tests/factory/test_lessons.py",
    "tests/factory/test_tcb.py",
    "tests/factory/test_cli_compatibility_spike.py",
    "tests/factory/cli_stub.py",
    "harness/cli_compatibility_spike.py",
    # WP02 gateway transport: the loopback server, its tests and the stub child the launcher test starts.
    "factory_kernel/gateway_server.py",
    "tests/factory/test_gateway_server.py",
    "tests/factory/gateway_stub_app.py",
    # WP06 capabilities and the in-process effect broker on the merge path, with the merge detector.
    "tests/factory/test_capabilities.py",
    "tests/factory/test_effect_broker.py",
    "tests/factory/test_factory_merge_authorized.py",
    # WP08 experiment registry: the deterministic boundary family and the registry's refusals.
    "tests/factory/test_experiments.py",
    "tests/factory/test_predictions.py",
    # WP09 outcome routing: the structural classifier and the assessment that records it.
    "tests/factory/test_outcome_router.py",
    # WP10A project graph transport and its Front Door routes.
    "tests/factory/test_project_graph_http.py",
    # WP11 calibration: the strict join and the preregistered protocol it reads.
    "tests/factory/test_calibration.py",
    "harness/experiments/learning_protocol.json",
    # WP11 experience packets: role-scoped retrieval at the one payload funnel.
    "tests/factory/test_experience.py",
    # WP12 governed maintenance: deterministic classification, proposals only.
    "tests/factory/test_maintenance.py",
    ".factory/maintenance-policy.json",
    # WP10B decision graph UI: the static assets are inside factory_kernel/ (copied as a directory).
    "tests/factory/test_decision_graph_ui.py",
    # WP08 contained experiment execution under a lease.
    "tests/factory/test_experiment_runner.py",
    # The vocabulary this runner records its own observations in, and its detector. Without the
    # module in the copy, run.py cannot be imported from inside one at all, so every test that
    # asks it a question dies the way the missing manifests once did (D-081).
    "harness/factory_mutations/outcomes.py",
    "tests/factory/test_mutation_execution_outcomes.py",
    # The kernel policy files' protection, and the example policy it reads.
    "tests/factory/test_policy_path_protection.py",
    "tests/factory/fixtures/policy/lesson-policy.example.json",
)
# A test file, not every file the copy needs. `startswith("tests/")` also selected the recorded
# JSON and text fixtures the tests read, and `run_tests` executed each of them as a Python
# program: three of them are not valid Python, exited non-zero, and made the baseline red, so
# this family refused to start with `focused baseline is red` every time it was reached. The
# nested run inside harness/mutations/run.py timed out before reaching it, which is why nobody
# saw it (D-073).
TEST_FILES = tuple(
    rel for rel in COPY_FILES
    if rel.startswith("tests/") and rel.endswith(".py") and Path(rel).name.startswith("test_")
)
BUDGET = load()
FAMILY_BUDGET_SECONDS = budget_seconds(BUDGET, scope="factory-family")
# Sub-bounds of the family, not additions to it. Each is applied together with whatever is
# LEFT of the family's own deadline, so a copy can never outlive the family that owns it, and
# the family can never outlive the mutation rung that owns THAT (D-075).
FILE_SECONDS = budget_seconds(BUDGET, scope="factory-focused-file")
IMMUNITY_SECONDS = budget_seconds(BUDGET, scope="immunity-check")
FAMILY_DEADLINE = deadline(BUDGET, "factory-family")


# An anchor that no longer matches is a defect that was never injected, so nothing about it
# was observed. `mutation_anchors.py` catches this in the static rung; when it reaches here it
# is an infrastructure fact about the catalogue, still reported under its historical marker.
ANCHOR_REASON = "anchor missing/non-unique"


def build_copy(parent: Path) -> Path:
    target = parent / "root"
    for rel in COPY_DIRS:
        src, dst = ROOT / rel, target / rel
        if not src.is_dir():
            raise RuntimeError(f"required factory mutation directory missing: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    for rel in COPY_FILES:
        src, dst = ROOT / rel, target / rel
        if not src.is_file():
            raise RuntimeError(f"required factory mutation input missing: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return target


def worker_count() -> int:
    """How many defect copies are evaluated at once.

    Each copy is a separate tree and a separate set of child processes, so the only shared
    resource is the machine. `FACTORY_MUTATION_WORKERS` exists for a host that wants to hold
    the runner to one core; it can only change how long the run takes, never its verdict.
    """
    override = os.environ.get("FACTORY_MUTATION_WORKERS", "").strip()
    if override.isdigit() and int(override) > 0:
        return min(int(override), 32)
    return max(1, min(8, os.cpu_count() or 2))


def results_dir() -> Path:
    """Where each mutant's own result file is published.

    One file per mutant, written atomically, is what makes a cancelled or timed-out run
    readable afterwards. Eight workers appending to one JSONL would interleave instead.
    """
    configured = os.environ.get("FACTORY_MUTATION_RESULTS", "").strip()
    target = Path(configured) if configured else Path(tempfile.gettempdir()) / f"factory-mutation-results-{os.getpid()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def source_ref() -> str:
    """The exact bytes this family is measuring, as one digest.

    A cached detector result is only reusable while every copied file, the catalogue and the
    interpreter are identical; this is the first half of that identity.
    """
    entries = []
    for rel in COPY_FILES:
        path = ROOT / rel
        entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""))
    for rel in COPY_DIRS:
        base = ROOT / rel
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.is_file():
                entries.append((path.relative_to(ROOT).as_posix(),
                                hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest_text(json.dumps(sorted(entries), separators=(",", ":")))


def environment_ref() -> str:
    """The toolchain half of the cache identity: a different interpreter is a different run."""
    return digest_record({"python": sys.version.split()[0], "platform": sys.platform,
                          "executable_kind": Path(sys.executable).name,
                          "files": len(TEST_FILES)})


_DETECTOR_REFERENCE = re.compile(r"tests/factory/test_[A-Za-z0-9_]+\.py")


def associated_detectors(defect: dict) -> tuple[str, ...]:
    """The detectors this defect's own catalogue entry points at.

    `mutation_anchors.py` already requires every factory defect's `why` to name the test that
    is supposed to notice it, so the catalogue -- not a guess about file names -- is where the
    likely detector comes from. The module's same-named test is a second, weaker hint.
    """
    named = [rel for rel in _DETECTOR_REFERENCE.findall(str(defect.get("why", "")))
             if rel in TEST_FILES]
    stem = Path(str(defect.get("file", ""))).stem
    sibling = f"tests/factory/test_{stem}.py"
    if sibling in TEST_FILES and sibling not in named:
        named.append(sibling)
    return tuple(dict.fromkeys(named))


def detector_order(defect: dict, order: tuple[str, ...], causal: dict | None = None) -> tuple[str, ...]:
    """Which detector to ask first, without ever narrowing the set that must be asked.

    Order is a permutation: the previously observed causal detector for THIS defect in THIS
    environment, then the detectors its catalogue entry names, then everything else in the
    baseline's cheapest-first order. A survivor still runs every one of them, so the ordering
    can only change how quickly a kill is found, never whether a bypass is reported.
    """
    front: list[str] = []
    remembered = (causal or {}).get(defect.get("id", ""))
    if isinstance(remembered, str) and remembered in TEST_FILES:
        front.append(remembered)
    front.extend(rel for rel in associated_detectors(defect) if rel not in front)
    rest = [rel for rel in (order or TEST_FILES) if rel not in front]
    missing = [rel for rel in TEST_FILES if rel not in front and rel not in rest]
    return tuple(front + rest + missing)


def cheapest_first(durations: dict[str, float]) -> tuple[str, ...]:
    """Every test file, ordered by what the baseline measured it to cost.

    The set is always the whole suite, so nothing is dropped and an escape is still an escape;
    an unmeasured file sorts last rather than being assumed cheap.
    """
    return tuple(sorted(TEST_FILES, key=lambda rel: (durations.get(rel, float("inf")), rel)))


def detector_seconds() -> float:
    """A detector's bound: its own, and never more than the family has left."""
    return min(FILE_SECONDS, remaining(FAMILY_DEADLINE))


def suite_env(root: Path) -> dict:
    env = dict(os.environ)
    python_paths = [str(root), str(root / "scripts")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env


def run_tests(
    root: Path, order: tuple[str, ...] | None = None, baseline: dict | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, float]]:
    """Run the focused suite in `root`, stopping at the first VALID KILL.

    Returns the verdict and what each file that ran cost. Stopping early is verdict-identical
    only because a detector that did not go red does not stop the suite, and a suite with no
    kill still runs every file. `baseline` names the detectors allowed to prove one; without
    it every detector is trusted, which is what the unmutated baseline run itself needs.
    """
    outputs: list[str] = []
    durations: dict[str, float] = {}
    env = suite_env(root)
    statuses: dict[str, str] = {}
    for rel in order or TEST_FILES:
        if baseline is not None and baseline.get("detectors", {}).get(rel, {}).get("status") != PASSED:
            continue
        status, _, output, elapsed_ns = run_detector(
            [sys.executable, rel], cwd=root, env=env, seconds=detector_seconds())
        durations[rel] = round(elapsed_ns / 1_000_000_000, 3)
        statuses[rel] = status
        outputs.append(output)
        if status != PASSED:
            return subprocess.CompletedProcess([rel], 1, "\n".join(outputs), status), durations
    return subprocess.CompletedProcess([], 0, "\n".join(outputs), PASSED), durations


def run_baseline(root: Path) -> dict:
    """Every detector, on the unmutated copy, with nothing stopping the run.

    A detector that is red here cannot prove a kill later: its redness is a fact about the
    tree. Recording all of them, rather than refusing at the first, is what lets the run say
    which detectors were unusable instead of only that something was.
    """
    env = suite_env(root)
    detectors: dict[str, dict] = {}
    for rel in TEST_FILES:
        status, returncode, output, elapsed_ns = run_detector(
            [sys.executable, rel], cwd=root, env=env, seconds=detector_seconds())
        detectors[rel] = {"status": status, "exit_code": returncode,
                          "seconds": round(elapsed_ns / 1_000_000_000, 3),
                          "tail": output[-600:] if status != PASSED else ""}
    manifest = {"schema": "dark-factory/mutation-baseline", "schema_version": "1.0",
                "source_ref": source_ref(), "environment_ref": environment_ref(),
                "detectors": detectors}
    manifest["baseline_ref"] = digest_record(
        {"source_ref": manifest["source_ref"], "environment_ref": manifest["environment_ref"],
         "statuses": {rel: row["status"] for rel, row in detectors.items()}})
    return manifest


def baseline_durations(manifest: dict) -> dict[str, float]:
    return {rel: row["seconds"] for rel, row in manifest.get("detectors", {}).items()}


def unusable_detectors(manifest: dict) -> list[str]:
    return sorted(rel for rel, row in manifest.get("detectors", {}).items()
                  if row.get("status") != PASSED)


def inject(root: Path, defect: dict) -> bool:
    path = root / defect["file"]
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    anchor = defect["find"]
    if text.count(anchor) != 1:
        return False
    path.write_text(text.replace(anchor, defect["replace"], 1), encoding="utf-8")
    return True


def immunity_is_green() -> bool:
    if not IMMUNITY.is_file():
        print("FACTORY_MUTATIONS_REFUSED harness/immunity.py is missing", flush=True)
        return False
    proc = subprocess.run(
        [sys.executable, str(IMMUNITY)], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=min(IMMUNITY_SECONDS, remaining(FAMILY_DEADLINE)),
    )
    if proc.stdout.strip():
        print(proc.stdout.strip(), flush=True)
    if proc.returncode != 0:
        print((proc.stderr or "")[-2000:], flush=True)
        print("FACTORY_MUTATIONS_REFUSED active immunity obligation failed", flush=True)
        return False
    if "IMMUNITY_OK" not in (proc.stdout or ""):
        print("FACTORY_MUTATIONS_REFUSED immunity checker emitted no positive marker", flush=True)
        return False
    return True


def load_defects() -> list[dict]:
    defects: list[dict] = []
    for path in DEFECT_FILES:
        if not path.is_file():
            raise RuntimeError(f"required factory mutation manifest missing: {path.name}")
        loaded = json.loads(path.read_text(encoding="utf-8")).get("defects")
        if not isinstance(loaded, list) or not loaded:
            raise RuntimeError(f"no defects configured in {path.name}")
        defects.extend(loaded)
    ids = [str(defect.get("id", "")) for defect in defects]
    if len(ids) != len(set(ids)) or not all(ids):
        raise RuntimeError("factory mutation ids must be non-empty and unique across manifests")
    return defects


def load_causal_cache() -> dict:
    """Detectors that killed these same mutants before, if the identity still matches exactly.

    The cache is a speed hint and nothing else: it can only move a detector to the front of a
    permutation. It is honoured only when the source and toolchain digests are identical, so
    a changed tree never reuses an observation made about a different one.
    """
    configured = os.environ.get("FACTORY_MUTATION_CACHE", "").strip()
    if not configured:
        return {}
    path = Path(configured)
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if record.get("source_ref") != source_ref() or record.get("environment_ref") != environment_ref():
        return {}
    causal = record.get("causal")
    return causal if isinstance(causal, dict) else {}


def save_causal_cache(results: list[MutationResult]) -> None:
    configured = os.environ.get("FACTORY_MUTATION_CACHE", "").strip()
    if not configured:
        return
    causal = {result.mutant_id: result.detector for result in results
              if result.state == CAUGHT and result.detector}
    write_atomic(Path(configured), {"schema": "dark-factory/mutation-causal", "schema_version": "1.0",
                                    "source_ref": source_ref(), "environment_ref": environment_ref(),
                                    "causal": causal})


def evaluate(defect: dict, order: tuple[str, ...], baseline: dict | None = None,
             causal: dict | None = None, target: Path | None = None) -> MutationResult:
    """One defect, in its own copy of the trust root, which is removed however this ends.

    The verdict is a `MutationResult`, not a returncode: a detector that timed out or could
    not be collected reports that, and the mutant is neither caught nor survived. Survival
    requires every detector the baseline found usable to have run and passed.
    """
    started = time.monotonic_ns()
    baseline = baseline or {"detectors": {rel: {"status": PASSED} for rel in TEST_FILES}}
    reference = baseline.get("baseline_ref", "")
    statuses = baseline.get("detectors", {})
    usable = [rel for rel in detector_order(defect, order, causal)
              if statuses.get(rel, {}).get("status") == PASSED]
    # The detectors this defect's catalogue entry names are the ones that can actually see it;
    # the rest of the suite is the fallback that makes a survivor believable. If every named
    # detector was red on the baseline, nothing in the suite was looking for this property, and
    # the other 150 files passing says only that they were not looking either. Reporting that
    # as a surviving trust-root bypass would be the exact mislabelling this rework exists to
    # remove, so it is an absence of observation instead.
    named = associated_detectors(defect)
    blinded = [rel for rel in named if statuses.get(rel, {}).get("status") != PASSED]
    # Whether the suite that a survivor is measured against was complete at all. Not every
    # defect's `why` names a detector -- 94 of the 957 references cover a subset of the
    # catalogue -- so the named-detector rule alone leaves a gap, and the local 2026-09-18 run
    # walked straight into it: `worker-scheduler-persists-checkout-token` ran all 151 usable
    # detectors, none went red, and it was reported as a surviving trust-root bypass. Its
    # actual detector is tests/factory/test_factory_worker_authority.py, which was red on that
    # host's baseline for environment reasons; injected against that detector alone the mutant
    # is caught by an assertion. A survivor claims the WHOLE suite looked and none of it saw
    # anything, so it needs the whole suite.
    unusable_suite = sorted(rel for rel, row in statuses.items() if row.get("status") != PASSED)
    state, detector, injected_digest = SURVIVED, "", ""
    diagnostic: dict = {"mutant_id": defect["id"], "file": defect.get("file", ""),
                        "why": defect.get("why", ""), "detectors": [],
                        "named_detectors": list(named), "unusable_named_detectors": blinded}
    with tempfile.TemporaryDirectory(prefix=f"dark-factory-meta-{defect['id']}-") as tmp:
        root = build_copy(Path(tmp))
        if not inject(root, defect):
            state, diagnostic["reason"] = INFRA_ERROR, ANCHOR_REASON
        else:
            injected_digest = hashlib.sha256((root / defect["file"]).read_bytes()).hexdigest()
            env = suite_env(root)
        if state != INFRA_ERROR and named and len(blinded) == len(named):
            state, diagnostic["reason"] = INFRA_ERROR, (
                "every detector this defect names was red on the baseline: " + ",".join(blinded))
        elif state != INFRA_ERROR:
            for rel in usable:
                if remaining(FAMILY_DEADLINE) <= 1:
                    state, diagnostic["reason"] = TIMEOUT, "family deadline reached"
                    break
                status, returncode, output, elapsed_ns = run_detector(
                    [sys.executable, rel], cwd=root, env=env, seconds=detector_seconds())
                diagnostic["detectors"].append(
                    {"detector": rel, "status": status, "exit_code": returncode,
                     "seconds": round(elapsed_ns / 1_000_000_000, 3),
                     "tail": output[-800:] if status != PASSED else ""})
                if status == FAILED:
                    state, detector = CAUGHT, rel
                    break
                if status == TIMEOUT:
                    state, detector, diagnostic["reason"] = TIMEOUT, rel, "detector timed out"
                    break
                if status == INFRA_ERROR:
                    state, detector, diagnostic["reason"] = INFRA_ERROR, rel, "detector could not run"
                    break
    if state == SURVIVED and unusable_suite:
        state, diagnostic["reason"] = INFRA_ERROR, (
            f"no detector went red, but {len(unusable_suite)} of {len(statuses)} detectors were "
            "unusable on this baseline, so the suite that would have to have looked did not: "
            + ",".join(unusable_suite[:5]) + ("..." if len(unusable_suite) > 5 else ""))
    elapsed_ns = time.monotonic_ns() - started
    diagnostic.update({"state": state, "elapsed_ns": elapsed_ns, "baseline_ref": reference,
                       "unusable_suite": unusable_suite})
    reference_path = ""
    if target is not None:
        path = target / f"{defect['id']}.json"
        write_atomic(path, diagnostic)
        reference_path = path.name
    return MutationResult(defect["id"], injected_digest, state, detector, reference,
                          elapsed_ns, reference_path)


def evaluate_all(defects: list[dict], order: tuple[str, ...], workers: int,
                 baseline: dict | None = None, causal: dict | None = None,
                 target: Path | None = None) -> list[MutationResult]:
    """Every defect in the manifest, evaluated, whatever the clock says.

    A worker that raises no longer unwinds the pool and discards every other verdict: the
    exception becomes that mutant's `infra_error`, which cannot be read as a kill or as an
    escape and keeps the run from reporting green. `map` keeps manifest order in the report.
    """
    def one(defect: dict) -> MutationResult:
        try:
            return evaluate(defect, order, baseline, causal, target)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            record = {"mutant_id": defect["id"], "state": INFRA_ERROR, "reason": str(exc)[:400]}
            if target is not None:
                write_atomic(target / f"{defect['id']}.json", record)
            return MutationResult(defect["id"], "", INFRA_ERROR, "",
                                  (baseline or {}).get("baseline_ref", ""), 0, "")

    if workers <= 1:
        return [one(defect) for defect in defects]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, defects))


def diagnostic_reasons(target: Path, results: list[MutationResult]) -> dict[str, str]:
    """Why each unobserved mutant was unobserved, read back from its own result file.

    The per-mutant file is the retained evidence; this only lifts its one-line reason into the
    summary so a reader of the tail can tell an anchor that stopped matching from a detector
    that could not run.
    """
    reasons: dict[str, str] = {}
    for result in results:
        if result.state in (CAUGHT, SURVIVED, NOT_RUN):
            continue
        path = target / f"{result.mutant_id}.json"
        if not path.is_file():
            continue
        try:
            reason = json.loads(path.read_text(encoding="utf-8")).get("reason")
        except (OSError, ValueError):
            continue
        if isinstance(reason, str):
            reasons[result.mutant_id] = reason
    return reasons


def shard_selection(defects: list[dict]) -> tuple[list[dict], int, int]:
    """This process's share of the catalogue, and the split it belongs to.

    Sharding exists so measured complete work can fit inside the existing per-shard bound. It
    is a split of one catalogue against one baseline -- never a smaller catalogue -- so the
    manifest still lists every expected mutant and the ones this shard did not own are
    `not_run` until an aggregate puts the shards back together.
    """
    count = os.environ.get("FACTORY_MUTATION_SHARDS", "").strip()
    index = os.environ.get("FACTORY_MUTATION_SHARD", "").strip()
    if not (count.isdigit() and index.isdigit()):
        return defects, 0, 1
    shards, position = max(1, int(count)), int(index)
    if not 0 <= position < shards:
        raise RuntimeError(f"FACTORY_MUTATION_SHARD={position} is outside 0..{shards - 1}")
    members = set(shard_members([str(defect["id"]) for defect in defects], shards, position))
    return [defect for defect in defects if str(defect["id"]) in members], position, shards


def main() -> int:
    started = time.monotonic()
    try:
        defects = load_defects()
        selected, shard_index, shard_count = shard_selection(defects)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FACTORY_MUTATIONS_REFUSED {exc}", flush=True)
        return 1
    if not immunity_is_green():
        return 1

    target = results_dir()
    with tempfile.TemporaryDirectory(prefix="dark-factory-meta-baseline-") as tmp:
        baseline = run_baseline(build_copy(Path(tmp)))
    unusable = unusable_detectors(baseline)
    write_atomic(target / "baseline.json", baseline)
    if unusable:
        print(f"FACTORY_MUTATION_BASELINE_RED={','.join(unusable)}", flush=True)
    if len(unusable) == len(TEST_FILES):
        print("FACTORY_MUTATIONS_REFUSED focused baseline is red", flush=True)
        return 1
    order = cheapest_first(baseline_durations(baseline))
    print(f"FACTORY_MUTATION_BASELINE_OK ref={baseline['baseline_ref'][:16]} "
          f"usable={len(TEST_FILES) - len(unusable)}/{len(TEST_FILES)}", flush=True)

    workers = worker_count()
    print(f"FACTORY_MUTATION_START defects={len(selected)}/{len(defects)} workers={workers} "
          f"files={len(TEST_FILES)} shard={shard_index + 1}/{shard_count}", flush=True)
    causal = load_causal_cache()
    measured = evaluate_all(selected, order, workers, baseline, causal, target)
    save_causal_cache(measured)

    owned = {result.mutant_id: result for result in measured}
    results = [owned.get(str(defect["id"]),
                         MutationResult(str(defect["id"]), "", NOT_RUN, "",
                                        baseline["baseline_ref"], 0, ""))
               for defect in defects]
    manifest = {"schema": "dark-factory/mutation-manifest", "schema_version": "1.0",
                "baseline_ref": baseline["baseline_ref"], "shard_index": shard_index,
                "shard_count": shard_count, "unusable_detectors": unusable,
                "expected_ids": [str(defect["id"]) for defect in defects],
                "results": [result.as_record() for result in results]}
    write_atomic(target / "manifest.json", manifest)

    labels = {CAUGHT: "CAUGHT      ", SURVIVED: "SURVIVED    ", INFRA_ERROR: "INFRA_ERROR ",
              TIMEOUT: "TIMEOUT     ", NOT_RUN: "NOT_RUN     "}
    reasons = diagnostic_reasons(target, results)
    for result in results:
        if result.state == NOT_RUN and shard_count > 1:
            continue
        detail = result.detector or reasons.get(result.mutant_id) or (
            "no detector went red" if result.state == SURVIVED else "")
        print(f"  {labels[result.state]}  {result.mutant_id:<48} {detail} "
              f"seconds={round(result.elapsed_ns / 1_000_000_000, 1)}", flush=True)

    total = len(defects)
    counts = {state: sum(1 for result in results if result.state == state) for state in labels}
    not_injected = sum(1 for result in results
                       if reasons.get(result.mutant_id) == ANCHOR_REASON)
    seconds = round(time.monotonic() - started, 1)
    print(f"FACTORY_MUTATIONS_TOTAL={total}", flush=True)
    print(f"FACTORY_MUTATIONS_CAUGHT={counts[CAUGHT]}", flush=True)
    print(f"FACTORY_MUTATIONS_NOT_INJECTED={not_injected}", flush=True)
    print(f"FACTORY_MUTATIONS_SURVIVED={counts[SURVIVED]}", flush=True)
    print(f"FACTORY_MUTATIONS_INFRA_ERROR={counts[INFRA_ERROR]}", flush=True)
    print(f"FACTORY_MUTATIONS_TIMEOUT={counts[TIMEOUT]}", flush=True)
    print(f"FACTORY_MUTATIONS_NOT_RUN={counts[NOT_RUN]}", flush=True)
    print(f"FACTORY_MUTATIONS_RESULTS={target}", flush=True)
    print(f"FACTORY_MUTATIONS_SECONDS={seconds}", flush=True)
    warning = drift_warning(seconds, BUDGET, scope="factory-family",
                            marker="FACTORY_MUTATIONS_BUDGET_WARNING")
    if warning:
        print(warning, flush=True)

    # WHICH defects failed, at the tail, next to the marker that says the family failed. The
    # per-defect lines above are 950 of them and every consumer of this output keeps only the
    # end of it: the kernel stores the last characters of a refused tool's output
    # (`validation-refusal.json`), and the workflow log shows the exception, not the stream. On
    # 2026-09-07 that left "FACTORY_MUTATIONS_FAILED" with three anonymous survivors and a
    # count of twenty-three uninjected, and diagnosing it needed a local re-run of the whole
    # catalogue. A failure names its own members here so the refusal artifact carries them.
    for state, marker in ((SURVIVED, "FACTORY_MUTATIONS_ESCAPED"),
                          (INFRA_ERROR, "FACTORY_MUTATIONS_UNINJECTED"),
                          (TIMEOUT, "FACTORY_MUTATIONS_TIMED_OUT"),
                          (NOT_RUN, "FACTORY_MUTATIONS_MISSING")):
        ids = [result.mutant_id for result in results
               if result.state == state and not (state == NOT_RUN and shard_count > 1)]
        if ids:
            print(f"{marker}={','.join(ids)}", flush=True)
    unobserved = counts[INFRA_ERROR] + counts[TIMEOUT] + (
        0 if shard_count > 1 else counts[NOT_RUN])
    # Two different bad outcomes, always both reported when both happened. A survivor is a
    # claim about the trust root; an unobserved mutant is a claim about this run. Naming only
    # one of them would hide the other, and in shard mode the naming happens here too rather
    # than under a marker that says the shard was fine.
    if unobserved:
        print("FACTORY_MUTATIONS_INCOMPLETE - the catalogue was not fully observed", flush=True)
    if counts[SURVIVED]:
        print("FACTORY_MUTATIONS_FAILED - factory trust-root bypass survived", flush=True)
    if shard_count > 1:
        if not counts[SURVIVED] and not unobserved:
            print(f"FACTORY_MUTATIONS_SHARD_OK shard={shard_index + 1}/{shard_count} "
                  f"measured={len(selected)} results={target}", flush=True)
            return 0
        return 1
    if counts[CAUGHT] == total and not unusable:
        print(f"FACTORY_MUTATIONS_OK defects={total} seconds={seconds} "
              f"budget={FAMILY_BUDGET_SECONDS}", flush=True)
        return 0
    if not unobserved and not counts[SURVIVED] and unusable:
        print("FACTORY_MUTATIONS_INCOMPLETE - detectors were unusable on this baseline",
              flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
