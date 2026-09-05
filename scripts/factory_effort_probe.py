"""Does the worker's model route honour `--effort`? A preflight measurement, never a gate.

Every worker runs at a stated effort (`worker_policy.ROLE_EFFORT`: workers `medium`, judges
`high`), rendered as `--effort <level>` on every request. The CLI documents the scale as
calibrated per model, and the factory's route is OpenRouter's Anthropic-compatible endpoint to a
non-Anthropic model, so whether the route honours the level at all is a question the policy
cannot answer for itself. The worker workflow's preflight answers it once per run: the worker
model twice on one fixed reasoning prompt, one turn each, at the lowest and the highest level
the policy uses, counting the thinking each stream showed (`providers.thinking_tokens`, the
same estimate the stage records carry). It prints

    FACTORY_PREFLIGHT_EFFORT_PROBE model=<slug> low_thinking=<N> high_thinking=<M>
        honoured=true|false low_level=<level> high_level=<level> low_events=<n>
        high_events=<n> [error=<what>]

on one line, and exits 0 whatever it found: `honoured=false` is data for the next tuning of the
levels (D-055), and a route that cannot run the model at all was refused by the route probe
before this one ran. `honoured` means the higher level thought more by a clear margin:
at least `MARGIN_RATIO` times the lower level's count and at least `MARGIN_TOKENS` more.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# The tree under test is the working directory (D-036): its `.factory/kernel.json` names the
# worker model when `--model` is not given. The code is loaded from beside this file.
ROOT = Path.cwd().resolve()
sys.path.insert(0, str(HERE.parent))

from factory_kernel.providers import parse_events, thinking_tokens  # noqa: E402
from factory_kernel.worker_policy import EFFORT_LEVELS, ROLE_EFFORT, effort_rank  # noqa: E402

LINE_PREFIX = "FACTORY_PREFLIGHT_EFFORT_PROBE"
KERNEL_JSON = ROOT / ".factory" / "kernel.json"
# A short task with a real plan in it, so a model that is allowed to think has something to
# think about, and a one-turn answer is still cheap.
PROMPT = "Plan, in numbered steps, how you would add a column to a Postgres table without downtime."
MARGIN_RATIO = 1.5
MARGIN_TOKENS = 100
PROBE_TIMEOUT_SECONDS = 180


def configured_model(policy: Path = KERNEL_JSON) -> str:
    """`provider.model` of the tree under test's kernel policy: the worker model."""
    try:
        raw = json.loads(policy.read_text(encoding="utf-8"))
        model = raw["provider"]["model"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read provider.model from {policy}: {exc}") from exc
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"provider.model in {policy} must be a non-empty string")
    return model.strip()


def policy_extremes() -> tuple[str, str]:
    """The lowest and the highest level any role runs at: the spread the policy relies on."""
    levels = sorted(set(ROLE_EFFORT.values()), key=effort_rank)
    return levels[0], levels[-1]


def probe_argv(binary: str, model: str, level: str) -> list[str]:
    """The tool-less request `ClaudeCliProvider.run` makes, one turn, one dollar, at `level`."""
    if level not in EFFORT_LEVELS:
        raise ValueError(f"effort level {level!r} is not one the CLI accepts")
    return [
        binary,
        "--bare",
        "-p",
        PROMPT,
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
        "--max-budget-usd",
        "1",
        "--effort",
        level,
    ]


@dataclass(frozen=True)
class Measurement:
    level: str
    events: int
    thinking: int
    returned: bool
    error: str = ""


def measure(level: str, stdout: str, *, returncode: int | None) -> Measurement:
    """What one probe process showed: its events, the thinking they carried, and whether it
    ended in a non-error `result` event."""
    events = parse_events(stdout)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    returned = returncode == 0 and result is not None and result.get("is_error") is False
    error = ""
    if not returned:
        detail = str(result.get("result") or result.get("subtype") or "") if result else ""
        error = (detail or f"rc={returncode}").replace(" ", "_")[:80]
    return Measurement(
        level=level,
        events=len(events),
        thinking=thinking_tokens(events),
        returned=returned,
        error=error,
    )


def honoured(low_thinking: int, high_thinking: int) -> bool:
    return (
        high_thinking >= low_thinking * MARGIN_RATIO
        and high_thinking - low_thinking >= MARGIN_TOKENS
    )


def probe_line(model: str, low: Measurement, high: Measurement) -> str:
    fields = [
        f"model={model}",
        f"low_thinking={low.thinking}",
        f"high_thinking={high.thinking}",
        f"honoured={'true' if low.returned and high.returned and honoured(low.thinking, high.thinking) else 'false'}",
        f"low_level={low.level}",
        f"high_level={high.level}",
        f"low_events={low.events}",
        f"high_events={high.events}",
    ]
    errors = [f"{m.level}:{m.error}" for m in (low, high) if m.error]
    if errors:
        fields.append("error=" + ",".join(errors))
    return LINE_PREFIX + " " + " ".join(fields)


Runner = Callable[..., Any]


def run_one(
    binary: str,
    model: str,
    level: str,
    *,
    runner: Runner = subprocess.run,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> Measurement:
    try:
        proc = runner(
            probe_argv(binary, model, level),
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
        found = measure(level, stdout, returncode=None)
        return Measurement(
            level, found.events, found.thinking, False, f"timeout_after_{timeout:g}s"
        )
    except OSError as exc:
        return Measurement(level, 0, 0, False, type(exc).__name__)
    return measure(level, proc.stdout or "", returncode=proc.returncode)


def run_probe(
    model: str,
    *,
    binary: str = "claude",
    low_level: str | None = None,
    high_level: str | None = None,
    runner: Runner = subprocess.run,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> str:
    default_low, default_high = policy_extremes()
    low_level = low_level or default_low
    high_level = high_level or default_high
    if effort_rank(low_level) >= effort_rank(high_level):
        raise ValueError(
            f"the probe needs two levels in order; got {low_level!r} and {high_level!r}"
        )
    low = run_one(binary, model, low_level, runner=runner, timeout=timeout)
    high = run_one(binary, model, high_level, runner=runner, timeout=timeout)
    return probe_line(model, low, high)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--model",
        default=None,
        help="the worker model slug; default: provider.model of ./.factory/kernel.json",
    )
    parser.add_argument("--binary", default="claude")
    parser.add_argument("--low", default=None, help="lower level; default: the policy's lowest")
    parser.add_argument("--high", default=None, help="higher level; default: the policy's highest")
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    print(
        run_probe(
            args.model or configured_model(),
            binary=args.binary,
            low_level=args.low,
            high_level=args.high,
            timeout=args.timeout,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
