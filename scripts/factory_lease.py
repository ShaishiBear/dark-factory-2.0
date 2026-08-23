#!/usr/bin/env python3
"""Heartbeat and recover Dark Factory `factory:in-progress` claims."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

MARKER = "dark-factory-lease:v1"
MARKER_RE = re.compile(r"<!--\s*dark-factory-lease:v1\s+(\{.*?\})\s*-->")
LINK_RE = re.compile(r"(?im)^\s*(?:fixes|closes|resolves)\s+#(\d+)\b")
HANDOFF = {"factory:needs-review", "factory:needs-fix"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def render(record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return (
        f"<!-- {MARKER} {payload} -->\n"
        f"**Dark Factory lease:** `{record['state']}` · stage `{record['stage']}` · "
        f"heartbeat `{record['heartbeat_at']}`"
    )


def parse_lease(body: str) -> dict | None:
    match = MARKER_RE.search(body or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
        required = {"lease_id", "workflow_id", "heartbeat_at", "stage", "state"}
        if not isinstance(value, dict) or not required.issubset(value):
            return None
        parse_time(str(value["heartbeat_at"]))
        return value
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def decide_reap(
    now: datetime,
    accepted: bool,
    updated: datetime,
    lease: dict | None,
    marker_seen: bool,
    handoff: str,
    active_ttl: int,
    legacy_ttl: int,
) -> tuple[str, str]:
    if not accepted:
        return "protect", "in-progress issue is not factory:accepted"
    if handoff == "unlabeled":
        return "protect", "linked open PR lacks factory handoff label"
    if marker_seen and lease is None:
        return "protect", "lease marker is malformed"
    if lease is None:
        if (now - updated).total_seconds() <= legacy_ttl:
            return "keep", "legacy claim still inside grace period"
        return "reap", "legacy in-progress claim exceeded grace period"
    if lease["state"] != "active":
        if lease["state"] == "finished" and handoff == "ready":
            return "reap", "finished workflow left claim behind after PR handoff"
        return "protect", f"unexpected lease state {lease['state']!r}"
    if (now - parse_time(str(lease["heartbeat_at"]))).total_seconds() <= active_ttl:
        return "keep", "active lease heartbeat is fresh"
    if handoff == "ready":
        return "reap", "stale builder lease; linked PR already owns handoff"
    return "reap", "active lease heartbeat expired before PR handoff"


def run_gh(args: list[str], json_out: bool = True):
    proc = subprocess.run(
        ["gh", *args], capture_output=json_out, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        detail = (proc.stderr or "")[-1200:] if json_out else f"rc={proc.returncode}"
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    if not json_out:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh {' '.join(args)} returned invalid JSON") from exc


def repo_name() -> str:
    return str(run_gh(["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"])


def save(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or "comment_id" not in value:
            raise ValueError
        return value
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"lease file missing or invalid: {path}") from exc


def start(issue: int, stage: str, path: Path, workflow_id: str) -> None:
    repo = repo_name()
    record = {
        "lease_id": str(uuid.uuid4()), "workflow_id": workflow_id or "unknown",
        "heartbeat_at": iso(now_utc()), "stage": stage, "state": "active", "pr": None,
    }
    created = run_gh([
        "api", "--method", "POST", f"repos/{repo}/issues/{issue}/comments",
        "-f", f"body={render(record)}",
    ])
    record["comment_id"] = int(created["id"])
    save(path, record)
    print(f"LEASE_STARTED issue=#{issue} stage={stage}", flush=True)


def update(issue: int, stage: str, path: Path, state: str, pr: int | None) -> None:
    repo = repo_name()
    local = load(path)
    record = {k: v for k, v in local.items() if k != "comment_id"}
    record.update({"heartbeat_at": iso(now_utc()), "stage": stage, "state": state})
    if pr is not None:
        record["pr"] = pr
    run_gh([
        "api", "--method", "PATCH",
        f"repos/{repo}/issues/comments/{int(local['comment_id'])}",
        "-f", f"body={render(record)}",
    ])
    record["comment_id"] = int(local["comment_id"])
    save(path, record)
    print(f"LEASE_{state.upper()} issue=#{issue} stage={stage}", flush=True)


def pr_handoff(issue: int, prs: list[dict]) -> str:
    linked = [
        pr for pr in prs
        if issue in {int(number) for number in LINK_RE.findall(str(pr.get("body") or ""))}
    ]
    if not linked:
        return "none"
    for pr in linked:
        labels = {str(label.get("name")) for label in pr.get("labels", [])}
        if labels & HANDOFF:
            return "ready"
    return "unlabeled"


def latest_lease(comments: list[dict]) -> tuple[dict | None, bool, int | None]:
    found: list[tuple[datetime, dict, int]] = []
    marker_seen = False
    for comment in comments:
        body = str(comment.get("body") or "")
        if MARKER not in body:
            continue
        marker_seen = True
        lease = parse_lease(body)
        if lease is not None:
            found.append((parse_time(str(lease["heartbeat_at"])), lease, int(comment["id"])))
    if not found:
        return None, marker_seen, None
    _, lease, comment_id = max(found, key=lambda item: item[0])
    return lease, marker_seen, comment_id


def reap(active_ttl: int, legacy_ttl: int) -> int:
    repo = repo_name()
    issues = run_gh([
        "issue", "list", "--state", "open", "--label", "factory:in-progress",
        "--limit", "1000", "--json", "number,updatedAt,labels",
    ])
    prs = run_gh([
        "pr", "list", "--state", "open", "--limit", "1000",
        "--json", "number,body,labels,url",
    ])
    current = now_utc()
    reaped = protected = 0
    for issue in issues:
        number = int(issue["number"])
        labels = {str(label.get("name")) for label in issue.get("labels", [])}
        comments = run_gh(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"])
        lease, marker_seen, comment_id = latest_lease(comments)
        handoff = pr_handoff(number, prs)
        action, reason = decide_reap(
            current, "factory:accepted" in labels, parse_time(str(issue["updatedAt"])),
            lease, marker_seen, handoff, active_ttl, legacy_ttl,
        )
        if action != "reap":
            protected += action == "protect"
            print(f"STALL_{action.upper()} issue=#{number} reason={reason!r} handoff={handoff}")
            continue
        if lease is not None and comment_id is not None:
            lease = dict(lease)
            lease.update({"heartbeat_at": iso(current), "stage": "reaped", "state": "reaped"})
            run_gh([
                "api", "--method", "PATCH", f"repos/{repo}/issues/comments/{comment_id}",
                "-f", f"body={render(lease)}",
            ])
        run_gh(["issue", "edit", str(number), "--remove-label", "factory:in-progress"], False)
        next_step = "PR validation has priority" if handoff == "ready" else "issue is eligible for redispatch"
        run_gh([
            "api", "--method", "POST", f"repos/{repo}/issues/{number}/comments",
            "-f", f"body=**Dark Factory recovery:** released stale `factory:in-progress` claim. "
                  f"{reason}; {next_step}.",
        ])
        reaped += 1
        print(f"STALL_REAPED issue=#{number} reason={reason!r} handoff={handoff}")
    print(f"STALL_REAPER scanned={len(issues)} reaped={reaped} protected={protected}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "touch", "finish"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--issue", type=int, required=True)
        cmd.add_argument("--stage", required=True)
        cmd.add_argument("--lease-file", type=Path, required=True)
        if name == "start":
            cmd.add_argument("--workflow-id", default=os.environ.get("WORKFLOW_ID", "unknown"))
        else:
            cmd.add_argument("--pr", type=int)
    cmd = sub.add_parser("reap")
    cmd.add_argument("--active-ttl", type=int, default=21600)
    cmd.add_argument("--legacy-ttl", type=int, default=86400)
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args.issue, args.stage, args.lease_file, args.workflow_id)
        elif args.command in ("touch", "finish"):
            update(args.issue, args.stage, args.lease_file,
                   "finished" if args.command == "finish" else "active", args.pr)
        else:
            return reap(args.active_ttl, args.legacy_ttl)
        return 0
    except (RuntimeError, KeyError, TypeError, ValueError) as exc:
        print(f"LEASE_ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
