"""Read publication prerequisites from protected GitHub state, never request JSON."""
from __future__ import annotations

from .canonical import sha256_value
from .frontdoor_control import stop_status
from .frontdoor_intent import IntentRefused
from .programme_runtime import ProgrammeQueue


def observe_publication_source(github):
    repository = github.repository
    metadata = github.json(["api", f"repos/{repository}"])
    if (metadata.get("full_name") != repository or type(metadata.get("private")) is not bool
            or metadata.get("default_branch") != "main"):
        raise IntentRefused("publication repository identity, visibility or default branch is unknown")
    branch = github.json(["api", f"repos/{repository}/branches/main"])
    if branch.get("protected") is not True:
        raise IntentRefused("publication requires protected main")
    programme = ProgrammeQueue(github, "main").current()
    active = None if programme is None else {
        "version": "1.0", "spec": programme.spec, "app_login": programme.app_login,
        "proposal": {"spec_sha256": sha256_value(programme.spec), "items": list(programme.items)},
    }
    stop = stop_status(github)
    latest_branch = github.json(["api", f"repos/{repository}/branches/main"])
    latest_metadata = github.json(["api", f"repos/{repository}"])
    if (latest_branch.get("protected") is not True
            or latest_branch["commit"]["sha"] != branch["commit"]["sha"]
            or any(latest_metadata.get(key) != metadata[key] for key in ("full_name", "private", "default_branch"))):
        raise IntentRefused("publication source changed during observation")
    return {"repository": repository, "visibility": "private" if metadata["private"] else "public",
            "main_sha": branch["commit"]["sha"], "protected": True, "active_input": active, "stop": stop}
