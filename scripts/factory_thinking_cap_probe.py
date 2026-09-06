"""Does the worker's model route honour a thinking budget? A preflight measurement, never a gate.

`--effort` (D-055) is the CLI's calibrated level, and on the factory's route (OpenRouter's
Anthropic-compatible endpoint to a non-Anthropic model) it is not a bound: the effort probe
read honoured=false, true, true, false across the last four builds of issue #103, `medium`
out-thought `high` twice, and all six builds died or overran in `test_author` (the sixth:
2025 s, 15 turns, 121,065 thinking tokens, ~22,500 of them in the last turn). The one lever
not yet tried is the CLI's thinking budget, `MAX_THINKING_TOKENS` in its environment: above
zero the CLI sends `thinking: {type: "enabled", budget_tokens: N}` (raising N to 1024 if
lower), exactly zero sends `thinking: {type: "disabled"}`, unset leaves thinking adaptive.

The worker workflow's preflight measures it once per run: the worker model three times on the
effort probe's fixed reasoning prompt, one turn each, at the mutation roles' effort, with no
cap, with `MAX_THINKING_TOKENS=1024` (the smallest budget the CLI sends as given) and with
`MAX_THINKING_TOKENS=0` (thinking disabled), counting the thinking each stream showed
(`providers.thinking_tokens`, the estimate the stage records carry). It prints

    FACTORY_PREFLIGHT_THINKING_CAP_PROBE model=<slug> uncapped=N cap1024=M cap0=K
        honoured=true|false cap1024_honoured=true|false cap0_honoured=true|false
        effort=<level> uncapped_events=<n> cap1024_events=<n> cap0_events=<n> [error=<what>]

on one line and exits 0 whatever it found. A cap is honoured when its run returned, thought no
more than `CAP_SLACK` times the cap (so at most 1536 for the budget and nothing at all with
thinking disabled) and clearly less than the uncapped run: at least `MARGIN_RATIO` times less
and at least `MARGIN_TOKENS` fewer, the effort probe's own margins. `honoured` is both caps at
once. The line is data for `worker_policy.ROLE_THINKING_CAP`, every row of which is `None`
until it says the budget is honoured (D-059).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# The tree under test is the working directory (D-036): its `.factory/kernel.json` names the
# worker model when `--model` is not given. The code is loaded from beside this file.
ROOT = Path.cwd().resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from factory_effort_probe import (  # noqa: E402
    MARGIN_RATIO,
    MARGIN_TOKENS,
    PROBE_TIMEOUT_SECONDS,
    PROMPT,
    configured_model,
    measure,
    probe_argv,
)

from factory_kernel.worker_policy import (  # noqa: E402
    THINKING_CAP_DISABLED,
    THINKING_CAP_ENV,
    THINKING_CAP_MIN_BUDGET,
    WORKER_EFFORT,
)

LINE_PREFIX = "FACTORY_PREFLIGHT_THINKING_CAP_PROBE"
# The three ways the prompt is run, in order: no cap, the smallest budget the CLI sends as
# given, thinking disabled.
BUDGET_CAP = THINKING_CAP_MIN_BUDGET
DISABLE_CAP = THINKING_CAP_DISABLED
CAPS: tuple[int | None, ...] = (None, BUDGET_CAP, DISABLE_CAP)
# A capped run may show this much more thinking than its cap and still count as honoured:
# the count is the CLI's own estimate, not the bill.
CAP_SLACK = 1.5
# The level the roles that died run at (`worker_policy.ROLE_EFFORT["test_author"]`): the
# probe makes the request a builder makes, with the one variable added.
EFFORT = WORKER_EFFORT
# Three one-turn calls, each under the effort probe's own wall.
TIMEOUT_SECONDS = PROBE_TIMEOUT_SECONDS

Runner = Callable[..., Any]


def cap_field(cap: int | None) -> str:
    """`uncapped`, `cap1024`, `cap0`: the line's field for one way of running the prompt."""
    return "uncapped" if cap is None else f"cap{cap}"


