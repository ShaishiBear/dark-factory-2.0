#!/usr/bin/env python3
"""Mutation-test the factory's own merge guards without editing the live worktree."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFECTS = HERE / "defects.json"
COPY_FILES = (
    "scripts/factory_security.py",
    "scripts/factory_evidence.py",
    "scripts/factory_protocol.py",
    "tests/factory/test_factory_security.py",
    "tests/factory/test_factory_security_evidence.py",
    "tests/factory/test_factory_evidence.py",
)
TEST_FILES = (
    "tests/factory/test_factory_security.py",
    "tests/factory/test_factory_security_evidence.py",
    "tests/factory/test_factory_evidence.py",
)


def build_copy(parent: Path) -> Path:
    target = parent / "root"
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
    for rel in TEST_FILES:
        proc = subprocess.run(
            [sys.executable, rel], cwd=root, capture_output=True, text=True,
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


def main() -> int:
    if not DEFECTS.is_file():
        print("FACTORY_MUTATIONS_REFUSED defects.json is missing", flush=True)
        return 1
    defects = json.loads(DEFECTS.read_text(encoding="utf-8")).get("defects")
    if not isinstance(defects, list) or not defects:
        print("FACTORY_MUTATIONS_REFUSED no defects configured", flush=True)
        return 1

    with tempfile.TemporaryDirectory(prefix="dark-factory-meta-baseline-") as tmp:
        baseline_root = build_copy(Path(tmp))
        baseline = run_tests(baseline_root)
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
                print(f"  NOT_INJECTED  {defect['id']:<46} anchor missing/non-unique", flush=True)
                continue
            result = run_tests(root)
            if result.returncode != 0:
                caught += 1
                print(f"  CAUGHT        {defect['id']:<46} focused suite went red", flush=True)
            else:
                print(f"  ESCAPED       {defect['id']:<46} <-- {defect['why']}", flush=True)

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
