"""Command surface for the repo-owned Dark Factory control plane."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .manifest import RunManifest
from .runtime import FactoryStopped, KernelRuntime
from .state import FactoryState, Outcome, Stage, retry, transition

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".factory" / "kernel.json"


def manifest_validate(path: str) -> int:
    manifest = RunManifest.load(path)
    print(f"MANIFEST_OK claims={len(manifest.claims)} sha256={manifest.sha256()}")
    return 0


def state_next(issue: int, stage: str, outcome: str, attempt: int) -> int:
    current = FactoryState(issue=issue, stage=Stage(stage), attempt=attempt)
    nxt = retry(current) if outcome == "retry" else transition(current, Outcome(outcome))
    print(
        json.dumps(
            {"issue": nxt.issue, "stage": nxt.stage.value, "attempt": nxt.attempt},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def runtime(config_path: Path) -> KernelRuntime:
    return KernelRuntime(repo_root=ROOT, config=load_config(config_path))


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m factory_kernel")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest-validate")
    manifest.add_argument("path", type=Path)

    state = sub.add_parser("state-next")
    state.add_argument("--issue", type=int, required=True)
    state.add_argument("--stage", choices=[stage.value for stage in Stage], required=True)
    state.add_argument(
        "--outcome", choices=[outcome.value for outcome in Outcome] + ["retry"], required=True
    )
    state.add_argument("--attempt", type=int, default=1)

    sub.add_parser("config-check")
    sub.add_parser("stop-check")
    sub.add_parser("reap")

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--once", action="store_true", help="execute exactly one priority item")
    dispatch.add_argument("--no-merge", action="store_true")

    build = sub.add_parser("build")
    build.add_argument("--issue", type=int, required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--pr", type=int, required=True)
    validate.add_argument("--no-merge", action="store_true")

    args = parser.parse_args()
    if args.command == "manifest-validate":
        return manifest_validate(str(args.path))
    if args.command == "state-next":
        return state_next(args.issue, args.stage, args.outcome, args.attempt)
    if args.command == "config-check":
        cfg = load_config(args.config)
        print(
            f"KERNEL_CONFIG_OK repo={cfg.repository} provider={cfg.provider.provider_id} "
            f"prompts={len(cfg.prompts)}"
        )
        return 0

    rt = runtime(args.config)
    try:
        if args.command == "stop-check":
            rt.check_stop()
            print("KERNEL_STOP_CHECK_OK")
            return 0
        if args.command == "reap":
            rt.check_stop()
            rt.reap_stale_claims()
            print("KERNEL_REAP_OK")
            return 0
        if args.command == "dispatch":
            if not args.once:
                parser.error("dispatch currently requires --once; scheduling belongs to systemd")
            decision = rt.dispatch_once(merge=not args.no_merge)
            print(
                f"KERNEL_DISPATCH kind={decision.kind} "
                f"number={decision.number if decision.number is not None else '-'} "
                f"reason={decision.reason!r}"
            )
            return 0
        if args.command == "build":
            pr = rt.build_issue(args.issue)
            print(f"KERNEL_BUILD_OK issue={args.issue} pr={pr}")
            return 0
        if args.command == "validate":
            output = rt.validate_pr(args.pr, merge=not args.no_merge)
            print(f"KERNEL_VALIDATE_OK pr={args.pr} output={output}")
            return 0
    except FactoryStopped as exc:
        print(f"KERNEL_STOPPED {exc}")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