def probe_environment(
    cap: int | None, source: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment for one run: the process's own with `MAX_THINKING_TOKENS` removed,
    then set to `cap` when there is one. The runner's own value of the variable, if any, is
    never what the uncapped run inherits."""
    original = os.environ if source is None else source
    env = {key: value for key, value in original.items() if key != THINKING_CAP_ENV}
    if cap is not None:
        env[THINKING_CAP_ENV] = str(cap)
    return env


def within_cap(thinking: int, cap: int) -> bool:
    """No more thinking than the cap allows, with `CAP_SLACK` for the estimate."""
    return thinking <= cap * CAP_SLACK


def clearly_below(thinking: int, uncapped: int) -> bool:
    """The uncapped run thought at least `MARGIN_RATIO` times as much and at least
    `MARGIN_TOKENS` more: the effort probe's own reading of a clear margin."""
    return uncapped >= thinking * MARGIN_RATIO and uncapped - thinking >= MARGIN_TOKENS


def cap_honoured(thinking: int, cap: int, uncapped: int) -> bool:
    """One cap is honoured when its run stayed inside the cap and clearly below the
    uncapped run; a route that ignores the variable fails the first, and a prompt that
    never needed thinking fails the second."""
    return within_cap(thinking, cap) and clearly_below(thinking, uncapped)


@dataclass(frozen=True)
class CapMeasurement:
    cap: int | None
    events: int
    thinking: int
    returned: bool
    error: str = ""

    @property
    def field(self) -> str:
        return cap_field(self.cap)


def honoured(uncapped: CapMeasurement, capped: CapMeasurement) -> bool:
    """Whether `capped`'s cap was honoured against `uncapped`; an errored call proves
    nothing, on either side."""
    if capped.cap is None:
        raise ValueError("the uncapped run has no cap to honour")
    if not (uncapped.returned and capped.returned):
        return False
    return cap_honoured(capped.thinking, capped.cap, uncapped.thinking)


def probe_line(model: str, runs: list[CapMeasurement]) -> str:
    by_cap = {run.cap: run for run in runs}
    if set(by_cap) != set(CAPS) or len(runs) != len(CAPS):
        raise ValueError(f"the probe needs one run per cap {CAPS!r}; got {runs!r}")
    uncapped = by_cap[None]
    capped = [by_cap[cap] for cap in CAPS if cap is not None]
    verdicts = {run.field: honoured(uncapped, run) for run in capped}
    fields = [f"model={model}"]
    fields.extend(f"{by_cap[cap].field}={by_cap[cap].thinking}" for cap in CAPS)
    fields.append(f"honoured={'true' if all(verdicts.values()) else 'false'}")
    fields.extend(f"{name}_honoured={'true' if ok else 'false'}" for name, ok in verdicts.items())
    fields.append(f"effort={EFFORT}")
    fields.extend(f"{by_cap[cap].field}_events={by_cap[cap].events}" for cap in CAPS)
    errors = [f"{by_cap[cap].field}:{by_cap[cap].error}" for cap in CAPS if by_cap[cap].error]
    if errors:
        fields.append("error=" + ",".join(errors))
    return LINE_PREFIX + " " + " ".join(fields)


def run_one(
    binary: str,
    model: str,
    cap: int | None,
    *,
    runner: Runner = subprocess.run,
    timeout: float = TIMEOUT_SECONDS,
    source: Mapping[str, str] | None = None,
) -> CapMeasurement:
    """One process: the effort probe's argv at the builders' level, in an environment that
    carries the cap or nothing."""
    argv = probe_argv(binary, model, EFFORT)
    env = probe_environment(cap, source)
    label = cap_field(cap)
    try:
        proc = runner(
            argv,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        found = measure(label, stdout, returncode=None)
        return CapMeasurement(
            cap, found.events, found.thinking, False, f"timeout_after_{timeout:g}s"
        )
    except OSError as exc:
        return CapMeasurement(cap, 0, 0, False, type(exc).__name__)
    found = measure(label, proc.stdout or "", returncode=proc.returncode)
    return CapMeasurement(cap, found.events, found.thinking, found.returned, found.error)


def run_probe(
    model: str,
    *,
    binary: str = "claude",
    runner: Runner = subprocess.run,
    timeout: float = TIMEOUT_SECONDS,
    source: Mapping[str, str] | None = None,
) -> str:
    runs = [
        run_one(binary, model, cap, runner=runner, timeout=timeout, source=source)
        for cap in CAPS
    ]
    return probe_line(model, runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--model",
        default=None,
        help="the worker model slug; default: provider.model of ./.factory/kernel.json",
    )
    parser.add_argument("--binary", default="claude")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    print(
        run_probe(args.model or configured_model(), binary=args.binary, timeout=args.timeout),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
