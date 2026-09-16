"""Preregistered, operational definitions of falsifiable strategy assumptions."""
from copy import deepcopy

from .frontdoor_intent import IntentRefused, _shape
from .programme import _id

KIND = "new-architecture-dependency-v1"


def validate_rules(rules, claims):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 8:
        raise IntentRefused("register 1–8 bounded strategy rejection rules")
    seen = set()
    for rule in rules:
        _shape(rule, {"id", "kind", "claim_id", "from_layer", "to_layer"})
        for field in ("id", "claim_id", "from_layer", "to_layer"):
            _id(rule[field])
        if (rule["id"] in seen or rule["kind"] != KIND or rule["claim_id"] not in claims
                or claims[rule["claim_id"]]["kind"] != "assumption"
                or claims[rule["claim_id"]].get("status", "active") != "active"
                or rule["from_layer"] == rule["to_layer"]):
            raise IntentRefused("strategy rule requires a distinct, active assumption and supported predicate")
        seen.add(rule["id"])
    return deepcopy(rules)


def register(engine, project, command, *, principal):
    """Freeze the operational meaning before any recommendation or factory outcome exists."""
    request = deepcopy(command["request"])
    _shape(request, {"rules"})

    def transition(state, approval):
        session = engine._session(state, approval, command["session_id"])
        if (session["status"] != "exploring" or session["recommendations"]
                or session["handoffs"] or session["rejection_rules"]):
            raise IntentRefused("rejection rules must be registered once, before selection")
        from .programme_strategy import closure
        roots = {claim for candidate in session["candidates"].values() for claim in candidate["claim_ids"]}
        selected = closure(state["claims"], roots)
        rules = validate_rules(request["rules"], {key: state["claims"][key] for key in selected})
        return "strategy-rules-registered", {"rules": rules, "context_identity": session["context"]["identity"]}

    return engine._append(project, principal, command, "register-strategy-rules", transition)[0]
