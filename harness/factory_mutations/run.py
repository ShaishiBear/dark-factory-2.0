#!/usr/bin/env python3
"""Mutation-test copied factory trust-root code without touching the live worktree."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
    "scripts/factory_protocol.py",
    "scripts/factory_artifacts.py",
    "scripts/factory_proof.py",
    "scripts/factory_architecture_guard.py",
    "harness/bootstrap_e2e.py",
    "harness/harness.config.json",
    "harness/serve.py",
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
    "harness/focused.py",
    "tests/factory/test_factory_security.py",
    "tests/factory/test_factory_contract_shape.py",
    "tests/factory/fixtures/contracts/run-33912650468-issue-49-keyed.json",
    "tests/factory/test_factory_workflow_hygiene.py",
    "tests/factory/test_factory_worker_throughput.py",
    "tests/factory/test_factory_methods.py",
    "tests/factory/test_factory_review_axes.py",
    "tests/factory/test_factory_repro_loop.py",
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
)
TEST_FILES = tuple(rel for rel in COPY_FILES if rel.startswith("tests/"))


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


def run_tests(root: Path) -> subprocess.CompletedProcess[str]:
    outputs: list[str] = []
    failed = False
    env = dict(os.environ)
    python_paths = [str(root), str(root / "scripts")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    for rel in TEST_FILES:
        proc = subprocess.run(
            [sys.executable, rel], cwd=root, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
        outputs.append((proc.stdout or "") + (proc.stderr or ""))
        failed = failed or proc.returncode != 0
    return subprocess.CompletedProcess([], 1 if failed else 0, "\n".join(outputs), "")


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
        encoding="utf-8", errors="replace", timeout=60,
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


def main() -> int:
    try:
        defects = load_defects()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FACTORY_MUTATIONS_REFUSED {exc}", flush=True)
        return 1
    if not immunity_is_green():
        return 1

    with tempfile.TemporaryDirectory(prefix="dark-factory-meta-baseline-") as tmp:
        baseline = run_tests(build_copy(Path(tmp)))
        if baseline.returncode != 0:
            print("FACTORY_MUTATIONS_REFUSED focused baseline is red", flush=True)
            print((baseline.stdout or "")[-2500:], flush=True)
            return 1
    print("FACTORY_MUTATION_BASELINE_OK", flush=True)

    caught = not_injected = 0
    print("FACTORY_MUTATION_START", flush=True)
    for defect in defects:
        with tempfile.TemporaryDirectory(prefix=f"dark-factory-meta-{defect['id']}-") as tmp:
            root = build_copy(Path(tmp))
            if not inject(root, defect):
                not_injected += 1
                print(f"  NOT_INJECTED  {defect['id']:<48} anchor missing/non-unique", flush=True)
                continue
            result = run_tests(root)
            if result.returncode != 0:
                caught += 1
                print(f"  CAUGHT        {defect['id']:<48} focused suite went red", flush=True)
            else:
                print(f"  ESCAPED       {defect['id']:<48} <-- {defect['why']}", flush=True)

    total = len(defects)
    print(f"FACTORY_MUTATIONS_TOTAL={total}", flush=True)
    print(f"FACTORY_MUTATIONS_CAUGHT={caught}", flush=True)
    print(f"FACTORY_MUTATIONS_NOT_INJECTED={not_injected}", flush=True)
    if caught == total and not_injected == 0:
        print("FACTORY_MUTATIONS_OK", flush=True)
        return 0
    print("FACTORY_MUTATIONS_FAILED - factory trust-root bypass survived", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
