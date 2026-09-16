"""Run HC-01 without connecting it to a production worker or merge authority."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import signal

from .benchmark import canonical, digest
from .feedback_lab import Limits, run_experiment, validate_tasks
from .feedback_sandbox import DockerSandbox, SandboxError
from .task_replay import source_identity, strict_json
from harness.feedback.provider import LiveWorker


class RecordedWorker:
    """A deterministic plumbing control, never evidence of model improvement."""
    def __call__(self, prompt, **caps):
        packet = json.loads(prompt.split("\n", 1)[1])
        # First draft returns null; a repair implements only the visible examples.
        # Unseen final checks expose this overfit even though public feedback turns green.
        if "public_feedback" not in packet:
            code = "def solve(value):\n    return None\n"
        else:
            examples = repr(packet["public_examples"])
            code = (f"EXAMPLES = {examples}\ndef solve(value):\n"
                    "    for example in EXAMPLES:\n"
                    "        if example['input'] == value:\n"
                    "            return example['expected']\n"
                    "    return None\n")
        return {"code": code, "cost_usd": 0}


def run(args):
    raw = args.tasks.read_bytes()
    corpus = strict_json(raw)
    if set(corpus) != {"version", "tasks"} or corpus["version"] != "1.0":
        raise ValueError("unsupported task corpus")
    tasks = corpus["tasks"]
    validate_tasks(tasks)
    limits = Limits(rounds=args.rounds, dollars=args.per_arm_usd, seconds=args.per_arm_seconds)
    limits.validate()
    assigned_reservation = 2 * len(tasks) * limits.dollars
    if args.live:
        if (args.total_usd is None or not math.isfinite(args.total_usd) or args.total_usd <= 0
                or assigned_reservation > args.total_usd + 1e-9):
            raise ValueError("live mode requires a total cap covering every assigned arm reservation")
        worker = LiveWorker(args.config)
    else:
        worker = RecordedWorker()
    sandbox = DockerSandbox(args.image, args.docker)
    image = sandbox.preflight()
    identity = source_identity()
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"version": "1.0", "identity": identity, "image": image, "limits": asdict(limits),
                "task_set_sha256": digest(tasks), "worker": "live" if args.live else "recorded-control",
                "model": getattr(worker, "model", None), "assigned_reservation_usd": assigned_reservation,
                "provider_configuration": asdict(worker.provider.config) if args.live else None,
                "billing_reconciled": False, "qualification_authority": False}
    (output / "manifest.json").write_bytes(canonical(manifest))
    (output / "tasks.json").write_bytes(raw)
    interrupted = False

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = signal.signal(signal.SIGINT, stop)
    try:
        with (output / "events.jsonl").open("xb") as journal:
            def emit(event):
                journal.write(canonical(event))
                journal.flush()
                os.fsync(journal.fileno())
            report = run_experiment(tasks, worker, sandbox, limits, emit=emit, cancelled=lambda: interrupted)
        report.update({"source_unchanged": source_identity() == identity, "manifest_sha256": digest(manifest),
                       "model_quality_measured": args.live, "worker": manifest["worker"]})
        (output / "report.json").write_bytes(canonical(report))
        return report
    finally:
        signal.signal(signal.SIGINT, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=Path("harness/feedback/fixtures.json"))
    parser.add_argument("--image", required=True, help="local immutable sha256 image ID")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--per-arm-seconds", type=float, default=120)
    parser.add_argument("--per-arm-usd", type=float, default=.10)
    parser.add_argument("--total-usd", type=float)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, default=Path(".factory/kernel.json"))
    args = parser.parse_args()
    try:
        report = run(args)
    except (ValueError, OSError, SandboxError) as exc:
        print(f"FEEDBACK_REFUSED {type(exc).__name__}: {exc}")
        return 2
    complete = report["source_unchanged"] and all(r["status"] == "observed" for r in report["attempts"])
    print(f"FEEDBACK_{'OBSERVED' if complete else 'INCOMPLETE'} arms={report['arms']} output={args.output}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
