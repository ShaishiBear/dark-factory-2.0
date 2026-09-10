#!/usr/bin/env python3
"""The static rung, across BOTH halves of DynaChat.

`ci.py` runs one command per rung, and this repo has two stacks. Rather than teach the
ladder about that - the ladder is the same in every factory and should stay that way -
the split lives here, behind one command that `harness.config.json` can name.

The commands are lifted verbatim from `.archon/workflows/dark-factory-validate-pr.yaml`
(`static-checks-backend-p1` / `static-checks-frontend-p1`) so there is ONE definition of
what "static passes" means for this repo. If you change one, change the other, or the
gate and the workflow start disagreeing about a green build.

    python harness/static.py

Exits non-zero if any check fails. Prints which one, because "static failed" across two
languages and five tools is not a diagnosis.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "app" / "backend"
FRONTEND = ROOT / "app" / "frontend"

sys.path.insert(0, str(HERE))
import budget  # noqa: E402

# ONE DEADLINE FOR THE WHOLE RUNG, not one per check. Each of the five checks carried
# `timeout=600`, which is a 3000 s rung wearing a 600 s label -- inside a rung `harness/ci.py`
# bounded at 300 s, so the inner number was five times the outer one. Now both come from the
# `static-rung` scope in harness/budgets.json, and each check is given what is LEFT of it, so
# the rung cannot outlive the wrapper that contains it (D-075).
BUDGET = budget.load()
RUNG_SECONDS = budget.budget_seconds(BUDGET, scope="static-rung")

# (label, cwd, argv). Kept as argv rather than shell strings: `ci.py` resolves argv[0]
# through shutil.which for the Windows .cmd-shim problem, and a shell string would
# reintroduce the quoting bugs that ate 2026-04-14.
CHECKS = [
    # First because it costs milliseconds and because what it catches is invisible everywhere
    # else: a mutation anchor that no longer matches its source reports `not_injected` only in
    # the full harness, fifty minutes into a validation, and the defect it names has had no
    # detector since the refactor that moved it (D-076).
    ("mutation-anchors", ROOT, [sys.executable, str(HERE / "mutation_anchors.py")]),
    # Second, and for the same reason as the first: milliseconds, and invisible everywhere else.
    # The four checks below lint app/backend and app/frontend. NOTHING linted the trust root --
    # the kernel that judges every product PR received less static analysis than the product it
    # judges. `merge_authorized` carried `create(` where the import is `create_detached`; it
    # passed its whole suite, passed static, passed unit, and raised NameError on its first real
    # invocation in the merge step of run 34399514537, eighty-nine minutes into a lap that had
    # gone green on every rung. `ruff --select F` refuses it in milliseconds, on its own pull
    # request. Verified in both directions before this check was added: clean on the tree as it
    # stands, one error with that name restored (DFE-026).
    #
    # `--select F` is pyflakes only, not the backend's full rule set: that reports 156 findings
    # here and would make this a formatting argument instead of a proof one. `--isolated` so the
    # backend's pyproject config, with its own selections and excludes, cannot narrow what the
    # trust root is held to. Run from BACKEND only to borrow its pinned ruff; the paths are
    # absolute and nothing under app/ is examined here.
    #
    # THE THREE IGNORES ARE A DEBT, NOT A POLICY. `--select F` alone reports 30 findings on the
    # trust root today: 27 F401 unused imports, 2 F841 unused locals, 1 F541 f-string with no
    # placeholder. Every one is hygiene; not one is a defect. Fixing thirty files here would
    # make this pull request about something other than the check it adds, and this check is
    # about Lane A being proved by less than Lane B (DFE-026), so it is the only variable in its
    # own change. The correctness half of F -- undefined names, redefinitions, comparison and
    # format errors -- is enforced from today with nothing excused. Delete an ignore and fix
    # what it was hiding; the list only shrinks.
    ("ruff-trust-root", BACKEND, [
        "uv", "run", "ruff", "check", "--isolated", "--no-cache",
        "--select", "F", "--ignore", "F401,F841,F541",
        str(ROOT / "factory_kernel"), str(ROOT / "harness"),
        str(ROOT / "scripts"), str(ROOT / "tests" / "factory"),
    ]),
    ("ruff-lint",   BACKEND,  ["uv", "run", "ruff", "check", "."]),
    ("ruff-format", BACKEND,  ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy",        BACKEND,  ["uv", "run", "mypy", "."]),
    ("tsc",         FRONTEND, ["bun", "run", "tsc", "--noEmit"]),
    ("biome",       FRONTEND, ["bun", "x", "biome", "check", "src"]),
]


def main() -> int:
    failed: list[str] = []
    ran = 0
    # The reserve is what this runner keeps back so that IT names the check that ran long,
    # before ci.py's own timeout kills the process with nothing to say.
    until = budget.deadline(BUDGET, "static-rung")
    print(f"STATIC_BUDGET seconds={RUNG_SECONDS}", flush=True)

    for label, cwd, argv in CHECKS:
        if not cwd.exists():
            # LOUD. A missing half is not a passing half - that is the whole argument of
            # this harness, applied to itself.
            print(f"STATIC_MISSING {label}: {cwd} does not exist", flush=True)
            failed.append(label)
            continue
        left = budget.remaining(until)
        try:
            p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=left)
        except FileNotFoundError:
            print(f"STATIC_MISSING {label}: {argv[0]} is not on PATH", flush=True)
            failed.append(label)
            continue
        except subprocess.TimeoutExpired:
            print(f"STATIC_TIMEOUT {label} after {left:.0f}s of the {RUNG_SECONDS}s "
                  f"static-rung budget", flush=True)
            failed.append(label)
            continue

        ran += 1
        if p.returncode != 0:
            print(f"--- {label} ---", flush=True)
            print(((p.stdout or "") + (p.stderr or "")).strip()[-2000:], flush=True)
            failed.append(label)
        else:
            print(f"  ok  {label}", flush=True)

    if failed:
        print(f"STATIC_FAILED checks={','.join(failed)}", flush=True)
        return 1

    # A COUNT, not just a name. Five tools silently becoming three is the shape of every
    # bug this repo has filed against its own gate.
    print(f"STATIC_OK checks={ran}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
