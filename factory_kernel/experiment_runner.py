"""Contained execution of registered experiments (SPECIFICATION 8, C08 `execute_registered`, WP08).

An experiment the exploration service reserved is run here, not in the service's own process:
`execute_registered` acquires a lease for the session's experiment slot in the lease store,
spawns a fresh Python interpreter on this module's child entry with an allowlisted environment
(only what the interpreter and platform need to start; nothing else is inherited, so no credential is),
an empty temporary working directory and a wall clock, hands it exactly one JSON object (the
validated spec and the session's frozen repository context) on stdin, and reads exactly one
JSON object back. The child resolves the family through the registry (`experiments.run_experiment`);
nothing a model supplies can name a command, an image, an import path or a file. The parent
re-validates what comes back: the runner is the spec's family, the results cover exactly the
spec's strategies with the family's metrics, the receipt binds the exact spec and the frozen
context, and its scope is the family's. A child that does not finish within the wall clock is a timeout, which proves only
that the bounded workload did not finish within the stated limit; a child that fails to start
or exits non-zero is a failed attempt, never a measurement; a malformed or forged receipt is
refused. Every attempt is recorded, including failures, and the lease is released whatever
happened.

This is a process boundary with an allowlisted environment and no network use by construction of
the families, not an OS or container sandbox: the child can still read the filesystem. The
attempt record says so (`containment`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping

from .canonical import sha256_value
from .experiments import FAMILIES, metrics_for, run_experiment, validate_experiment
from .lease_store import LeaseBundle, LeaseRefused, LeaseStore

SCHEMA = "dark-factory/experiment-attempt"
SCHEMA_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[1]
WALL_SECONDS = 30            # the family's own deadline is at most 10 s; the rest is interpreter start
LEASE_TTL_SECONDS = 120
MAX_INPUT_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 2_000_000
MAX_STDERR_CHARS = 2000
# The child's environment is built from an allowlist, never inherited: the interpreter and the
# platform need these to start (PATH and the Windows system variables, a temp directory, a home)
# and nothing else crosses, so no credential can, whatever the service's own environment holds.
CHILD_ENVIRONMENT_KEYS = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                          "LANG", "LC_ALL", "TZ")
CONTAINMENT = "subprocess: fresh interpreter, allowlisted environment (no inherited variable but PATH, the platform's and a temp/home; no credentials), empty temporary cwd, stdin/stdout JSON only, wall clock; not an OS or container sandbox"


class ExperimentTimeout(RuntimeError):
    """The bounded workload did not finish within the stated environment and limit."""


class ExperimentFailed(RuntimeError):
    """The child failed to start or exited without a result."""


class ExperimentRefused(ValueError):
    """The child's result is not a receipt of the requested experiment."""


def reserve(leases: LeaseStore, *, session_id: str, reservation_id: str, spec: Mapping[str, Any], now: int,
            ttl_seconds: int = LEASE_TTL_SECONDS) -> LeaseBundle:
    """One experiment at a time per session, plus the shared runner slot. A live lease on either
    resource refuses the reservation; nothing is spent by a refusal."""
    validate_experiment(spec)
    return leases.acquire_many(owner=f"exploration:{session_id}", role="experiment-runner",
                               resources=[f"experiment:{session_id}", "experiment-runner"],
                               request_sha256=sha256_value(dict(spec)), subject_sha256=sha256_value({"reservation": reservation_id}),
                               ttl_seconds=ttl_seconds, now=now)


def cancel(leases: LeaseStore, bundle: LeaseBundle, *, now: int) -> dict:
    """Release without a result. The reservation the service recorded stays charged; only the
    runner slot is freed."""
    return leases.release(bundle, now=now).to_dict()


def child_environment() -> dict[str, str]:
    """Only the allowlisted keys the interpreter and platform need, plus the import path."""
    env = {key: os.environ[key] for key in CHILD_ENVIRONMENT_KEYS if key in os.environ}
    env.update({"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"})
    return env


def _spawn(payload: bytes, *, wall_seconds: float, python: str) -> subprocess.CompletedProcess:
    """The one process launch. Environment allowlisted, cwd empty, fixed entry, bounded wall clock."""
    env = child_environment()
    with tempfile.TemporaryDirectory(prefix="dark-factory-experiment-") as cwd:
        # -s: no user site-packages (a user's sitecustomize/usercustomize would run in the child);
        # -B: no bytecode written into the kernel tree by the child.
        return subprocess.run([python, "-s", "-B", "-m", "factory_kernel.experiment_runner", "--child"], input=payload,
                              capture_output=True, cwd=cwd, env=env, timeout=wall_seconds)


def context_bound(kind: str) -> bool:
    """Whether the family's receipt is measured over the frozen repository context (the registry's
    own environment statement), as opposed to a data-only workload."""
    return "frozen repository context" in str(FAMILIES[kind]["environment"])


