#!/usr/bin/env python3
"""Mutation testing: break real source and require independent gates to notice.

Mutations run in place and are restored with git because copying app/backend/.venv and
app/frontend/node_modules is prohibitively expensive. The tree must therefore be clean.

Every application mutation is evaluated by ALL available non-E2E channels:
  - harness/ci.py --quick         builder-visible static/unit gate
  - .factory/holdout/run.py       independent core holdout
  - .factory/holdout/citations.py independent citation-composition probe
  - scripts/factory_security.py   deterministic security/dependency worktree guard

Pass --application-only to run the application family alone and leave the factory trust-root
family to its own owner. Genesis uses that mode, because it gives each family its own disposable
runner; ordinary canonical use keeps the integrated default.

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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "harness"))
from budget import budget_seconds, deadline, drift_warning, load, remaining  # noqa: E402

DEFECTS = Path(__file__).resolve().parent / "defects.json"
FACTORY_MUTATIONS = ROOT / "harness" / "factory_mutations" / "run.py"
BUDGET = load()
RUNG_BUDGET_SECONDS = budget_seconds(BUDGET, scope="mutation-rung")
FACTORY_BUDGET_SECONDS = budget_seconds(BUDGET, scope="factory-family")
CHANNEL_BUDGET_SECONDS = budget_seconds(BUDGET, scope="mutation-channel")
APPLICATION_BUDGET_SECONDS = budget_seconds(BUDGET, scope="application-family")
# The whole application family runs under ONE deadline, and each channel is given the smaller
# of its own budget and what is left of that. Ten iterations times four channels at a
# per-channel literal is a rung forty times its own label; the deadline is what makes the
# family's budget bound the family rather than one call inside it (D-075).
APPLICATION_DEADLINE = deadline(BUDGET, "application-family")
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
    """Return channel -> (went_red, detail), without short-circuiting.

    Every channel is evaluated for every defect, deliberately: which channel notices a defect
    is the measurement (`MUTATIONS_INDEPENDENT_CAUGHT`, `MUTATIONS_SECURITY_CAUGHT`, both
    ratcheted in `.factory/locks/floor.json`), so stopping at the first red would trade the
    evidence for the clock.
    """
    env = dict(os.environ, FACTORY_IN_MUTATION="1")
    results: dict[str, tuple[bool, str]] = {}
    for name, command in CHANNELS:
        # ONE channel, not the rung: the widest of the four is the quick gate, measured at
        # 179.0/203.4/207.4 s on three ubuntu runs (D-073). Bounded by the family's remaining
        # deadline as well, so the four channels of ten iterations cannot together outlive the
        # application family's own budget (D-075).
        seconds = min(CHANNEL_BUDGET_SECONDS, remaining(APPLICATION_DEADLINE))
        try:
            proc = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=seconds,
            )
        except subprocess.TimeoutExpired:
            # A TIMEOUT IS A FAILURE, NOT A CRASH. This call had no handler, so a channel that
            # ran long took the whole runner down with a traceback and no verdict for any
            # defect. Red, and named as a timeout so it is never mistaken for a real catch.
            results[name] = (True, f"{name}-timeout after {seconds:.0f}s")
            continue
        detail = _quick_rung(proc.stdout or "") if name == "quick" else name
        results[name] = (proc.returncode != 0, detail)
    return results


def timing_line(defect_id: str, seconds: float, outcome: str) -> str:
    """One defect's clock. The rung's total is the sum of these plus the baseline.

    A rung with a budget and no per-item timings can only be debugged by bisecting a timeout,
    which is what happened to run 34066724127: the whole rung reported `TIMEOUT after 900s`
    and nothing said which defect, or even which family, the clock had reached (D-073).
    """
    return f"MUTATION_TIMING id={defect_id} seconds={seconds:.1f} outcome={outcome}"


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
        text=True, encoding="utf-8", errors="replace", timeout=FACTORY_BUDGET_SECONDS,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip(), flush=True)
    if proc.returncode != 0:
        if proc.stderr.strip():
            print(proc.stderr.strip()[-2000:], flush=True)
        return False
    return True


def report_clock(total: int, started: float, ok: bool, failure: str) -> None:
    """Close the rung with its clock against its budget, whichever way it went.

    The budget is derived from `harness/budgets.json` (see harness/budget.py),
    and the warning fires while the run still passes, so the next person sees the rung running
    out of room instead of discovering it as a timeout.
    """
    seconds = time.monotonic() - started
    warning = drift_warning(
        seconds, BUDGET, scope="mutation-rung", marker="MUTATIONS_BUDGET_WARNING"
    )
    if warning:
        print(warning, flush=True)
    print(f"MUTATIONS_SECONDS={seconds:.1f}", flush=True)
    if ok:
        print(
            f"MUTATIONS_OK defects={total} seconds={seconds:.1f} budget={RUNG_BUDGET_SECONDS}",
            flush=True,
        )
    else:
        print(failure, flush=True)


def application_only_requested(argv: list[str]) -> bool:
    """Whether this invocation owns only the application family.

    Kept as its own function so the decision can be tested directly. Asserting it from the shape
    of main() only proves the guard is written, not that it is ever true.
    """
    return "--application-only" in argv


def main(argv: list[str] | None = None) -> int:
    # Genesis fans the two mutation families out to separate disposable runners, so the
    # application stage must not also run the factory suite: it would duplicate work the
    # factory-mutations stage already owns, and the nested run is what timed out under the
    # decomposition. Default behaviour is unchanged for ordinary canonical use.
    application_only = application_only_requested(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()

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

    baseline_started = time.monotonic()
    if not baseline_is_green():
        return 1
    print(f"MUTATION_BASELINE_SECONDS={time.monotonic() - baseline_started:.1f}", flush=True)

    defects = json.loads(DEFECTS.read_text(encoding="utf-8"))["defects"]
    total = caught = not_injected = 0
    quick_caught = independent_caught = citation_caught = security_caught = 0
    # Named, not just counted. Every consumer of this output keeps only its tail -- the kernel
    # stores the last characters of a refused tool's output in `validation-refusal.json` -- so
    # a failure that names its members only in the per-defect lines above arrives anonymous.
    escaped_ids: list[str] = []
    uninjected_ids: list[str] = []

    print("MUTATION_START", flush=True)
    for defect in defects:
        total += 1
        injected = False
        outcome = "not_injected"
        defect_started = time.monotonic()
        try:
            if not apply(defect):
                not_injected += 1
                uninjected_ids.append(defect["id"])
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
                outcome = "caught"
                details = ", ".join(results[name][1] for name in red_channels)
                print(
                    f"  CAUGHT        {defect['id']:<38} by {details}",
                    flush=True,
                )
            else:
                outcome = "escaped"
                escaped_ids.append(defect["id"])
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
            print(
                timing_line(defect["id"], time.monotonic() - defect_started, outcome),
                flush=True,
            )

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
    if not app_ok:
        for ids, marker in ((escaped_ids, "MUTATIONS_ESCAPED"),
                            (uninjected_ids, "MUTATIONS_UNINJECTED")):
            if ids:
                print(f"{marker}={','.join(ids)}", flush=True)
    if application_only:
        if app_ok:
            print("MUTATIONS_APPLICATION_ONLY_OK", flush=True)
            report_clock(total, started, True, "")
            return 0
        report_clock(
            total, started, False,
            "MUTATIONS_FAILED - an application defect can currently escape",
        )
        return 1

    factory_ok = run_factory_mutations()
    report_clock(
        total, started, app_ok and factory_ok,
        "MUTATIONS_FAILED - an application or factory defect can currently escape",
    )
    if app_ok and factory_ok:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
