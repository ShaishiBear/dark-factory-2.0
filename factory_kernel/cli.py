"""Small command surface for CI/control-plane integrations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import RunManifest
from .state import FactoryState, Outcome, Stage, retry, transition


def manifest_validate(path: str) -> int:
    manifest = RunManifest.load(path)
    print(f"MANIFEST_OK claims={len(manifest.claims)} sha256={manifest.sha256()}")
    return 0


def state_next(issue: int, stage: str, outcome: str, attempt: int) -> int:
    current = FactoryState(issue=issue, stage=Stage(stage), attempt=attempt)
    if outcome == "retry":
        nxt = retry(current)
    else:
        nxt = transition(current, Outcome(outcome))
    print(
        json.dumps(
            {"issue": nxt.issue, "stage": nxt.stage.value, "attempt": nxt.attempt},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m factory_kernel")
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest-validate")
    manifest.add_argument("path", type=Path)

    state = sub.add_parser("state-next")
    state.add_argument("--issue", type=int, required=True)
    state.add_argument("--stage", choices=[stage.value for stage in Stage], required=True)
    state.add_argument(
        "--outcome",
        choices=[outcome.value for outcome in Outcome] + ["retry"],
        required=True,
    )
    state.add_argument("--attempt", type=int, default=1)

    args = parser.parse_args()
    if args.command == "manifest-validate":
        return manifest_validate(str(args.path))
    if args.command == "state-next":
        return state_next(args.issue, args.stage, args.outcome, args.attempt)
    raise AssertionError(f"unhandled command: {args.command}")