def verify_receipt(spec: Mapping[str, Any], context: Mapping[str, Any], receipt: Any) -> dict:
    """The child's receipt must be the requested experiment's: its family, its strategies, the
    family's metrics, the frozen context, and an identity that recomputes."""
    if not isinstance(receipt, Mapping) or receipt.get("runner") != spec["kind"] or receipt.get("kind") != "measured":
        raise ExperimentRefused("the result is not a measured receipt of the requested family")
    strategies = spec["strategies"] if isinstance(spec["strategies"], list) else sorted(spec["strategies"])
    results = receipt.get("results")
    if not isinstance(results, Mapping) or set(results) != set(strategies):
        raise ExperimentRefused("the receipt does not cover exactly the requested strategies")
    allowed = metrics_for(spec["kind"])
    for name, row in results.items():
        # A row is the family's numeric metrics, plus an optional `detail` mapping the family
        # documents (paths, gaps); nothing else, and no metric the contract does not name.
        if not isinstance(row, Mapping) or not isinstance(row.get("detail", {}), Mapping):
            raise ExperimentRefused(f"strategy {name!r} reports a malformed row")
        metrics = {k: v for k, v in row.items() if k != "detail"}
        if not set(metrics) <= allowed or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in metrics.values()):
            raise ExperimentRefused(f"strategy {name!r} reports metrics outside the family's contract")
    if receipt.get("input_sha256") != sha256_value(dict(spec)):
        raise ExperimentRefused("the receipt names another spec")
    # A family that reads the frozen repository context must bind it in its receipt; a family that
    # is data-only must not claim one. Either way the receipt says exactly what it was measured over.
    if context_bound(spec["kind"]):
        if receipt.get("context_identity") != context.get("identity"):
            raise ExperimentRefused("the receipt does not bind the frozen repository context")
    elif "context_identity" in receipt:
        raise ExperimentRefused("a context-free family's receipt claims a repository context")
    if receipt.get("scope") != FAMILIES[spec["kind"]]["claim_scope"] or receipt.get("qualification_status") != "UNPROVEN":
        raise ExperimentRefused("the receipt's scope or qualification status is not the family's")
    return dict(receipt)


def execute_registered(spec: Mapping[str, Any], *, context: Mapping[str, Any], check_stop: Callable[[], None] = lambda: None,
                       leases: LeaseStore | None = None, session_id: str = "session", reservation_id: str = "reservation",
                       now: int | None = None, wall_seconds: float = WALL_SECONDS, python: str = sys.executable) -> dict:
    """Run one reserved, validated experiment in a contained child and return its verified
    receipt with the attempt record under `attempt`. Raises ExperimentTimeout / ExperimentFailed
    / ExperimentRefused; the lease is released in every case."""
    validate_experiment(spec)
    check_stop()
    clock = int(time.time()) if now is None else now
    bundle = reserve(leases, session_id=session_id, reservation_id=reservation_id, spec=spec, now=clock) if leases is not None else None
    attempt: dict[str, Any] = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "family": spec["kind"], "containment": CONTAINMENT,
                               "wall_seconds": wall_seconds, "lease_id": bundle.lease_id if bundle else None, "status": "started"}
    started = time.monotonic()
    try:
        payload = json.dumps({"spec": dict(spec), "context": dict(context)}, sort_keys=True).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise ExperimentRefused("experiment input exceeds the size bound")
        try:
            completed = _spawn(payload, wall_seconds=wall_seconds, python=python)
        except subprocess.TimeoutExpired as exc:
            attempt.update(status="timeout", detail="the bounded workload did not finish within the stated environment and limit")
            raise ExperimentTimeout(attempt["detail"]) from exc
        except OSError as exc:
            attempt.update(status="failed-to-start", detail=type(exc).__name__)
            raise ExperimentFailed("the experiment child failed to start") from exc
        attempt["stderr_tail"] = completed.stderr.decode("utf-8", errors="replace")[-MAX_STDERR_CHARS:]
        if completed.returncode != 0 or len(completed.stdout) > MAX_OUTPUT_BYTES:
            attempt.update(status="failed", detail=f"exit {completed.returncode}")
            raise ExperimentFailed(f"the experiment child exited {completed.returncode}")
        try:
            result = json.loads(completed.stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            attempt.update(status="refused", detail="malformed child output")
            raise ExperimentRefused("the experiment child returned no JSON object") from exc
        if not isinstance(result, Mapping) or result.get("status") != "complete":
            attempt.update(status="failed", detail=str(result.get("failure") if isinstance(result, Mapping) else "malformed")[:200])
            raise ExperimentFailed("the experiment child reported a failure: " + attempt["detail"])
        try:
            receipt = verify_receipt(spec, context, result.get("receipt"))
        except ExperimentRefused as exc:
            attempt.update(status="refused", detail=str(exc))
            raise
        attempt.update(status="complete", child_environment_keys=sorted(result.get("environment_keys", [])) if isinstance(result.get("environment_keys"), list) else None)
        return {**receipt, "attempt": attempt}
    except (ExperimentTimeout, ExperimentFailed, ExperimentRefused) as exc:
        exc.attempt = attempt  # the service records the attempt with the failed observation
        raise
    finally:
        attempt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if bundle is not None:
            attempt["lease_release"] = leases.release(bundle, now=int(time.time()) if now is None else now).to_dict()
        check_stop()


def child_main() -> int:
    """The fixed child entry: one JSON object in, one JSON object out. Never a shell, never a
    model-named command; the family is resolved by the registry from the spec's kind."""
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        payload = json.loads(raw.decode("utf-8"))
        spec, context = payload["spec"], payload["context"]
        receipt = run_experiment(spec, context=context, check_stop=lambda: None)
        out = {"status": "complete", "receipt": receipt, "environment_keys": sorted(os.environ)}
    except Exception as exc:  # the parent turns this into a failed attempt, never a measurement
        out = {"status": "failed", "failure": type(exc).__name__}
    sys.stdout.write(json.dumps(out, sort_keys=True))
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--child"]:
        return child_main()
    sys.stderr.write("experiment_runner: only the --child entry is runnable; experiments are reserved and executed by the exploration service\n")
    return 2


__all__ = ["CHILD_ENVIRONMENT_KEYS", "CONTAINMENT", "ExperimentFailed", "ExperimentRefused", "ExperimentTimeout", "LEASE_TTL_SECONDS", "SCHEMA", "SCHEMA_VERSION",
           "WALL_SECONDS", "cancel", "child_environment", "child_main", "context_bound", "execute_registered", "reserve", "verify_receipt"]


if __name__ == "__main__":
    raise SystemExit(main())
