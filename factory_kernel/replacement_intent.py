"""Freeze exact owner-reviewed replacement facts without authorizing external effects."""
from copy import deepcopy
from datetime import datetime, timezone
import re

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, _shape
from .programme import parse_json
from .programme_turnover import review_turnover

OPERATION = "replacement-intent-event"


def plans(events):
    result = []
    for event in events:
        if event["command"]["operation"] != OPERATION:
            continue
        plan = deepcopy(event["command"]["payload"]["plan"])
        digest = plan.pop("plan_sha256")
        if sha256_value(plan) != digest:
            raise IntentRefused("replacement intent content cannot be verified")
        result.append({**plan, "plan_sha256": digest,
            "recorded_project_version": event["project_version"],
            "currency": "unchanged-owner-history" if event["project_version"] == len(events) else "owner-history-changed",
            "activation_allowed": False})
    return result


class ReplacementIntents:
    def __init__(self, publications, github):
        self.publications, self.github = publications, github
        self.store = publications.store

    def snapshot(self, project, *, principal):
        self.publications._owner(principal)
        with self.store._locked(project) as path:
            return plans(self.store._read(path))

    def freeze(self, project, command, *, principal):
        self.publications._owner(principal)
        command = parse_json(canonical_bytes(command).decode("utf-8"))
        _shape(command, {"idempotency_key", "expected_project_version", "review_sha256", "review"})
        if (not isinstance(command["idempotency_key"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", command["idempotency_key"])
                or type(command["expected_project_version"]) is not int
                or command["expected_project_version"] < 1
                or not isinstance(command["review_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", command["review_sha256"])):
            raise IntentRefused("invalid replacement intent identity or version")
        request_sha = sha256_value(command)
        with self.store._locked(project) as path:
            events = self.store._read(path)
            for event in events:
                if event["command"]["idempotency_key"] == command["idempotency_key"]:
                    if (event["command"]["operation"] != OPERATION
                            or event["command"]["payload"]["request_sha256"] != request_sha):
                        raise IntentRefused("replacement intent identity reused with different content")
                    # Return historical evidence only. No remote effect is retried or authorized.
                    return {"plan": next(row for row in plans(events)
                        if row["request_id"] == command["idempotency_key"]), "replayed": True}
            if len(events) != command["expected_project_version"]:
                raise IntentRefused("stale replacement intent project version")
        review = review_turnover(self.publications, self.github, project, command["review"], principal=principal)
        if (review["review_sha256"] != command["review_sha256"]
                or review["project_version"] != command["expected_project_version"]):
            raise IntentRefused("replacement facts differ from the owner's reviewed plan")
        if any(row["kind"] == "completed-work-changed" for row in review["blockers"]):
            raise IntentRefused("replacement cannot change an independently completed obligation")
        prepared = self.publications.review(project, command["review"], principal=principal)
        if (prepared["input_sha256"] != review["input_sha256"]
                or prepared["programme_sha256"] != review["programme_sha256"]
                or prepared["project_version"] != review["project_version"]):
            raise IntentRefused("replacement recommendation changed before freezing")
        plan = {"schema": "dark-factory/replacement-intent", "schema_version": "1.0",
            "request_id": command["idempotency_key"], "project": project,
            "repository": self.store.repository, "owner": principal.identity,
            "authority": "frozen-plan-only", "qualification_status": "UNPROVEN",
            "proof_reuse_allowed": False, "activation_allowed": False,
            "review": deepcopy(review), "proposed_input": deepcopy(prepared["input"]),
            "required_actions": ["protect-and-observe-execution-fence", "drain-and-reconcile-old-execution",
                "journal-exact-pending-work-retirement", "archive-original-lineage-and-completion-receipts",
                "establish-cumulative-spending-coverage", "fresh-owner-consent-and-protected-replacement",
                "observe-exact-activation-before-fence-release", "fresh-independent-qualification"]}
        plan["plan_sha256"] = sha256_value(plan)
        with self.store._locked(project) as path:
            events = self.store._read(path)
            if (len(events) != review["project_version"]
                    or sha256_value(events[-1]) != review["intent_head_sha256"]):
                raise IntentRefused("owner decisions or spending changed before freezing replacement")
            event = {"schema": "dark-factory/intent-event", "schema_version": "1.0",
                "project": project, "repository": self.store.repository, "project_version": len(events) + 1,
                "command": {"idempotency_key": command["idempotency_key"],
                    "expected_project_version": len(events), "operation": OPERATION,
                    "payload": {"request_sha256": request_sha, "plan": plan}},
                "actor": {"identity": principal.identity, "role": principal.role},
                "created_at": datetime.now(timezone.utc).isoformat(), "previous": sha256_value(events[-1])}
            self.store._write(path, [*events, event])
            return {"plan": plans([*events, event])[-1], "replayed": False}
