#!/usr/bin/env python3
"""Mutation testing: break real source and require independent gates to notice.

Mutations run in place and are restored with git because copying app/backend/.venv and
app/frontend/node_modules is prohibitively expensive. The tree must therefore be clean.

Every application mutation is evaluated by ALL available non-E2E channels:
  - harness/ci.py --quick         builder-visible static/unit gate
  - .factory/holdout/run.py       independent core holdout
  - .factory/holdout/citations.py independent citation-composition probe
  - scripts/factory_security.py   deterministic security/dependency worktree guard

After those real-source probes, harness/factory_mutations/run.py separately mutation-tests
copied factory trust-root code. Factory mutations never edit the live worktree.

The clean baseline must pass all application channels before any defect is injected.
Individual defects may also declare `must_catch` channels; those probes only count when
the named guard itself turns red, even if some unrelated channel notices the mutation.

The full browser journey is still excluded because it requires the external validation
environment. That gap remains explicit rather than turning missing infrastructure into
a false mutation catch.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFECTS = Path(__file__).resolve().parent / "defects.json"
FACTORY_MUTATIONS = ROOT / "harness" / "factory_mutations" / "run.py"
CHANNELS = (
    ("quick", [sys.executable, "harness/ci.py", "--quick"]),
    ("holdout", [sys.executable, ".factory/holdout/run.py"]),
    ("citation", [sys.executable, ".factory/holdout/citations.py"]),
    ("security", [sys.executable, "scripts/factory_security.py", "--worktree"]),
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def tree_is_clean() -> bool:
    return not git("status", "--porcelain").stdout.strip()


def apply(defect: dict) -> bool:
    target = ROOT / defect["file"]
    if not target.exists():
        return False
    body = target.read_text(encoding="utf-8")
    if defect["find"] not in body:
        return False
    target.write_text(body.replace(defect["find"], defect["replace"], 1), encoding="utf-8")
    return True


def _quick_rung(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("GATE_FAILED:"):
            return line.split(":", 1)[1].strip()
    return "quick"


def run_channels() -> dict[str, tuple[bool, str]]:
    """Return channel -> (went_red, detail), without short-circuiting."""
    env = dict(os.environ, FACTORY_IN_MUTATION="1")
    results: dict[str, tuple[bool, str]] = {}
    for name, command in CHANNELS:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        detail = _quick_rung(proc.stdout or "") if name == "quick" else name
        results[name] = (proc.returncode != 0, detail)
    return results


def baseline_is_green() -> bool:
    print("MUTATION_BASELINE_START", flush=True)
    results = run_channels()
    failed = [name for name, (red, _detail) in results.items() if red]
    for name, (red, detail) in results.items():
        state = "RED" if red else "GREEN"
        print(f"  BASELINE      {name:<10} {state:<5} {detail}", flush=True)
    if failed:
        print(
            "MUTATIONS_REFUSED baseline is already red in "
            + ", ".join(failed)
            + "; a broken gate cannot be credited with catching mutations",
            flush=True,
        )
        return False
    print("MUTATION_BASELINE_OK", flush=True)
    return True


def run_factory_mutations() -> bool:
    if not FACTORY_MUTATIONS.is_file():
        print("FACTORY_MUTATIONS_ABSENT no harness/factory_mutations/run.py", flush=True)
        return False
    proc = subprocess.run(
        [sys.executable, str(FACTORY_MUTATIONS)], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip(), flush=True)
    if proc.returncode != 0:
        if proc.stderr.strip():
            print(proc.stderr.strip()[-2000:], flush=True)
        return False
    return True


def main() -> int:
    if not DEFECTS.exists():
        print("MUTATIONS_ABSENT no defects.json next to this script", flush=True)
        return 0

    if not tree_is_clean():
        print(
            "MUTATIONS_REFUSED the working tree is dirty. This runner mutates in place "
            "and restores with git, so it will not start on an unclean tree.",
            flush=True,
        )
        return 1

    if not baseline_is_green():
        return 1

    defects = json.loads(DEFECTS.read_text(encoding="utf-8"))["defects"]
    total = caught = not_injected = 0
    quick_caught = independent_caught = citation_caught = security_caught = 0

    print("MUTATION_START", flush=True)
    for defect in defects:
        total += 1
        injected = False
        try:
            if not apply(defect):
                not_injected += 1
                print(
                    f"  NOT_INJECTED  {defect['id']:<38} "
                    f"anchor not found in {defect['file']}",
                    flush=True,
                )
                continue

            injected = True
            results = run_channels()
            red_channels = [name for name, (red, _detail) in results.items() if red]
            required = defect.get("must_catch", [])
            missing_required = [name for name in required if name not in results or not results[name][0]]
            if results["quick"][0]:
                quick_caught += 1
            if results["holdout"][0] or results["citation"][0] or results["security"][0]:
                independent_caught += 1
            if results["citation"][0]:
                citation_caught += 1
            if results["security"][0]:
                security_caught += 1

            if red_channels and not missing_required:
                caught += 1
                details = ", ".join(results[name][1] for name in red_channels)
                print(
                    f"  CAUGHT        {defect['id']:<38} by {details}",
                    flush=True,
                )
            else:
                requirement = (
                    f" required channel(s) stayed green: {', '.join(missing_required)};"
                    if missing_required else ""
                )
                print(
                    f"  ESCAPED       {defect['id']:<38} <--{requirement} {defect['why']}",
                    flush=True,
                )
        finally:
            if injected:
                git("checkout", "--", defect["file"])

    if not tree_is_clean():
        print(
            "MUTATIONS_DIRTY the tree did not restore cleanly - inspect `git status`",
            flush=True,
        )
        return 1

    print(f"MUTATIONS_TOTAL={total}", flush=True)
    print(f"MUTATIONS_CAUGHT={caught}", flush=True)
    print(f"MUTATIONS_QUICK_CAUGHT={quick_caught}", flush=True)
    print(f"MUTATIONS_INDEPENDENT_CAUGHT={independent_caught}", flush=True)
    print(f"MUTATIONS_CITATION_CAUGHT={citation_caught}", flush=True)
    print(f"MUTATIONS_SECURITY_CAUGHT={security_caught}", flush=True)
    print(f"MUTATIONS_NOT_INJECTED={not_injected}", flush=True)

    app_ok = caught == total and not_injected == 0
    factory_ok = run_factory_mutations()
    if app_ok and factory_ok:
        print("MUTATIONS_OK", flush=True)
        return 0

    print(
        "MUTATIONS_FAILED - an application or factory defect can currently escape",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
