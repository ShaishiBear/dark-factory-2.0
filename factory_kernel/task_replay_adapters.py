"""Reviewed offline replay adapters. Inputs are data, never commands or plugins.

These execute existing production rules; validation orchestration reuses the
existing rehearsal's explicit fake GitHub/provider/executor. No model quality or
live merge claim follows from their results. Expected answers never arrive here.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

from factory_kernel.benchmark import canonical

ADAPTERS = frozenset({"contract", "lease", "repro", "validation"})


def fields(value: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("adapter input fields do not match its contract")


def observe(adapter: str, inputs: dict) -> dict:
    """Return observations from a fixed implementation, not a fixture's label."""
    if adapter == "contract":
        from scripts.factory_protocol import validate_contract

        fields(inputs, {"contract"})
        if not isinstance(inputs["contract"], dict):
            raise ValueError("contract must be an object")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            try:
                value = validate_contract(inputs["contract"])
            except SystemExit as exc:
                if exc.code != 1 or not output.getvalue().startswith("PROTOCOL_FAIL:"):
                    raise
                return {"outcome": "stop", "level": "production-rule",
                        "evidence": {"compiler_refusal": output.getvalue()[-4000:]}}
        return {"outcome": "wait", "level": "production-rule",
                "evidence": {"contract_sha256": value}}

    if adapter == "lease":
        from scripts.factory_lease import decide_reap

        fields(inputs, {"now", "accepted", "updated", "lease", "marker_seen", "handoff",
                        "active_ttl", "legacy_ttl"})
        values = dict(inputs)
        for key in ("now", "updated"):
            values[key] = datetime.fromisoformat(values[key])
            if values[key].tzinfo is None:
                raise ValueError("lease times must have timezones")
        for key in ("accepted", "marker_seen"):
            if not isinstance(values[key], bool):
                raise ValueError("lease flags must be boolean")
        for key in ("active_ttl", "legacy_ttl"):
            if type(values[key]) is not int or values[key] <= 0:
                raise ValueError("lease TTL must be a positive integer")
        action, reason = decide_reap(**values)
        return {"outcome": {"reap": "recover", "keep": "wait", "protect": "needs-human"}[action],
                "level": "production-rule", "evidence": {"action": action, "reason": reason}}

    if adapter == "repro":
        from factory_kernel.repro import execute, ReproRefused, validate_repro

        fields(inputs, {"repro", "returncode", "stdout"})
        if type(inputs["returncode"]) is not int or not isinstance(inputs["stdout"], str):
            raise ValueError("repro observation is malformed")
        calls = []

        def recorded_runner(argv, cwd, env, timeout):
            calls.append({"argv": list(argv), "cwd": ".", "timeout": timeout})
            return subprocess.CompletedProcess(argv, inputs["returncode"], inputs["stdout"], "")

        try:
            repro = validate_repro(inputs["repro"])
            # Only the root exists. The fixture cannot cause arbitrary directories to be created.
            with tempfile.TemporaryDirectory() as tmp:
                result = execute(repro, worktree=Path(tmp), runner=recorded_runner)
        except ReproRefused as exc:
            return {"outcome": "do-not-guess", "level": "rule-with-recorded-process",
                    "evidence": {"refusal": str(exc), "calls": calls}}
        return {"outcome": "wait", "level": "rule-with-recorded-process",
                "evidence": {"output_sha256": result.output_sha256, "calls": calls}}

    if adapter == "validation":
        from harness.rehearsal import rehearse, Scenario

        # Exclude the rehearsal's file-writing and arbitrary failure-output fixtures.
        fields(inputs, set(), {"reject", "fail", "head", "pack_base_is_ancestor"})
        if inputs.get("reject") not in {None, "holdout", "architecture-holdout",
                                         "contract-certifier", "design-certifier", "governor-certifier"}:
            raise ValueError("unknown rehearsal authority")
        if inputs.get("fail") not in {None, "factory_security.py", "factory_evidence.py",
                                      "merge_verify.py:pre", "merge_verify.py:post"}:
            raise ValueError("unknown rehearsal failure seam")
        if "head" in inputs:
            from factory_kernel.benchmark import OID
            if not isinstance(inputs["head"], str) or not OID.fullmatch(inputs["head"]):
                raise ValueError("head must be a full object id")
        if "pack_base_is_ancestor" in inputs and not isinstance(inputs["pack_base_is_ancestor"], bool):
            raise ValueError("ancestry observation must be boolean")
        with contextlib.redirect_stdout(io.StringIO()):
            trace = rehearse(Scenario("offline-replay", **inputs))
        if trace.outcome not in {"returned", "NeedsHuman", "PostMergeUnverified", "ToolRefused"}:
            raise RuntimeError("unexpected orchestration failure: " + trace.outcome)
        merge_called = trace.happened("merge_squash")
        completed = trace.outcome == "returned" and merge_called
        outcome = "merge" if completed else ("needs-human" if merge_called else "reject")
        return {"outcome": outcome, "level": "simulated-orchestration",
                "evidence": {"terminal": trace.outcome, "merge_called": merge_called,
                             "steps": [{"kind": step.kind, "name": step.name} for step in trace.steps]}}
    raise ValueError("unknown replay adapter")


def blocked(*args, **kwargs):
    raise RuntimeError("offline adapter attempted external execution or network access")


def main() -> None:
    """Fixed child entrypoint; no import path or source code comes from the suite."""
    request = json.loads(sys.stdin.read(256_001))
    fields(request, {"adapter", "inputs"})
    if request["adapter"] not in ADAPTERS:
        raise ValueError("unknown replay adapter")
    # Regression tripwires for reviewed Python only, NOT a sandbox for hostile code.
    with patch("subprocess.Popen", side_effect=blocked), patch("os.system", side_effect=blocked), \
            patch.object(socket, "socket", side_effect=blocked), \
            patch.object(socket, "create_connection", side_effect=blocked):
        result = observe(request["adapter"], request["inputs"])
    sys.stdout.buffer.write(canonical(result))


if __name__ == "__main__":
    main()
