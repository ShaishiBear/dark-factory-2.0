"""Fixed-origin worker transport and one-use provider adapter; never exports owner tokens."""
from dataclasses import asdict
import os
from pathlib import Path
import secrets
import tempfile
from urllib.request import Request, build_opener

from .canonical import canonical_bytes, sha256_value
from .execution_budget import microusd
from .execution_exchange import ExecutionProtocol, MAX_ENVELOPE
from .frontdoor_intent import IntentRefused
from .programme import compile_programme, parse_json
from .publication_client import NoRedirect
from .publication_currency import key_from_identity
from .publication_source import observe_publication_source
from . import publication_policy as policy


class ExecutionClient:
    def __init__(self, protocol, binding, *, opener=None):
        if protocol.repository != policy.REPOSITORY or protocol.project != policy.PROJECT:
            raise IntentRefused("execution client destination differs from protected policy")
        self.protocol, self.binding = protocol, dict(binding)
        self.opener = opener if opener is not None else build_opener(NoRedirect())

    @classmethod
    def from_environment(cls, github):
        # Called by protected code only. Capture/remove this capability before any child
        # process; the owner bearer and GitHub App private key are never needed here.
        secret = os.environ.pop("FRONTDOOR_AGE_IDENTITY", "")
        workflow_ref = policy.REPOSITORY + "/.github/workflows/dark-factory-worker.yml@refs/heads/main"
        if (not secret or len(secret) > 1024 or os.environ.get("GITHUB_WORKFLOW_REF") != workflow_ref
                or os.environ.get("GITHUB_REPOSITORY") != policy.REPOSITORY
                or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"):
            raise IntentRefused("protected execution identity or first-attempt workflow is unavailable")
        source = observe_publication_source(github)
        if source["main_sha"] != os.environ.get("GITHUB_SHA") or source["active_input"] is None:
            raise IntentRefused("execution client requires the current active protected programme")
        programme = compile_programme(source["active_input"], repository=policy.REPOSITORY)
        with tempfile.TemporaryDirectory(prefix="factory-execution-key-") as directory:
            path = Path(directory) / "identity.age"
            path.touch(mode=0o600)
            path.write_text(secret, encoding="utf-8")
            protocol = ExecutionProtocol(key_from_identity(path), repository=policy.REPOSITORY, project=policy.PROJECT)
        return cls(protocol, {"run_id": int(os.environ.get("GITHUB_RUN_ID", "0")), "run_attempt": 1,
                              "source_sha": source["main_sha"], "programme_sha256": programme.sha256})

    def exchange(self, phase, call, payload):
        envelope = self.protocol.challenge(phase, call, payload)
        request = Request(policy.ORIGIN + "/api/execution-reservation", data=canonical_bytes(envelope),
            headers={"Content-Type": "application/json", "Origin": policy.ORIGIN}, method="POST")
        # Never retry a mutating exchange. A lost response may have charged or started the call.
        with self.opener.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != request.full_url:
                raise IntentRefused("execution response origin or status refused")
            raw = response.read(MAX_ENVELOPE + 1)
        if len(raw) > MAX_ENVELOPE:
            raise IntentRefused("execution response exceeds its bound")
        return self.protocol.verify(parse_json(raw.decode("utf-8")), envelope)

    def run(self, provider, request, *, transcript=None, before_retry=None):
        call = {**self.binding, "id": secrets.token_hex(16), "role": request.role,
                "microusd": microusd(request.max_budget_usd), "request_sha256": sha256_value(asdict(request))}
        # The reservation identity this call is charged under, for the diagnostic record of
        # whoever launched it. Observation only: nothing reads it to decide anything.
        self.last_call_id = call["id"]
        reservation = self.exchange("reserve", call, {})
        if reservation["status"] != "reserved":
            raise IntentRefused("historical reservation cannot authorize a worker call")
        started = self.exchange("start", call, {"reservation_version": reservation["project_version"]})
        if started["status"] != "start-once":
            raise IntentRefused("worker reservation was already consumed")

        def refuse_retry(_attempt):
            # The first attempt has not returned telemetry yet; neither restoring a worktree
            # nor creating another CLI process can resolve its unknown spend.
            raise IntentRefused("unobserved worker retry refused")

        try:
            result = provider.run(request, transcript=transcript, before_retry=refuse_retry)
        except BaseException:
            try:
                self.exchange("observe", call, {"reported_microusd": None, "outcome": "failed"})
            except Exception:
                pass  # Durable reservation/start survive; preserve the original failure.
            raise
        try:
            reported = microusd(result.cost_usd) if result.cost_usd is not None else None
        except (IntentRefused, AttributeError):
            reported = None
        self.exchange("observe", call, {"reported_microusd": reported, "outcome": "returned"})
        return result
