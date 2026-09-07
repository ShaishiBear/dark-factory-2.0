#!/usr/bin/env python3
"""Mutation-test copied factory trust-root code without touching the live worktree.

Every defect gets its own copy of the trust root, so the defects are independent by
construction and are evaluated CONCURRENTLY (D-073). Each copy's focused suite stops at its
first red file: the property this runner asserts is "the focused suite goes red", and a suite
that has already produced one red is red, so stopping there reaches the identical verdict.
The order those files run in is the baseline's own measurement of what each one costs,
cheapest first -- the baseline runs all of them anyway, and every file still runs whenever
nothing goes red, so the ordering only decides which red is found first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from budget import budget_seconds, deadline, drift_warning, load, remaining  # noqa: E402

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
    ".factory/architecture.json",
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
    "tests/factory/test_factory_github_e2e_bootstrap.py",
    "tests/factory/test_factory_merge_verify.py",
    "tests/factory/test_factory_post_merge.py",
    "tests/factory/test_factory_post_merge_runtime.py",
    "tests/factory/test_factory_benchmark.py",
    "tests/factory/test_factory_immunity.py",
    "tests/factory/test_factory_provenance.py",
    "tests/factory/test_factory_note_identity.py",
    "tests/factory/test_factory_evidence_closure.py",
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


def run_tests(
    root: Path, order: tuple[str, ...] | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, float]]:
    """Run the focused suite in `root`, stopping at the first red file.

    Returns the verdict and what each file that ran cost. The verdict is exactly the one the
    exhaustive loop reached -- red iff some file is red -- because a suite is red as soon as
    one of its files is, and a green suite still runs every file.
    """
    outputs: list[str] = []
    durations: dict[str, float] = {}
    env = dict(os.environ)
    python_paths = [str(root), str(root / "scripts")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    for rel in order or TEST_FILES:
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, rel], cwd=root, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=min(FILE_SECONDS, remaining(FAMILY_DEADLINE)),
        )
        durations[rel] = round(time.monotonic() - started, 3)
        outputs.append((proc.stdout or "") + (proc.stderr or ""))
        if proc.returncode != 0:
            return subprocess.CompletedProcess([], 1, "\n".join(outputs), ""), durations
    return subprocess.CompletedProcess([], 0, "\n".join(outputs), ""), durations


def cheapest_first(durations: dict[str, float]) -> tuple[str, ...]:
    """Every test file, ordered by what the baseline measured it to cost.

    The set is always the whole suite, so nothing is dropped and an escape is still an escape;
    an unmeasured file sorts last rather than being assumed cheap.
    """
    return tuple(sorted(TEST_FILES, key=lambda rel: (durations.get(rel, float("inf")), rel)))


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


def evaluate(defect: dict, order: tuple[str, ...]) -> dict:
    """One defect, in its own copy of the trust root, which is removed however this ends."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"dark-factory-meta-{defect['id']}-") as tmp:
        root = build_copy(Path(tmp))
        if not inject(root, defect):
            outcome, detail = "not_injected", "anchor missing/non-unique"
        elif run_tests(root, order)[0].returncode != 0:
            outcome, detail = "caught", "focused suite went red"
        else:
            outcome, detail = "escaped", f"<-- {defect['why']}"
    return {"id": defect["id"], "outcome": outcome, "detail": detail,
            "seconds": round(time.monotonic() - started, 1)}


def evaluate_all(defects: list[dict], order: tuple[str, ...], workers: int) -> list[dict]:
    """Every defect in the manifest, evaluated, whatever the clock says.

    `map` keeps manifest order in the report and re-raises a worker's exception instead of
    dropping that defect's result: a defect that could not be evaluated must stop the run, not
    quietly leave the catalogue.
    """
    if workers <= 1:
        return [evaluate(defect, order) for defect in defects]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda defect: evaluate(defect, order), defects))


def main() -> int:
    started = time.monotonic()
    try:
        defects = load_defects()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FACTORY_MUTATIONS_REFUSED {exc}", flush=True)
        return 1
    if not immunity_is_green():
        return 1

    with tempfile.TemporaryDirectory(prefix="dark-factory-meta-baseline-") as tmp:
        baseline, durations = run_tests(build_copy(Path(tmp)))
        if baseline.returncode != 0:
            print("FACTORY_MUTATIONS_REFUSED focused baseline is red", flush=True)
            print((baseline.stdout or "")[-2500:], flush=True)
            return 1
    order = cheapest_first(durations)
    print("FACTORY_MUTATION_BASELINE_OK", flush=True)

    workers = worker_count()
    print(f"FACTORY_MUTATION_START defects={len(defects)} workers={workers} "
          f"files={len(TEST_FILES)}", flush=True)
    results = evaluate_all(defects, order, workers)
    labels = {"caught": "CAUGHT      ", "escaped": "ESCAPED     ",
              "not_injected": "NOT_INJECTED"}
    for result in results:
        print(f"  {labels[result['outcome']]}  {result['id']:<48} "
              f"{result['detail']} seconds={result['seconds']}", flush=True)

    total = len(defects)
    caught = sum(1 for result in results if result["outcome"] == "caught")
    not_injected = sum(1 for result in results if result["outcome"] == "not_injected")
    seconds = round(time.monotonic() - started, 1)
    print(f"FACTORY_MUTATIONS_TOTAL={total}", flush=True)
    print(f"FACTORY_MUTATIONS_CAUGHT={caught}", flush=True)
    print(f"FACTORY_MUTATIONS_NOT_INJECTED={not_injected}", flush=True)
    print(f"FACTORY_MUTATIONS_SECONDS={seconds}", flush=True)
    warning = drift_warning(seconds, BUDGET, scope="factory-family",
                            marker="FACTORY_MUTATIONS_BUDGET_WARNING")
    if warning:
        print(warning, flush=True)
    if caught == total and not_injected == 0:
        print(f"FACTORY_MUTATIONS_OK defects={total} seconds={seconds} "
              f"budget={FAMILY_BUDGET_SECONDS}", flush=True)
        return 0
    # WHICH defects failed, at the tail, next to the marker that says the family failed. The
    # per-defect lines above are 409 of them and every consumer of this output keeps only the
    # end of it: the kernel stores the last characters of a refused tool's output
    # (`validation-refusal.json`), and the workflow log shows the exception, not the stream. On
    # 2026-09-07 that left "FACTORY_MUTATIONS_FAILED" with three anonymous survivors and a
    # count of twenty-three uninjected, and diagnosing it needed a local re-run of the whole
    # catalogue. A failure names its own members here so the refusal artifact carries them.
    for outcome, marker in (("escaped", "FACTORY_MUTATIONS_ESCAPED"),
                            ("not_injected", "FACTORY_MUTATIONS_UNINJECTED")):
        ids = [result["id"] for result in results if result["outcome"] == outcome]
        if ids:
            print(f"{marker}={','.join(ids)}", flush=True)
    print("FACTORY_MUTATIONS_FAILED - factory trust-root bypass survived", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
