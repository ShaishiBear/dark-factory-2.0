"""Account for each diagnostic process before it starts; never retry an uncertain call.

Every launch is described by a strict `dark-factory/diagnostic-result` record: which phase
the call reached, whether the provider was started (`not_started`, `started` or `unknown`),
what refused or failed, and what remains uncertain. The record is written atomically whether
or not a normal run directory exists, and carries no environment and no secret. It retains
what happened; it does not infer a cause.

Worker runs 35249730246, 35216659945, 35188495138 and 35166723469 (17 September 2026) all
failed at the route probe with `stop_reason=unreadable` and a traceback the workflow cut off
before its exception class. Nothing retained could say whether this wrapper refused before a
launch or the provider failed after one. The record below answers that for the next run; it
does not answer it for those four (WP00, C06).
"""
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import secrets
import subprocess
import sys
import traceback as traceback_module
from types import SimpleNamespace

from .canonical import canonical_bytes, sha256_bytes
from .execution_budget import microusd
from .execution_client import ExecutionClient
from .frontdoor_intent import IntentRefused
from .github_cli import GitHubClient
from .providers import ClaudeCliProvider, parse_events
from .refusal import (
    DIAGNOSTIC_ROLES, DIAGNOSTIC_SCHEMA, DIAGNOSTIC_SCHEMA_VERSION, classify_diagnostic,
    diagnostic_summary, scrub, validate_diagnostic,
)
from . import publication_policy as policy

MAINTENANCE_WORKFLOW = ".github/workflows/dark-factory-main-regression.yml"
# A directory the host names for per-call records when no explicit `--result-path` is given,
# so the effort, thinking-cap, read-scope and test-author probes retain their launches too.
DIAGNOSTICS_DIR_ENV = "FACTORY_DIAGNOSTICS_DIR"
MESSAGE_LIMIT = 2000
TRACEBACK_LIMIT = 8000
RECORD_LIMIT = 65536


@dataclass(frozen=True)
class ProbeRequest:
    """Identity of an already rendered diagnostic command, not a provider AgentRequest."""
    role: str
    argv: list[str]
    cwd: str
    timeout: float | None
    thinking_cap: str | None
    max_budget_usd: int = 1


def diagnostic(*, invocation_id, role, phase, status, reason_code, provider_started, metering,
               reservation_id=None, exception=None, exit_code=None, output=None, receipts=None,
               uncertainty=()):
    """Build and validate one record. Text is scrubbed; no environment is ever included."""
    record = {
        "schema": DIAGNOSTIC_SCHEMA, "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "invocation_id": invocation_id, "role": role, "phase": phase, "status": status,
        "reason_code": reason_code, "provider_started": provider_started, "metering": metering,
        "reservation_id": reservation_id, "error_type": None, "error_message": None, "traceback": None,
        "exit_code": exit_code, "output_sha256": None, "output_bytes": None,
        "reported_microusd": None, "cost_reason": "unknown", "uncertainty": sorted(set(uncertainty)),
    }
    if exception is not None:
        record["error_type"] = type(exception).__name__
        record["error_message"] = scrub(str(exception))[:MESSAGE_LIMIT]
        formatted = "".join(traceback_module.format_exception(type(exception), exception, exception.__traceback__))
        record["traceback"] = scrub(formatted)[-TRACEBACK_LIMIT:]
    if output is not None:
        raw = output.encode("utf-8", "replace")
        record["output_sha256"], record["output_bytes"] = sha256_bytes(raw), len(raw)
    if provider_started == "not_started":
        record["cost_reason"] = "not_launched"
    elif receipts is None:
        record["cost_reason"] = "unknown"
    elif exit_code not in (0, None):
        record["cost_reason"] = "nonzero_exit"
    elif len(receipts) != 1:
        record["cost_reason"] = "no_receipt" if not receipts else "ambiguous_receipt"
    else:
        try:
            record["reported_microusd"] = microusd(receipts[0].get("total_cost_usd"))
            record["cost_reason"] = "reported"
        except (IntentRefused, TypeError, AttributeError):
            record["cost_reason"] = "no_receipt"
    if record["reported_microusd"] is None and provider_started != "not_started":
        record["uncertainty"] = sorted({*record["uncertainty"], "cost_unknown"})
    if provider_started == "unknown":
        record["uncertainty"] = sorted({*record["uncertainty"], "provider_start_unknown"})
    return validate_diagnostic(record)


