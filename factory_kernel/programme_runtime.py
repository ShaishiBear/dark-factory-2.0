"""Kernel admission and GitHub projection for a protected, versioned programme.

Creation is serialised by the existing worker workflow. A lost POST response is reconciled
against a complete issue inventory on the next invocation; duplicate/edited objects refuse.
GitHub provides no atomic idempotency key for issue creation. Concurrent independent runners
are unsupported, and duplicates are detected rather than silently selected.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Mapping

from .programme import ACTIVE_PATH, MARKER, Programme, ProgrammeRefused, compile_programme, parse_json

OUTCOME_MARKER = "<!-- dark-factory-programme-outcome:"


class ProgrammeQueue:
    def __init__(self, github, default_branch: str):
        self.github = github
        self.default_branch = default_branch

    def current(self) -> Programme | None:
        """Read approved input from GitHub's current protected branch, never candidate disk."""
        repo = self.github.repository
        branch = self.github.json(["api", f"repos/{repo}/branches/{self.default_branch}"])
        if branch.get("protected") is not True:
            raise ProgrammeRefused("programme admission requires a protected default branch")
        sha = branch["commit"]["sha"]
        tree = self.github.json(["api", f"repos/{repo}/git/trees/{sha}?recursive=1"])
        if tree.get("truncated") is not False:
            raise ProgrammeRefused("cannot admit from a truncated repository tree")
        entries = [x for x in tree["tree"] if x["path"] == ACTIVE_PATH]
        if not entries:
            return None
        if len(entries) != 1 or entries[0].get("mode") != "100644":
            raise ProgrammeRefused("programme input must be one regular protected file")
        blob = self.github.json(["api", f"repos/{repo}/git/blobs/{entries[0]['sha']}"])
        if blob.get("encoding") != "base64" or blob.get("size", 250001) > 250000:
            raise ProgrammeRefused("invalid programme source blob")
        raw = parse_json(base64.b64decode(blob["content"]).decode("utf-8"))
        return compile_programme(raw, repository=repo)

    def inventory(self, programme: Programme) -> dict[str, dict]:
        matches: dict[str, dict] = {}
        prefix = f"{MARKER}{programme.sha256}:"
        items = {item["id"]: item for item in programme.items}
        for row in self.github.programme_issues():
            body = str(row.get("body") or "")
            if (prefix in body and row.get("user", {}).get("type") == "Bot"
                    and row.get("user", {}).get("login") != programme.app_login):
                raise ProgrammeRefused("programme was published by an unexpected bot; check App configuration")
            # Anyone may copy a visible marker into another issue. It must neither acquire
            # membership nor stop the legitimate queue. GitHub owns the immutable creator.
            if (row.get("user", {}).get("login") != programme.app_login
                    or row.get("user", {}).get("type") != "Bot"):
                continue
            if row.get("user", {}).get("login") == programme.app_login and MARKER not in body:
                raise ProgrammeRefused("App-created issue lost its programme binding")
            if (row.get("user", {}).get("login") == programme.app_login
                    and MARKER in body and prefix not in body and row.get("state") == "open"):
                raise ProgrammeRefused("previous programme still has open work; reconcile before replacing it")
            if prefix not in body:
                continue
            found = [key for key, item in items.items() if programme.marker(item) in body]
            if len(found) != 1 or found[0] in matches:
                raise ProgrammeRefused("duplicate or unknown programme issue identity")
            matches[found[0]] = row
        numbers = {key: row["number"] for key, row in matches.items()}
        for key, row in matches.items():
            if not set(items[key]["blocked_by"]) <= numbers.keys():
                raise ProgrammeRefused("materialized item is missing its blocker objects")
            title, body = programme.render(items[key], numbers)
            if (row.get("title") != title or row.get("body") != body
                    or row.get("user", {}).get("login") != programme.app_login
                    or row.get("user", {}).get("type") != "Bot"):
                raise ProgrammeRefused(f"programme issue #{row['number']} was edited or forged")
        return matches

    def completed(self, programme: Programme, item: dict, row: dict) -> bool:
        """Issue closure is not proof. Require the kernel's post-merge receipt and run success.

        Receipt writers are protected workflow code using Actions' ordinary observation/control
        identity. The receipt binds the programme, issue, merged PR and canonical worker run.
        It is retained on the issue; seven-day diagnostic artifacts are not completion authority.
        """
        if row.get("state") != "closed":
            return False
        repo = self.github.repository
        number = row["number"]
        for page in range(1, 11):
            comments = self.github.json([
                "api", f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}"
            ])
            if not isinstance(comments, list):
                raise ProgrammeRefused("invalid outcome comment inventory")
            for comment in comments:
                if comment.get("user", {}).get("login") != "github-actions[bot]":
                    continue
                if comment.get("user", {}).get("type") != "Bot":
                    continue
                if (not comment.get("created_at")
                        or comment.get("updated_at") != comment["created_at"]):
                    continue  # editing a bot comment must not preserve its authority
                body = str(comment.get("body") or "")
                if not body.startswith(OUTCOME_MARKER) or not body.endswith(" -->"):
                    continue
                try:
                    receipt = parse_json(body[len(OUTCOME_MARKER):-4])
                except ValueError:
                    continue
                if not isinstance(receipt, dict) or (
                    receipt.get("version") != "1.0" or receipt.get("programme") != programme.sha256
                    or receipt.get("item") != item["id"] or receipt.get("issue") != number
                ):
                    continue
                if type(receipt.get("pr")) is not int or type(receipt.get("run")) is not int:
                    continue
                pr = self.github.json(["api", f"repos/{repo}/pulls/{receipt['pr']}"])
                run = self.github.json(["api", f"repos/{repo}/actions/runs/{receipt['run']}"])
                if (pr.get("merged") is True and pr.get("merge_commit_sha") == receipt.get("merge_sha")
                        and pr.get("head", {}).get("sha") == receipt.get("head_sha")
                        and pr.get("base", {}).get("ref") == self.default_branch
                        and pr.get("user", {}).get("login") == programme.app_login
                        and pr.get("merged_by", {}).get("login") == programme.app_login
                        and pr.get("merged_by", {}).get("type") == "Bot"
                        and pr.get("base", {}).get("repo", {}).get("full_name") == repo
                        and pr.get("head", {}).get("repo", {}).get("full_name") == repo
                        and re.search(rf"(?im)^\s*Fixes\s+#{number}\b", str(pr.get("body") or ""))
                        and run.get("conclusion") == "success"
                        and run.get("path") == ".github/workflows/dark-factory-worker.yml"
                        and run.get("head_branch") == self.default_branch
                        and run.get("event") in {"schedule", "workflow_dispatch"}
                        and run.get("run_attempt") == receipt.get("run_attempt")):
                    return True
            if len(comments) < 100:
                return False
        raise ProgrammeRefused("outcome comments exceed bounded inventory")

    def sync(self, check_stop) -> dict:
        """Create at most one ready candidate per invocation; never relabel/reopen rejected work."""
        check_stop()
        programme = self.current()
        if programme is None:
            return {"status": "no-programme", "created": None}
        inventory = self.inventory(programme)
        done = {item["id"] for item in programme.items if item["id"] in inventory
                and self.completed(programme, item, inventory[item["id"]])}
        for item in programme.items:
            if item["id"] in inventory or not set(item["blocked_by"]) <= done:
                continue
            check_stop()
            latest = self.current()
            if latest is None or latest.sha256 != programme.sha256:
                raise ProgrammeRefused("programme changed before materialization")
            title, body = programme.render(item, {k: v["number"] for k, v in inventory.items()})
            row = self.github.create_programme_issue(title=title, body=body)
            # Use the POST's identity, then read it back through the same full verifier.
            after = self.inventory(programme)
            if after.get(item["id"], {}).get("number") != row["number"]:
                raise ProgrammeRefused("created issue was not confirmed in the programme inventory")
            return {"status": "candidate-created", "created": row["number"],
                    "programme": programme.sha256}
        return {"status": "complete" if len(done) == len(programme.items) else "waiting",
                "created": None, "programme": programme.sha256, "completed": sorted(done)}

    def admit(self, issue: Mapping) -> tuple[Programme, dict] | None:
        """Mandatory check before model work and again before merge; stale bindings refuse."""
        author = str((issue.get("author") or {}).get("login") or "")
        # gh's GraphQL and REST displays use different spellings for App identities. Neither
        # spelling may lose admission by having its issue marker edited away. Actions-created
        # incident issues retain the pre-existing ordinary triage route.
        app_author = (author.startswith("app/") or author.endswith("[bot]")) and author not in {
            "app/github-actions", "github-actions[bot]",
        }
        if MARKER not in str(issue.get("body") or "") and not app_author:
            return None
        programme = self.current()
        if programme is None:
            raise ProgrammeRefused("programme issue has no current approved programme")
        inventory = self.inventory(programme)
        for item in programme.items:
            row = inventory.get(item["id"], {})
            if row.get("number") != issue.get("number"):
                continue
            # The caller's snapshot must agree too; editing while admission reads is a refusal.
            if row.get("body") != issue.get("body") or row.get("title") != issue.get("title"):
                raise ProgrammeRefused("issue changed during programme admission")
            if row.get("state") != "open":
                raise ProgrammeRefused("programme issue is not open")
            for blocker in item["blocked_by"]:
                predecessor = next(x for x in programme.items if x["id"] == blocker)
                if not self.completed(programme, predecessor, inventory[blocker]):
                    raise ProgrammeRefused("programme predecessor has no verified completion")
            return programme, item
        raise ProgrammeRefused("issue is not a member of the current programme")

    def record_completion(self, issue: Mapping, pr_number: int, merge: Mapping) -> None:
        """Called only after the production post-merge authority returned successfully."""
        if MARKER not in str(issue.get("body") or ""):
            return
        programme = self.current()
        if programme is None:
            raise ProgrammeRefused("programme retired before completion could be recorded")
        inventory = self.inventory(programme)
        item = next((x for x in programme.items
                     if inventory.get(x["id"], {}).get("number") == issue["number"]), None)
        if item is None:
            raise ProgrammeRefused("completed issue is not in the current programme")
        run = os.environ.get("GITHUB_RUN_ID", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if not run.isdecimal() or not attempt.isdecimal():
            raise ProgrammeRefused("programme completion requires a canonical Actions run")
        if merge.get("verdict") != "verified":
            raise ProgrammeRefused("merge verification did not produce a verified verdict")
        for key in ("merge_sha", "head_sha"):
            if not re.fullmatch(r"[0-9a-f]{40}", str(merge.get(key) or "")):
                raise ProgrammeRefused(f"merge verifier produced no {key}")
        receipt = {"version": "1.0", "programme": programme.sha256, "item": item["id"],
                   "issue": issue["number"], "pr": pr_number, "run": int(run),
                   "run_attempt": int(attempt), "merge_sha": merge["merge_sha"],
                   "head_sha": merge["head_sha"]}
        self.github.comment_issue(issue["number"],
                                  OUTCOME_MARKER + json.dumps(receipt, sort_keys=True) + " -->")
