"""Owner-visible explanation of the canonical intent log; no second event store or authority."""
from __future__ import annotations

from copy import deepcopy

from .canonical import sha256_value
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .exploration_records import OPERATION, projection
from .execution_budget import OPERATION as BUDGET_OPERATION, projection as budget_projection
from .replacement_intent import OPERATION as REPLACEMENT_OPERATION, plans


def explain_history(store: IntentStore, project: str, *, principal: Principal) -> dict:
    """Read under the existing store lock and authorization, preserving event/version identity.

    The principal must come from the authenticated transport, never request JSON. The service
    directory remains the provenance boundary: hashes detect drift, not a malicious store owner.
    This intentionally exposes private wording only to the configured owner.
    """
    store._authorize(principal)
    if principal.role != "owner":
        raise IntentRefused("decision history is owner-only")
    with store._locked(project) as path:
        events = store._read(path)
    rows, seen, drafts, approved = [], set(), {}, set()
    source_intent = None
    latest_approval = None
    for event in events:
        command = event["command"]
        key = command["idempotency_key"]
        if key in seen or command.get("expected_project_version") != event["project_version"] - 1:
            raise IntentRefused("duplicate/replayed or out-of-order decision event")
        seen.add(key)
        operation, payload = command["operation"], event["command"]["payload"]
        version = event["project_version"]
        actor = event["actor"]
        if (not isinstance(actor, dict) or not actor.get("identity")
                or actor.get("role") not in {"owner", "proposal"}
                or (actor["role"] == "owner" and actor["identity"] != store.owner)):
            raise IntentRefused("invalid decision principal")
        if operation not in {"record-intent", "add-exploration", "propose-spec", "approve-spec", OPERATION, BUDGET_OPERATION, REPLACEMENT_OPERATION}:
            raise IntentRefused("unsupported decision event")
        if operation != "propose-spec" and actor != {"identity": store.owner, "role": "owner"}:
            raise IntentRefused("decision event does not name the configured owner")
        digest = sha256_value(event)
        row = {"event_id": "intent_" + digest, "event_sha256": digest,
               "project_version": version, "previous_event_sha256": event["previous"],
               "operation": operation, "actor": deepcopy(actor), "created_at": event["created_at"],
               "record": deepcopy(payload), "basis": [], "authority": "project-history-only",
               "proof_status": "not-established"}
        if operation == "record-intent":
            source_intent = row["event_id"]
        elif operation == "propose-spec":
            row["basis"] = [source_intent] if source_intent else []
            row["spec_sha256"] = sha256_value(payload["spec"])
            drafts[version] = row
        elif operation == "approve-spec":
            draft = drafts.get(payload["draft_version"])
            if (draft is None or draft["spec_sha256"] != payload["spec_sha256"]
                    or not draft["basis"] or draft["basis"] != [source_intent]
                    or draft is not next(reversed(drafts.values()))
                    or payload["draft_version"] in approved or draft["record"]["open_questions"]):
                raise IntentRefused("approval does not reference the current recorded proposal")
            approved.add(payload["draft_version"])
            row["basis"] = [draft["event_id"]]
            row["spec_sha256"] = payload["spec_sha256"]
            row["supersedes"] = latest_approval
            latest_approval = row["event_id"]
        elif operation in {OPERATION, BUDGET_OPERATION, REPLACEMENT_OPERATION}:
            row["basis"] = [latest_approval] if latest_approval else []
        rows.append(row)
    return {"schema": "dark-factory/decision-history", "schema_version": "1.0",
            "repository": store.repository, "project": project, "project_version": len(events),
            "authority": "project-history-only", "source": "IntentStore",
            "source_head_sha256": sha256_value(events[-1]) if events else None,
            "events": rows, "latest_recorded_approval": latest_approval,
            "exploration": projection(events),
            "execution_budget": budget_projection(events),
            "replacement_intents": plans(events),
            "execution_status": "not-activated-by-history",
            "gaps": ["programme-admission-not-assessed", "qualification-not-assessed",
                     "exploratory-observations-do-not-establish-qualification"]}
