"""Prepare a review artifact from stored owner approval and an untrusted decomposition.

This adapter performs no GitHub effect and grants no activation authority. The existing
protected-main maintenance/admission boundary must still judge and publish the input.
"""
from __future__ import annotations

from copy import deepcopy

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .programme import compile_programme, parse_json


def prepare_programme(
    store: IntentStore, project: str, request: dict, *, principal: Principal, app_login: str,
) -> dict:
    """Use a named approval from the trusted store, never a caller-supplied approved spec.

    The project version fences the observation, not future activation. Delivery must re-read
    the approval before publishing; an exported JSON object is not a long-lived capability.
    """
    store._authorize(principal)
    if principal.role != "owner":
        raise IntentRefused("only the owner can prepare an approved programme for review")
    request = parse_json(canonical_bytes(request).decode("utf-8"))
    if not isinstance(request, dict) or set(request) != {
        "expected_project_version", "approval_version", "spec_sha256", "proposal",
    }:
        raise IntentRefused("programme review request has unknown or missing fields")
    with store._locked(project) as path:
        events = store._read(path)
        state = store._snapshot(events)
        if (type(request["expected_project_version"]) is not int
                or request["expected_project_version"] != state["project_version"]):
            raise IntentRefused("stale project version; reload before programme review")
        if not state["approvals"]:
            raise IntentRefused("explicit owner scope approval is required")
        approval = state["approvals"][-1]
        if (type(request["approval_version"]) is not int
                or request["approval_version"] != approval["project_version"]
                or request["spec_sha256"] != approval["spec_sha256"]):
            raise IntentRefused("programme review must name the latest exact approved scope")
        source = next(row for row in state["ledger"]
                      if row["version"] == approval["source_intent_version"])
        value = {"version": "1.0", "spec": deepcopy(approval["spec"]),
                 "proposal": request["proposal"], "app_login": app_login}
        programme = compile_programme(value, repository=store.repository)
        return {
            "schema": "dark-factory/programme-review", "schema_version": "1.0",
            "project": project, "project_version": state["project_version"],
            "approval": {"version": approval["project_version"],
                         "spec_sha256": approval["spec_sha256"],
                         "actor": deepcopy(approval["actor"]), "wording": approval["wording"],
                         "source_intent_version": source["version"], "original_intent": source["wording"]},
            "input": value, "input_sha256": sha256_value(value), "programme_sha256": programme.sha256,
            "items": deepcopy(list(programme.items)),
            "activation": "requires-protected-main-review",
        }
