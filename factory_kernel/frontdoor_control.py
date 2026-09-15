"""Narrow owner stop adapter. It cannot start work, clear a stop, approve scope or merge.

Only the dedicated protected-main workflow calls request_stop with an App token. The Front
Door asks that workflow to run; the private App key remains in its pinned mint action.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from .github_cli import GitHubClient


class StopRefused(ValueError):
    pass


def stop_status(github: GitHubClient) -> dict:
    rows = github.programme_issues()  # complete bounded inventory, including closed records
    numbers = []
    for row in rows:
        labels = {label["name"] for label in row.get("labels", [])}
        if row.get("state", "").lower() == "open" and "factory:stop" in labels:
            if type(row.get("number")) is not int or row["number"] <= 0:
                raise StopRefused("stop inventory returned an invalid issue identity")
            numbers.append(row["number"])
    return {"state": "stopped" if numbers else "clear", "issues": sorted(numbers)}


def request_stop(github: GitHubClient, *, request_id: str, reason: str) -> dict:
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise StopRefused("stop request ID must be 32 lowercase hex characters")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or "<!--" in reason:
        raise StopRefused("stop reason must be plain nonempty text of at most 2000 characters")
    marker = f"<!-- dark-factory-owner-stop:{request_id} -->"
    body = f"{marker}\n\n{reason}\n"
    rows = github.programme_issues()
    matching = [row for row in rows if marker in str(row.get("body", ""))]
    if len(matching) > 1:
        raise StopRefused("duplicate stop request identities require operator reconciliation")
    if matching:
        row = matching[0]
        if row.get("body") != body or row.get("title") != "Owner requested factory stop":
            raise StopRefused("stop request identity has changed content")
        # A replay of a deliberately cleared stop must not reopen it.
        return {"state": "already-recorded", "issue": row["number"], "request_id": request_id}
    existing = [row["number"] for row in rows if row.get("state", "").lower() == "open"
                and any(label.get("name") == "factory:stop" for label in row.get("labels", []))]
    if existing:
        return {"state": "already-stopped", "issues": sorted(existing), "request_id": request_id}
    payload = {"title": "Owner requested factory stop", "body": body,
               "labels": ["factory:stop"]}
    with tempfile.TemporaryDirectory(prefix="factory-stop-request-") as directory:
        path = Path(directory) / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        # One atomic labelled POST, never create first then label. An uncertain response is
        # not retried here. A later request reconciles through the complete inventory.
        raw = github.run_as_app([
            "api", f"repos/{github.repository}/issues", "--method", "POST", "--input", str(path),
        ], operation="request_emergency_stop")
    result = json.loads(raw)
    if not isinstance(result, dict) or type(result.get("number")) is not int or result["number"] <= 0:
        raise StopRefused("stop POST did not return a valid issue identity; reconcile before retry")
    # The acknowledgement is not proof of observed stop. The UI reads stop_status separately.
    return {"state": "stop-post-acknowledged", "issue": result["number"], "request_id": request_id}


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    if (not owner or os.environ.get("GITHUB_ACTOR") != owner
            or repository != f"{owner}/dark-factory-2.0"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise StopRefused("owner workflow_dispatch on protected main required")
    result = request_stop(GitHubClient(repository, cwd=Path.cwd()),
                          request_id=os.environ.get("STOP_REQUEST_ID", ""),
                          reason=os.environ.get("STOP_REASON", ""))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
