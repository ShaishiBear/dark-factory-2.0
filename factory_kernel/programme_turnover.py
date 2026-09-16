"""Observe replacement obligations without granting a transition capability.

Two matching reads detect changes, not mutual exclusion. Even an empty frontier
requires a persistent execution fence and serialized activation before replacement.
"""
from copy import deepcopy

from .canonical import sha256_value
from .exploration_records import approved_scope, projection
from .frontdoor_intent import IntentRefused
from .programme import compile_programme
from .programme_replan import review_replan
from .programme_runtime import ProgrammeQueue
from .publication_observation import _complete
from .publication_source import observe_publication_source

ACTIVE_RUN_STATES = ("queued", "in_progress", "waiting", "requested", "pending")
WORKER = "dark-factory-worker.yml"


def _execution(github, programme):
    queue = ProgrammeQueue(github, "main")
    inventory = queue.inventory(programme)
    items = []
    for item in programme.items:
        row = inventory.get(item["id"])
        receipt = queue.completion_receipt(programme, item, row) if row else None
        items.append({"item": deepcopy(item), "issue": row["number"] if row else None,
                      "issue_state": row["state"] if row else "not-materialized",
                      "issue_sha256": sha256_value(row) if row else None,
                      "completion": receipt})
    runs = {}
    prefix = f"repos/{github.repository}"
    for status in ACTIVE_RUN_STATES:
        rows = _complete(github.json(["api", f"{prefix}/actions/workflows/{WORKER}/runs"
                                      f"?status={status}&per_page=100"]), "workflow_runs")
        for row in rows:
            if (type(row.get("id")) is not int or row["id"] <= 0
                    or type(row.get("run_attempt")) is not int or row["run_attempt"] < 1
                    or row.get("path") != ".github/workflows/" + WORKER
                    or row.get("status") != status or row["id"] in runs):
                raise IntentRefused("replacement worker inventory is ambiguous or changed")
            runs[row["id"]] = {key: row[key] for key in ("id", "run_attempt", "status", "head_sha")}
    # One full page is only known complete when shorter than its requested bound.
    pulls = github.json(["api", f"{prefix}/pulls?state=open&per_page=100"])
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise IntentRefused("replacement pull request inventory is incomplete")
    open_pulls = []
    for row in pulls:
        if row.get("state") != "open" or type(row.get("number")) is not int or row["number"] <= 0:
            raise IntentRefused("replacement pull request inventory is malformed")
        if row.get("user", {}).get("login") == programme.app_login:
            open_pulls.append({"number": row["number"], "head_sha": row["head"]["sha"],
                               "observation_sha256": sha256_value(row)})
    if len({row["number"] for row in open_pulls}) != len(open_pulls):
        raise IntentRefused("replacement pull request inventory is duplicated")
    return {"items": items, "active_runs": [runs[key] for key in sorted(runs)],
            "open_app_pulls": sorted(open_pulls, key=lambda row: row["number"])}


def observe_turnover(github, proposed_input, *, source=observe_publication_source):
    observation = source(github)
    if observation["active_input"] is None:
        raise IntentRefused("replacement review requires an existing active programme")
    current = compile_programme(observation["active_input"], repository=github.repository)
    proposed = compile_programme(proposed_input, repository=github.repository)
    replan = review_replan(current.to_input(), proposed_input, repository=github.repository,
                          source_sha=observation["main_sha"])
    if replan["disposition"] == "unchanged":
        raise IntentRefused("replacement proposal does not change the programme")
    execution = _execution(github, current)
    new_items = {item["id"]: item for item in proposed.items}
    preserved, pending, blockers = [], [], []
    for row in execution["items"]:
        key = row["item"]["id"]
        if row["completion"] is not None:
            # A renamed item or changed blockers is not the same completed obligation.
            if new_items.get(key) != row["item"]:
                blockers.append({"kind": "completed-work-changed", "item_id": key})
            preserved.append({"programme_sha256": current.sha256, **deepcopy(row)})
        else:
            pending.append(deepcopy(row))
            if row["issue_state"] == "open":
                blockers.append({"kind": "open-pending-work", "item_id": key, "issue": row["issue"]})
    blockers.extend({"kind": "active-worker", "run_id": row["id"]} for row in execution["active_runs"])
    blockers.extend({"kind": "open-app-pull", "pr": row["number"]} for row in execution["open_app_pulls"])
    # Do not present matching observations as a lock or consume them as execution authority.
    if execution != _execution(github, current) or observation != source(github):
        raise IntentRefused("programme execution changed during replacement review")
    return {"schema": "dark-factory/programme-turnover-review", "schema_version": "1.0",
            "source": observation, "replanning": replan, "execution": execution,
            "preserved_completed_work": preserved, "pending_work": pending,
            "blockers": blockers, "authority": "review-only",
            "activation": "requires-persistent-fence-and-serialized-transition",
            "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
            "execution_budget": {"status": "requires-cumulative-ledger",
                                 "refund_allowed": False, "reset_allowed": False}}


def review_turnover(publications, github, project, request, *, principal):
    """Resolve approved content and budget history from the canonical owner store."""
    publications._owner(principal)
    store = publications.store
    if github.repository != store.repository:
        raise IntentRefused("replacement repository differs from approved scope")
    review = publications.review(project, request, principal=principal)
    with store._locked(project) as path:
        events = store._read(path)
        approval = approved_scope(store, events)
        if (len(events) != review["project_version"] or approval["spec"] != review["input"]["spec"]):
            raise IntentRefused("replacement review no longer names current approved intent")
        head = sha256_value(events[-1])
        state = projection(events)
        budget = deepcopy(state["budgets"].get(approval["spec_sha256"]))
        reservations = [{"session_id": key, "reservations": [
                            {**{field: reservation[field] for field in
                                ("id", "calls", "usd", "probe_units", "status", "round", "context_identity")},
                             "reservation_sha256": sha256_value(reservation)}
                            for reservation in row["reservations"].values()]}
                        for key, row in state["sessions"].items()
                        if row["binding"]["spec_sha256"] == approval["spec_sha256"]]
    result = observe_turnover(github, review["input"])
    # A fresh regeneration also rechecks live strategy evidence and current recommendation.
    fresh = publications.review(project, request, principal=principal)
    if fresh != review:
        raise IntentRefused("replacement recommendation changed during review")
    with store._locked(project) as path:
        after = store._read(path)
        if len(after) != len(events) or sha256_value(after[-1]) != head:
            raise IntentRefused("owner decisions or spend changed during replacement review")
    result.update(project=project, project_version=len(events), intent_head_sha256=head,
                  input_sha256=review["input_sha256"], programme_sha256=review["programme_sha256"],
                  exploration_budget={"spec_sha256": approval["spec_sha256"], "budget": budget,
                                      "reservations": reservations, "reset_allowed": False})
    return {**result, "review_sha256": sha256_value(result)}
