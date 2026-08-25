"""Small fail-closed adapter around the authenticated GitHub CLI."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .credential_env import scoped_environment


class GitHubClient:
    def __init__(self, repository: str, *, cwd: str | Path):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError(f"invalid GitHub repository identity: {repository!r}")
        self.repository = repository
        self.cwd = str(cwd)

    def run(self, args: list[str], *, timeout: int = 60) -> str:
        proc = subprocess.run(
            ["gh", *args],
            cwd=self.cwd,
            env=scoped_environment(scope="github"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode:
            detail = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
            raise RuntimeError(f"gh {' '.join(args)} failed rc={proc.returncode}: {detail}")
        return proc.stdout or ""

    def json(self, args: list[str], *, timeout: int = 60) -> Any:
        raw = self.run(args, timeout=timeout)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"gh {' '.join(args)} returned invalid JSON") from exc

    def issue(self, number: int) -> Mapping[str, Any]:
        self._number(number, "issue")
        return self.json(
            [
                "issue", "view", str(number), "-R", self.repository,
                "--json", "number,title,body,labels,state,url,updatedAt",
            ]
        )

    def pr(self, number: int, *, holdout_safe: bool = False) -> Mapping[str, Any]:
        self._number(number, "PR")
        fields = (
            "number,title,body,url,headRefName,headRefOid,baseRefName,baseRefOid,state,labels,changedFiles"
            if holdout_safe
            else "number,title,body,url,headRefName,headRefOid,baseRefName,baseRefOid,state,labels,changedFiles,mergeable"
        )
        return self.json(
            ["pr", "view", str(number), "-R", self.repository, "--json", fields]
        )

    def list_issues(self, label: str, *, limit: int = 100) -> list[Mapping[str, Any]]:
        value = self.json(
            [
                "issue", "list", "-R", self.repository, "--state", "open", "--label", label,
                "--limit", str(limit), "--json", "number,title,labels,updatedAt,url",
            ]
        )
        if not isinstance(value, list):
            raise RuntimeError("GitHub issue list was not an array")
        return value

    def list_prs(self, label: str, *, limit: int = 100) -> list[Mapping[str, Any]]:
        value = self.json(
            [
                "pr", "list", "-R", self.repository, "--state", "open", "--label", label,
                "--limit", str(limit), "--json", "number,title,labels,updatedAt,url,headRefName,headRefOid",
            ]
        )
        if not isinstance(value, list):
            raise RuntimeError("GitHub PR list was not an array")
        return value

    def add_issue_label(self, number: int, label: str) -> None:
        self._number(number, "issue")
        self.run(["issue", "edit", str(number), "-R", self.repository, "--add-label", label])

    def remove_issue_label(self, number: int, label: str) -> None:
        self._number(number, "issue")
        self.run(["issue", "edit", str(number), "-R", self.repository, "--remove-label", label])

    def add_pr_label(self, number: int, label: str) -> None:
        self._number(number, "PR")
        self.run(["pr", "edit", str(number), "-R", self.repository, "--add-label", label])

    def remove_pr_label(self, number: int, label: str) -> None:
        self._number(number, "PR")
        self.run(["pr", "edit", str(number), "-R", self.repository, "--remove-label", label])

    def comment_issue(self, number: int, body: str) -> None:
        self._number(number, "issue")
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        self.run(["issue", "comment", str(number), "-R", self.repository, "--body", body])

    def comment_pr(self, number: int, body: str) -> None:
        self._number(number, "PR")
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        self.run(["pr", "comment", str(number), "-R", self.repository, "--body", body])

    def create_pr(self, *, head: str, base: str, title: str, body_file: Path) -> Mapping[str, Any]:
        if not head.strip() or not base.strip() or not title.strip():
            raise ValueError("PR head/base/title must be non-empty")
        if not body_file.is_file():
            raise ValueError("PR body file is missing")
        self.run(
            [
                "pr", "create", "-R", self.repository, "--head", head, "--base", base,
                "--title", title, "--body-file", str(body_file),
            ],
            timeout=120,
        )
        value = self.json(
            [
                "pr", "view", head, "-R", self.repository,
                "--json", "number,url,headRefOid,baseRefOid,state",
            ]
        )
        if not isinstance(value, Mapping) or not isinstance(value.get("number"), int):
            raise RuntimeError("created PR could not be resolved")
        return value

    def push_branch(self, branch: str) -> None:
        if not branch.strip() or branch.startswith("-"):
            raise ValueError("unsafe branch name")
        github_env = scoped_environment(scope="github")
        token = github_env.get("GH_TOKEN") or github_env.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("git push requires GH_TOKEN or GITHUB_TOKEN")
        with tempfile.TemporaryDirectory(prefix="dark-factory-git-auth-") as tmp:
            askpass = Path(tmp) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
                "  *Password*) printf '%s\\n' \"$FACTORY_GIT_TOKEN\" ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            env = scoped_environment(scope="none")
            env.update(
                {
                    "GIT_ASKPASS": str(askpass),
                    "GIT_TERMINAL_PROMPT": "0",
                    "FACTORY_GIT_TOKEN": token,
                }
            )
            proc = subprocess.run(
                [
                    "git", "push",
                    f"https://github.com/{self.repository}.git",
                    f"HEAD:refs/heads/{branch}",
                ],
                cwd=self.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        if proc.returncode:
            raise RuntimeError(f"git push failed: {((proc.stdout or '') + (proc.stderr or ''))[-3000:]}")

    def merge_squash(self, number: int, *, expected_head: str) -> None:
        self._number(number, "PR")
        info = self.pr(number)
        if info.get("headRefOid") != expected_head:
            raise RuntimeError("refusing merge: PR head moved after authorization")
        self.run(
            [
                "pr", "merge", str(number), "-R", self.repository, "--squash",
                "--match-head-commit", expected_head,
            ],
            timeout=180,
        )

    @staticmethod
    def labels(value: Mapping[str, Any]) -> set[str]:
        raw = value.get("labels", [])
        if not isinstance(raw, list):
            return set()
        return {
            str(item.get("name"))
            for item in raw
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }

    @staticmethod
    def _number(value: int, name: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} number must be positive")
