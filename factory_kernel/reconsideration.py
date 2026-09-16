"""Evidence-bound owner judgments; neither a failure authority nor a programme activator."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .canonical import canonical_bytes, sha256_value
from .claim_explanation import _read, explain_run
from .exploration_records import OPERATION, affected_claims, approved_scope, projection
from .evidence_retention import _path
from .frontdoor_intent import IntentRefused, _shape, _text, _texts
from .programme import _id, compile_programme, parse_json
from .programme_strategy import strategy_from_session
from .refusal import AUTHORITY, scrub


@dataclass(frozen=True)
class RetainedRun:
    """Host configuration, never decoded from an owner/model request.

    Catalogue metadata locates historical evidence; it does not authenticate admission,
    the run's issuer, completion or qualification. Paths never appear in exported records.
    """

    programme_input: dict
    item_id: str
    run_id: int
    attempt: int
    pr: int
    artifacts: Path
    policy: Path
    head_sha: str
    base_sha: str


class Reconsideration:
    def __init__(self, exploration, catalogue: dict[str, RetainedRun]):
        self.exploration = exploration
        self.catalogue = deepcopy(catalogue)
        for key, source in self.catalogue.items():
            _id(key)
            if not isinstance(source, RetainedRun) or any(
                    type(value) is not int or value <= 0 for value in (source.run_id, source.attempt, source.pr)):
                raise IntentRefused("retained run requires positive run and attempt identities")

    def preview(self, project, session_id, source_id, *, principal):
        """Read locally retained evidence after owner authorization, without recording an event."""
        engine = self.exploration
        engine.records._owner(principal)
        engine.check_stop()
        _id(source_id)
        source = self.catalogue.get(source_id)
        if source is None:
            raise IntentRefused("unknown host-configured retained run")
        _path(source.artifacts, "root-check")
        store = engine.records.store
        with store._locked(project) as path:
            events = store._read(path)
        state, approval = projection(events), approved_scope(store, events)
        engine._session(state, approval, session_id, allow_stale=True)
        programme = compile_programme(source.programme_input, repository=store.repository)
        if programme.strategy is None or programme.spec != approval["spec"]:
            raise IntentRefused("feedback requires a strategy programme for current approved scope")
        if source.item_id not in {item["id"] for item in programme.items}:
            raise IntentRefused("retained run item is not a programme member")
        strategy = programme.strategy
        if strategy["source"]["session_id"] != session_id:
            raise IntentRefused("retained strategy belongs to another session")
        # Reconstruct the historical handoff, not today's possibly invalidated claims.
        matched = None
        for index, event in enumerate(events):
            command = event["command"]
            if command["operation"] != OPERATION:
                continue
            payload = command["payload"]
            if (payload["session_id"] != session_id or payload["kind"] != "handoff"
                    or payload["data"]["recommendation_sha256"] != strategy["source"]["recommendation_sha256"]):
                continue
            historical = projection(events[:index + 1])
            session = historical["sessions"][session_id]
            candidate = compile_programme({**deepcopy(payload["data"]["input"]), "version": "1.1",
                "strategy": strategy_from_session(historical, session)}, repository=store.repository)
            if candidate.sha256 == programme.sha256:
                matched = payload["data"]
                break
        if matched is None:
            raise IntentRefused("retained programme does not match a recorded exploration handoff")
        report = explain_run(artifact_root=source.artifacts, policy_path=source.policy,
                             expected_head_sha=source.head_sha, expected_base_sha=source.base_sha)
        refusal = retained_refusal(source)
        engine.check_stop()
        observation = {"source_id": source_id, "repository": store.repository,
            "run_id": source.run_id, "attempt": source.attempt, "pr": source.pr, "item_id": source.item_id,
            "programme_sha256": programme.sha256, "handoff_sha256": sha256_value(matched),
            "strategy_source": deepcopy(strategy["source"]), "candidate_id": strategy["candidate"]["id"],
            "causal_claim_ids": sorted(claim["id"] for claim in strategy["claims"]),
            "evidence": report, "refusal": refusal,
            "provenance": "host-catalogued-retained-files-unverified-issuer",
            "programme_admission": "not-assessed", "factory_completion": "not-assessed",
            "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
        observation = parse_json(canonical_bytes(observation).decode("utf-8"))
        return {"project_version": state["project_version"], "observation": observation,
                "observation_sha256": sha256_value(observation)}

    def review(self, project, command, *, principal):
        """An explicit owner attribution can invalidate assumptions, never certify code.

        Read/hash outside the project lock; CAS refuses intervening project changes. Replays
        recheck retained bytes and current approval but never apply the judgment a second time.
        """
        engine = self.exploration
        request = deepcopy(command["request"])
        _shape(request, {"source_id", "observation_sha256", "classification", "claim_ids",
                         "evidence_claim_ids", "rationale", "alternatives_considered", "supersedes"})
        for name in ("rationale", "alternatives_considered"):
            _text(request[name], 4000)
        claims, evidence = _texts(request["claim_ids"]), _texts(request["evidence_claim_ids"])
        if len(set(claims)) != len(claims) or len(set(evidence)) != len(evidence):
            raise IntentRefused("feedback references must be unique")
        if request["classification"] not in {"implementation-failure", "strategy-failure", "inconclusive"}:
            raise IntentRefused("unknown owner attribution")
        if bool(claims) != (request["classification"] == "strategy-failure"):
            raise IntentRefused("only strategy failure names assumptions to invalidate")
        preview = self.preview(project, command["session_id"], request["source_id"], principal=principal)
        if request["observation_sha256"] != preview["observation_sha256"]:
            raise IntentRefused("retained evidence changed since review preview")
        observation = preview["observation"]
        if not set(claims) <= set(observation["causal_claim_ids"]):
            raise IntentRefused("feedback names claims outside the selected strategy ancestry")
        rows = {row["claim_id"]: row for row in observation["evidence"]["claims"]}
        if not set(evidence) <= rows.keys() | {"validation-refusal"}:
            raise IntentRefused("unknown retained evidence claim")
        if request["classification"] != "inconclusive":
            allowed_gaps = {"current-claim-identity-unavailable", "issuer-provenance-not-authenticated",
                "authority-program-identity-unavailable", "toolchain-identity-unavailable",
                "live-world-replay-not-assessed"}
            refusal = observation["refusal"]
            if ("validation-refusal" not in evidence or refusal["status"] != "intact"
                    or refusal["record"]["reason_code"] in {
                        "unknown", "identity", "identity_expired", "stale_base", "trust_root_currency"}
                    or any(row["status"] == "stale"
                    for row in observation["evidence"]["dependency_comparisons"])
                    or any(rows[key]["changed_dependencies"] or set(rows[key]["gaps"]) - allowed_gaps
                           or not rows[key]["artifact_refs"]
                           or any(ref["status"] != "intact" for ref in rows[key]["artifact_refs"])
                           for key in evidence if key != "validation-refusal")):
                raise IntentRefused("missing, stale or inconsistent evidence supports only inconclusive review")

        def transition(state, approval):
            engine._session(state, approval, command["session_id"], allow_stale=True)
            if len(state["feedback"]) >= 80:
                raise IntentRefused("project feedback capacity reached")
            identity = {key: observation[key] for key in
                        ("repository", "run_id", "attempt", "programme_sha256", "item_id")}
            previous = [row for row in state["feedback"].values() if row["run_identity"] == identity]
            if previous:
                if (request["supersedes"] != previous[-1]["id"]
                        or previous[-1]["judgment"]["classification"] != "inconclusive"):
                    raise IntentRefused("run attempt already reviewed; only explicit inconclusive follow-up is allowed")
            elif request["supersedes"] is not None:
                raise IntentRefused("feedback supersession must name this run's previous review")
            if any(state["claims"][key]["status"] == "invalidated" for key in claims):
                raise IntentRefused("invalidated assumptions cannot be invalidated again")
            affected = affected_claims(state["claims"], set(claims))
            frontier = [{"session_id": key, "recommendation_sha256": sha256_value(session["recommendations"][-1]),
                         "claim_ids": sorted(affected.intersection(session["recommendations"][-1]["claim_ids"]))}
                        for key, session in sorted(state["sessions"].items()) if session["recommendations"]
                        and affected.intersection(session["recommendations"][-1]["claim_ids"])]
            data = {"run_identity": identity, "observation": observation,
                    "observation_sha256": preview["observation_sha256"],
                    "judgment": {**request, "authority": "owner-causal-assessment",
                                 "independent_strategy_rejection": "not-established"},
                    "affected_claim_ids": sorted(affected), "frontier": frontier,
                    "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
            return "factory-feedback", {**data, "id": sha256_value(data)}

        return engine._append(project, principal, command, "review-factory-feedback", transition)[0]

    def reopen(self, project, command, *, principal):
        """Reopen one affected question against current context, preserving all shared spend."""
        engine = self.exploration
        request = deepcopy(command["request"])
        _shape(request, {"feedback_sha256", "reason"})
        _text(request["reason"], 4000)

        def transition(state, approval):
            session = engine._session(state, approval, command["session_id"], allow_stale=True)
            feedback = state["feedback"].get(request["feedback_sha256"])
            if (feedback is None or not session["recommendations"] or
                    not any(row["session_id"] == session["id"] and
                            row["recommendation_sha256"] == sha256_value(session["recommendations"][-1])
                            for row in feedback["frontier"])):
                raise IntentRefused("feedback does not affect the current recommendation")
            if session["status"] != "reconsideration-required":
                raise IntentRefused("feedback reconsideration already opened or superseded")
            return "reopened", {**request, "context": engine._reopen_context(session)}

        return engine._append(project, principal, command, "reopen-from-feedback", transition)[0]


def retained_refusal(source: RetainedRun) -> dict:
    """Read the existing scrubbed runtime artifact; envelope integrity is not issuer identity."""
    raw, digest, gap = _read(source.artifacts, "validation-refusal.json")
    result = {"path": "validation-refusal.json", "sha256": digest, "status": gap or "invalid-record",
              "record": None, "issuer_authentication": "not-established"}
    fields = {"version", "pr", "head", "base", "stage", "stage_context", "reason_code", "authority",
              "tool", "phase", "rc", "exception", "detail", "timestamp"}
    if raw is None or set(raw) != fields:
        return result
    if (raw["version"] != "1.0" or type(raw["pr"]) is not int or raw["pr"] != source.pr
            or raw["head"] != source.head_sha or raw["base"] != source.base_sha
            or not isinstance(raw["reason_code"], str) or raw["reason_code"] not in AUTHORITY
            or raw["authority"] != AUTHORITY[raw["reason_code"]]
            or (raw["rc"] is not None and type(raw["rc"]) is not int)
            or any(not isinstance(raw[name], str) or len(raw[name]) > 2000
                   for name in fields - {"pr", "rc"})):
        return result
    return {**result, "status": "intact", "record": {**raw, "detail": scrub(raw["detail"])}}
