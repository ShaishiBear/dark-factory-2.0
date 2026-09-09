"""Small fail-closed adapter around the authenticated GitHub CLI."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .credential_env import (
    GITHUB_MUTATION_CREDENTIAL,
    identity_age_seconds,
    scoped_environment,
)
from .refusal import IdentityExpired


class GitHubClient:
    # The default exists so a client built without config still bounds identity age rather
    # than defaulting to unlimited. The kernel passes the configured value.
    DEFAULT_IDENTITY_MAX_AGE_SECONDS = 1200

    # Which spends run behind their own mint. ACP-004 splits the merge into its own workflow
    # step preceded by `create-github-app-token`, so a stale identity there is a caller bug with
    # a fix available -- it refuses. The build's push/PR handoff is NOT split yet (ACP-004 item
    # 2): it runs at the end of one long dispatch step, observed at 56m27s on the run that
    # opened PR #134. Refusing there today would break builds that currently succeed, so it
    # reports its age and proceeds. THIS IS NOT AN EXEMPTION -- it is the honest statement that
    # item 2 is unbuilt, and the age line is the evidence that will size it.
    SPLIT_OPERATIONS: frozenset[str] = frozenset({"merge_squash"})

    def __init__(
        self,
        repository: str,
        *,
        cwd: str | Path,
        identity_max_age_seconds: int | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError(f"invalid GitHub repository identity: {repository!r}")
        self.repository = repository
        self.cwd = str(cwd)
        self.identity_max_age_seconds = (
            self.DEFAULT_IDENTITY_MAX_AGE_SECONDS
            if identity_max_age_seconds is None
            else identity_max_age_seconds
        )

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

    def _autonomous_identity(self, operation: str = "unnamed") -> dict[str, str]:
        """The environment a GitHub *mutation* runs in: the App installation token, alone.

        Three operations need this and nothing else does -- pushing an autonomous branch, opening
        or updating an autonomous PR, and the exact-head merge. They need it because GitHub
        delivers no `pull_request` or `pull_request_target` event for anything GITHUB_TOKEN
        caused, and both required contexts on `main` are produced by those events.

        `scope="github-mutation"` carries the App token and neither GH_TOKEN nor GITHUB_TOKEN, so
        when the App token is absent this raises instead of quietly authenticating as Actions and
        opening a PR that can never be judged. Failing closed here is the point: an unmergeable PR
        that looks normal costs a full validation ladder to discover.
        """
        env = scoped_environment(scope="github-mutation")
        token = env.pop(GITHUB_MUTATION_CREDENTIAL, "")
        if not token:
            raise RuntimeError(
                f"autonomous GitHub mutation requires {GITHUB_MUTATION_CREDENTIAL}; GITHUB_TOKEN "
                "is not a fallback, because GitHub starts no workflow run for an event it causes"
            )
        self._check_identity_age(operation)
        env["GH_TOKEN"] = token
        return env

    def _check_identity_age(self, operation: str) -> None:
        """Judge the identity's age before it is spent, and say so on every spend.

        A GitHub App installation token lives 60 minutes. This kernel spends the identity at
        three points -- push_branch, create_pr, merge_squash -- and the merge is by construction
        the last, after a validation measured at 4970.989 s. The margin is judged against
        OBSERVED spend ages, not against the limit: the run that opened PR #134 called create_pr
        at 56m27s, so a five-minute margin would have waved through the spend that nearly failed
        (ACP-004 section 5).

        An identity that cannot state its age is treated exactly as one known to be stale: the
        point of the check is to establish freshness, and silence establishes nothing.
        """
        limit = self.identity_max_age_seconds
        age = identity_age_seconds()
        split = operation in self.SPLIT_OPERATIONS
        stale = age is None or age > limit
        seconds = "unknown" if age is None else str(int(age))
        print(
            f"FACTORY_IDENTITY_AGE operation={operation} seconds={seconds} limit={limit} "
            f"split={'yes' if split else 'no'} "
            f"verdict={'refused' if (stale and split) else 'proceeding'}",
            flush=True,
        )
        if not (stale and split):
            return
        if age is None:
            raise IdentityExpired(
                f"refusing to spend the autonomous identity on {operation}: it does not state "
                "when it was minted, so it cannot be shown to be fresh. The workflow step that "
                "mints the App token must export DARK_FACTORY_APP_TOKEN_MINTED_AT."
            )
        raise IdentityExpired(
            f"refusing to spend the autonomous identity on {operation}: minted {int(age)}s ago, "
            f"limit {limit}s. A GitHub App installation token lives 3600s and there is no "
            "fallback by design, so a spend this late fails at the API with a bare 401. Mint a "
            "fresh identity immediately before the operation that spends it."
        )

    def run_as_app(
        self, args: list[str], *, timeout: int = 60, operation: str = "unnamed"
    ) -> str:
        """`gh`, authenticated as the App installation rather than as Actions."""
        proc = subprocess.run(
            ["gh", *args],
            cwd=self.cwd,
            env=self._autonomous_identity(operation),
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
            "number,title,body,url,headRefName,headRefOid,baseRefName,baseRefOid,state,labels,changedFiles,author"
            if holdout_safe
            else "number,title,body,url,headRefName,headRefOid,baseRefName,baseRefOid,state,labels,changedFiles,mergeable,author"
        )
        return self.json(
            ["pr", "view", str(number), "-R", self.repository, "--json", fields]
        )

    def pr_author(self, number: int) -> dict[str, str]:
        """Who opened the PR, as GitHub's REST API reports it: platform identity, one spelling.

        `gh pr view --json author` (GraphQL) names a GitHub App as `app/github-actions`; the
        REST `pulls/N` endpoint names the same actor `github-actions[bot]` with type `Bot`. The
        trust-root guard (`scripts/factory_security.py pr_identity`) decides lanes from the REST
        shape, so every kernel decision about a PR's author reads the same source.
        """
        self._number(number, "PR")
        info = self.json(["api", f"repos/{self.repository}/pulls/{number}"])
        user = info.get("user") if isinstance(info, Mapping) else None
        if not isinstance(user, Mapping):
            raise RuntimeError(f"GitHub reported no user for PR #{number}")
        return {"login": str(user.get("login") or ""), "type": str(user.get("type") or "")}

    def pr_comments(self, number: int) -> list[str]:
        """Comment bodies in creation order; the kernel's own markers live in them."""
        self._number(number, "PR")
        value = self.json(["pr", "view", str(number), "-R", self.repository, "--json", "comments"])
        raw = value.get("comments") if isinstance(value, Mapping) else None
        if not isinstance(raw, list):
            raise RuntimeError("GitHub PR comments were not an array")
        return [str(item.get("body") or "") for item in raw if isinstance(item, Mapping)]

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
        # Opened as the App: this is the event that must start `quick-authority` and
        # `trust-root-authority`. Reading the result back afterwards is observation, so it stays
        # on the ordinary token.
        self.run_as_app(
            [
                "pr", "create", "-R", self.repository, "--head", head, "--base", base,
                "--title", title, "--body-file", str(body_file),
            ],
            timeout=120,
            operation="create_pr",
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

    def create_issue(self, *, title: str, body_file: Path, labels: tuple[str, ...] = ()) -> int:
        """Open an issue. This is how the factory raises an incident that outlives its runner.

        The worker starts from current main on a fresh hosted runner every hour, so a local kill
        file cannot contain an incident: the next run never sees it. An open issue carrying the
        stop label is the containment that survives, because scripts/factory-stop.sh reads it
        from GitHub before every dispatch and fails closed when it cannot.
        """
        if not title.strip():
            raise ValueError("issue title must be non-empty")
        if not body_file.is_file():
            raise ValueError("issue body file is missing")
        argv = ["issue", "create", "-R", self.repository, "--title", title,
                "--body-file", str(body_file)]
        for label in labels:
            if not label.strip() or label.startswith("-"):
                raise ValueError(f"unsafe issue label: {label!r}")
            argv += ["--label", label]
        self.run(argv, timeout=120)
        value = self.json(
            ["issue", "list", "-R", self.repository, "--state", "open",
             "--limit", "1", "--json", "number"]
        )
        if not isinstance(value, list) or not value or not isinstance(value[0].get("number"), int):
            raise RuntimeError("created issue could not be resolved")
        return int(value[0]["number"])

    def push_branch(self, branch: str, *, force_with_lease: str | None = None) -> None:
        """Push HEAD to the branch. Plain pushes are fast-forward only.

        `force_with_lease` is the exact old head the caller judged; it is accepted only for the
        kernel's re-head, whose rebase rewrites history by construction. The remote refuses the
        push unless the branch still points at that head, so nothing pushed by anyone else can be
        overwritten.
        """
        if not branch.strip() or branch.startswith("-"):
            raise ValueError("unsafe branch name")
        if force_with_lease is not None and not re.fullmatch(r"[0-9a-f]{40,64}", force_with_lease):
            raise ValueError("force-with-lease requires the exact old head object id")
        # Pushed as the App, because a `synchronize` caused by GITHUB_TOKEN delivers no event and
        # so re-runs neither required authority on the new head. `_autonomous_identity` raises
        # when the App token is absent rather than falling back.
        token = self._autonomous_identity("push_branch")["GH_TOKEN"]
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
            argv = ["git", "push"]
            if force_with_lease is not None:
                argv.append(f"--force-with-lease=refs/heads/{branch}:{force_with_lease}")
            argv += [
                f"https://github.com/{self.repository}.git",
                f"HEAD:refs/heads/{branch}",
            ]
            proc = subprocess.run(
                argv,
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
        # The merge broker: App identity, still bound to the exact authorized head by both the
        # check above and `--match-head-commit`, which GitHub itself enforces.
        self.run_as_app(
            [
                "pr", "merge", str(number), "-R", self.repository, "--squash",
                "--match-head-commit", expected_head,
            ],
            timeout=180,
            operation="merge_squash",
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