def write_diagnostic(path, record):
    """Atomic, bounded, validated. A partial file is never left where a reader could find it."""
    raw = canonical_bytes(validate_diagnostic(record))
    if len(raw) > RECORD_LIMIT:
        raise ValueError("diagnostic record exceeds its bound")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return raw


class ProbeRunner:
    def __init__(self, role, client, *, runner=None, result_path=None, result_dir=None, metering=None, scope=None):
        if role not in DIAGNOSTIC_ROLES:
            raise IntentRefused("unknown diagnostic role")
        self.role, self.client = role, client
        # A validation-meter scope (WP02): every launch takes a per-call ceiling inside one bundle
        # the ledger already holds, instead of its own reservation exchange. The scope's class must
        # be the diagnostic class; the meter refuses anything else before a process exists.
        self.scope = scope
        if scope is not None and getattr(scope, "scope_class", None) != "diagnostic-probe":
            raise IntentRefused("diagnostic launches need a diagnostic-probe scope")
        self.runner = runner or subprocess.run
        self.result_path = Path(result_path) if result_path else None
        self.result_dir = Path(result_dir) if result_dir else None
        self.metering = metering or ("metered-bundle" if scope is not None else "metered" if client is not None else "unknown")
        self.last_diagnostic = None

    @classmethod
    def from_environment(cls, role, *, result_path=None):
        ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
        result_dir = os.environ.get(DIAGNOSTICS_DIR_ENV) or None
        # The daily maintenance measurements have their own existing spending scope, recorded
        # here by name so the inventory can see it. No arbitrary Actions workflow, rerun or
        # missing worker key can use that exception.
        maintenance = policy.REPOSITORY + "/" + MAINTENANCE_WORKFLOW + "@refs/heads/main"
        if not os.environ.get("GITHUB_ACTIONS") and not ref:
            os.environ.pop("FRONTDOOR_AGE_IDENTITY", None)
            return cls(role, None, result_path=result_path, result_dir=result_dir, metering="unmetered-local")
        if ref == maintenance:
            os.environ.pop("FRONTDOOR_AGE_IDENTITY", None)
            return cls(role, None, result_path=result_path, result_dir=result_dir,
                       metering="unmetered-maintenance-scope")
        github = GitHubClient(policy.REPOSITORY, cwd=Path.cwd())
        return cls(role, ExecutionClient.from_environment(github), result_path=result_path,
                   result_dir=result_dir, metering="metered")

    def _launch(self, argv, kwargs, state):
        """The one place a process may start. What we know afterwards is three-valued."""
        try:
            result = self.runner(argv, **kwargs)
        except OSError:
            # The interpreter could not create the process: nothing reached the provider.
            state["provider_started"] = "not_started"
            raise
        except BaseException:
            # A timeout, a signal, a lost handle: the process may have reached the provider.
            state["provider_started"] = "unknown"
            raise
        state["provider_started"] = "started"
        state["result"] = result
        return result

    def _record(self, invocation_id, state, *, exception=None):
        result, receipts = state.get("result"), state.get("receipts")
        status, reason = classify_diagnostic(state["phase"], exception, result=result, receipts=receipts)
        record = diagnostic(
            invocation_id=invocation_id, role=self.role, phase=state["phase"], status=status, reason_code=reason,
            provider_started=state["provider_started"], metering=self.metering,
            reservation_id=state.get("reservation_id"), exception=exception,
            exit_code=None if result is None else int(result.returncode),
            output=None if result is None else str(result.stdout or ""), receipts=receipts,
        )
        self.last_diagnostic = record
        destination = self.result_path or (self.result_dir and self.result_dir / f"{self.role}-{invocation_id}.json")
        if destination is not None:
            try:
                write_diagnostic(destination, record)
            except OSError as exc:
                sys.stderr.write(f"FACTORY_DIAGNOSTIC_UNRETAINED {type(exc).__name__}\n")
        return record

    def with_scope(self, scope):
        """The same runner, result paths and client, launching inside a validation bundle."""
        return ProbeRunner(self.role, self.client, runner=self.runner, result_path=self.result_path,
                           result_dir=self.result_dir, scope=scope)

    def _launch_metered(self, invocation_id, request, argv, kwargs, state):
        """One launch inside the bundle: ceiling reserved, started once, observed once. The
        one-dollar CLI bound is the ceiling charged; a refusal by the meter is an admission
        refusal that never creates a process."""
        from .validation_meter import MeterRefused, observe_call, reserve_call, start_call

        try:
            call = reserve_call(self.scope, call_class="diagnostic-probe", microusd=microusd(1),
                                request_sha256=sha256_bytes(canonical_bytes(asdict(request))))
        except MeterRefused as exc:
            raise IntentRefused(str(exc)) from exc
        # The call identity is the reservation identity of this launch; the bundle it sits in is
        # named by the meter record (meter-<bundle>.json) beside the diagnostics.
        state["reservation_id"] = call.call_id
        start_call(self.scope, call.call_id)
        state["phase"] = "launch"
        try:
            result = self._launch(argv, kwargs, state)
        except BaseException:
            observe_call(self.scope, call.call_id, reported_microusd=None, outcome="failed",
                         telemetry={"provider_started": state["provider_started"]})
            raise
        state["phase"] = "parse"
        events = parse_events(result.stdout or "")
        receipts = [row for row in events if row.get("type") == "result"]
        state["receipts"] = receipts
        cost = receipts[0].get("total_cost_usd") if len(receipts) == 1 and result.returncode == 0 else None
        try:
            reported = microusd(cost) if cost is not None else None
        except IntentRefused:
            reported = None
        state["phase"] = "observe"
        observe_call(self.scope, call.call_id, reported_microusd=reported,
                     outcome="returned" if result.returncode == 0 else "failed",
                     telemetry={"exit_code": int(result.returncode), "receipts": len(receipts)})
        state["phase"] = "returned"
        self._record(invocation_id, state)
        return result

    def __call__(self, argv, **kwargs):
        invocation_id = secrets.token_hex(16)
        state = {"phase": "admission", "provider_started": "not_started", "reservation_id": None,
                 "result": None, "receipts": None}
        try:
            # Strip host, GitHub and validation authority, including from the thinking probe's
            # explicitly supplied environment. Retain only the existing model allowlist and
            # the two probe controls intentionally passed by protected probe code.
            supplied = kwargs.get("env", os.environ)
            allowed = ClaudeCliProvider._worker_env({})
            env = {key: value for key, value in supplied.items() if key in allowed}
            for key in ("MAX_THINKING_TOKENS", "ARTIFACTS_DIR"):
                if key in supplied:
                    env[key] = supplied[key]
            kwargs = {**kwargs, "env": env}
            if self.client is None and self.scope is None:
                state["phase"] = "launch"
                result = self._launch(argv, kwargs, state)
                state["phase"] = "parse"
                state["receipts"] = [row for row in parse_events(result.stdout or "") if row.get("type") == "result"]
                state["phase"] = "returned"
                self._record(invocation_id, state)
                return result
            # The bound on the launched binary must match the one charged to the ledger.
            if (not isinstance(argv, list) or argv.count("--max-budget-usd") != 1
                    or argv[argv.index("--max-budget-usd") + 1:] == []
                    or argv[argv.index("--max-budget-usd") + 1] != "1"):
                raise IntentRefused("diagnostic requires its protected one-dollar CLI bound")
            request = ProbeRequest(role=self.role, argv=list(argv), cwd=str(kwargs.get("cwd", Path.cwd())),
                timeout=kwargs.get("timeout"), thinking_cap=env.get("MAX_THINKING_TOKENS"))
            state["phase"] = "reservation"
            if self.scope is not None:
                return self._launch_metered(invocation_id, request, argv, kwargs, state)

            def run(_request, **_options):
                state["reservation_id"] = getattr(self.client, "last_call_id", None)
                state["phase"] = "launch"
                result = self._launch(argv, kwargs, state)
                state["phase"] = "parse"
                events = parse_events(result.stdout or "")
                receipts = [row for row in events if row.get("type") == "result"]
                state["receipts"] = receipts
                # Missing/ambiguous/failed telemetry blocks the next reservation. Probe output
                # can still report a failed measurement; it cannot turn unknown spend into zero.
                cost = receipts[0].get("total_cost_usd") if len(receipts) == 1 and result.returncode == 0 else None
                state["phase"] = "observe"
                return SimpleNamespace(cost_usd=cost)

            self.client.run(SimpleNamespace(run=run), request)
            state["reservation_id"] = state["reservation_id"] or getattr(self.client, "last_call_id", None)
            state["phase"] = "returned"
            self._record(invocation_id, state)
            return state["result"]
        except BaseException as exc:
            if state["reservation_id"] is None and self.client is not None:
                state["reservation_id"] = getattr(self.client, "last_call_id", None)
            self._record(invocation_id, state, exception=exc)
            raise


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    result_path = None
    # An optional host-selected record path, before the existing `--` delimiter. The legacy
    # form without it is unchanged.
    if args[:1] == ["--result-path"]:
        if len(args) < 2 or not args[1] or args[1].startswith("-"):
            raise IntentRefused("--result-path needs a file path")
        result_path, args = Path(args[1]), args[2:]
    if args[:1] != ["--"] or len(args) < 3:
        raise IntentRefused("expected -- followed by the bounded diagnostic command")
    runner = None
    record = None
    try:
        runner = ProbeRunner.from_environment("diagnostic-route", result_path=result_path)
        result = runner(args[1:], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=190)
        record = runner.last_diagnostic
    except BaseException as exc:
        if runner is not None and runner.last_diagnostic is not None:
            record = runner.last_diagnostic
        else:
            # Refused or failed before a runner existed: the identity phase. Nothing launched.
            status, reason = classify_diagnostic("identity", exc)
            record = diagnostic(invocation_id=secrets.token_hex(16), role="diagnostic-route", phase="identity",
                                status=status, reason_code=reason, provider_started="not_started",
                                metering="unknown", exception=exc)
        raise
    finally:
        if record is not None:
            # The public summary first, so a log that keeps only the head of stderr still names
            # the phase and the exception class. The private record holds the traceback.
            sys.stderr.write(diagnostic_summary(record) + "\n")
            if runner is None or runner.last_diagnostic is None:
                # No runner ever existed (the identity phase), so nothing else has written the
                # record. The host's directory is the fallback when no explicit path was given:
                # this is the worker's actual invocation, and the four failed runs are exactly
                # the case where this record is the only retained fact.
                directory = os.environ.get(DIAGNOSTICS_DIR_ENV) or None
                destination = result_path or (directory and Path(directory) / f"{record['role']}-{record['invocation_id']}.json")
                if destination is not None:
                    try:
                        write_diagnostic(destination, record)
                    except OSError as exc:
                        sys.stderr.write(f"FACTORY_DIAGNOSTIC_UNRETAINED {type(exc).__name__}\n")
    sys.stdout.write(scrub(result.stdout or ""))
    sys.stderr.write(scrub(result.stderr or ""))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
