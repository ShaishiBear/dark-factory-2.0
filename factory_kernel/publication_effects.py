"""Bounded programme effects, reachable only from the protected publication workflow.

Every spend records pending state before POST, and ambiguous outcomes end this attempt.
No worker, model output, PR description or cached currency response grants an effect.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .canonical import canonical_bytes
from .frontdoor_intent import IntentRefused
from .programme import ACTIVE_PATH
from .publication_admission import validate_manifest
from .publication_client import current_currency
from .publication_source import observe_publication_source
from . import publication_policy as policy


class EffectJournal:
    """Local durable attempt evidence, retained even on failure by the protected job.

    This journal never permits resuming a POST. A first-attempt/unique-dispatch guard and
    the host's separate dispatch reservation prevent replay after loss of the runner.
    """
    def __init__(self, path, manifest):
        self.path = Path(path)
        if self.path.exists() or self.path.is_symlink():
            raise IntentRefused("publication effect attempt already exists; reconcile without retry")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.record = {"schema": "dark-factory/publication-effects", "schema_version": "1.0",
                       "request_id": manifest["request_id"], "run_id": manifest["run_id"],
                       "source_sha": manifest["source_sha"], "effects": []}
        self._save()

    def _save(self):
        fd, temporary = tempfile.mkstemp(prefix=".publication-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(canonical_bytes(self.record))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def spend(self, phase, operation):
        if any(row["phase"] == phase or row["state"] != "observed" for row in self.record["effects"]):
            raise IntentRefused("publication effect already attempted or outcome is uncertain")
        row = {"phase": phase, "state": "pending", "at": datetime.now(timezone.utc).isoformat()}
        self.record["effects"].append(row)
        self._save()  # A failed durable write means no POST.
        try:
            result = operation()
        except Exception:
            row["state"] = "uncertain"
            self._save()
            raise IntentRefused("publication effect outcome uncertain; operator reconciliation required") from None
        row.update(state="observed", result=result)
        self._save()  # If this fails, the persisted pending record still forbids replay.
        return result


class ProgrammePublisher:
    def __init__(self, github, manifest, journal, *, currency=current_currency,
                 source=observe_publication_source):
        validate_manifest(manifest)
        if github.repository != policy.REPOSITORY:
            raise IntentRefused("publication destination refused")
        self.github, self.manifest, self.journal = github, manifest, journal
        self.currency, self.source = currency, source
        self.branch = policy.BRANCH_PREFIX + manifest["request_id"]
        self.prefix = f"repos/{policy.REPOSITORY}"

    def before(self, phase):
        observation = self.source(self.github)
        if (observation["repository"] != policy.REPOSITORY or observation["protected"] is not True
                or observation["main_sha"] != self.manifest["source_sha"]
                or observation["active_input"] is not None
                or observation["stop"] != {"state": "clear", "issues": []}):
            raise IntentRefused("publication source or stop changed before effect")
        result = self.currency(self.manifest, phase)
        # The actual transport verifies MAC/freshness; retain explicit identity checks here
        # so another adapter cannot quietly substitute a different owner request.
        expected = {"decision": "current-owner-request", "repository": policy.REPOSITORY,
                    "project": policy.PROJECT, "main_sha": self.manifest["source_sha"]}
        expected.update({key: self.manifest[key] for key in
                         ("request_id", "request_sha256", "input_sha256", "programme_sha256")})
        if any(result.get(key) != value for key, value in expected.items()):
            raise IntentRefused("publication owner currency differs from the exact validated payload")

    def _api_effect(self, phase, method, endpoint, payload, operation, project):
        # Structured JSON is written privately, never interpolated into a shell command.
        with tempfile.TemporaryDirectory(prefix="factory-publication-body-") as directory:
            path = Path(directory) / "body.json"
            path.touch(mode=0o600)
            path.write_bytes(canonical_bytes(payload))
            def spend():
                raw = self.github.run_as_app(["api", "--method", method, endpoint,
                                               "--input", str(path)], operation=operation)
                return project(json.loads(raw))
            self.before("pull-request" if phase == "pull-request" else "branch")
            return self.journal.spend(phase, spend)

    def publish(self):
        existing = self.github.json(["api", f"{self.prefix}/git/matching-refs/heads/{self.branch}"])
        if existing != []:
            raise IntentRefused("publication branch exists or cannot be ruled out; reconcile without retry")
        def branch_result(value):
            if value.get("ref") != "refs/heads/" + self.branch or value.get("object", {}).get("sha") != self.manifest["source_sha"]:
                raise IntentRefused("created branch identity differs")
            return {"ref": value["ref"], "sha": value["object"]["sha"]}
        self._api_effect("branch", "POST", f"{self.prefix}/git/refs",
                         {"ref": "refs/heads/" + self.branch, "sha": self.manifest["source_sha"]},
                         "push_branch", branch_result)
        branch = self.github.json(["api", f"{self.prefix}/git/ref/heads/{self.branch}"])
        if branch.get("object", {}).get("sha") != self.manifest["source_sha"]:
            raise IntentRefused("publication branch moved before its data commit")
        def commit_result(value):
            commit = value.get("commit", {})
            sha = commit.get("sha", "")
            if (not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
                    or [row.get("sha") for row in commit.get("parents", [])] != [self.manifest["source_sha"]]):
                raise IntentRefused("publication commit identity differs")
            return {"head_sha": sha}
        committed = self._api_effect("commit", "PUT", f"{self.prefix}/contents/{ACTIVE_PATH}",
                                     {"message": "Publish approved programme", "branch": self.branch,
                                      "content": base64.b64encode(canonical_bytes(self.manifest["input"]) + b"\n").decode("ascii")},
                                     "push_branch", commit_result)
        head = committed["head_sha"]
        prior = self.github.json(["api", f"{self.prefix}/pulls?state=all&head={policy.OWNER}:{self.branch}&per_page=100"])
        if prior != []:
            raise IntentRefused("publication PR exists or cannot be ruled out; reconcile without retry")
        def pr_result(value):
            if (type(value.get("number")) is not int or value["number"] <= 0
                    or value.get("head", {}).get("sha") != head or value.get("base", {}).get("sha") != self.manifest["source_sha"]
                    or value.get("user", {}).get("login") != policy.APP_LOGIN):
                raise IntentRefused("created publication PR identity differs")
            return {"pr": value["number"], "head_sha": head}
        body = (f"<!-- dark-factory-publication:{self.manifest['run_id']}:{self.manifest['request_id']} -->\n\n"
                "Publish the exact owner-approved programme input. This data-only publication is not product completion.\n\n"
                f"Input SHA-256: `{self.manifest['input_sha256']}`\n")
        return self._api_effect("pull-request", "POST", f"{self.prefix}/pulls",
                                {"title": "Publish approved programme", "head": self.branch,
                                 "base": "main", "body": body, "draft": False}, "create_pr", pr_result)

    def _exact_pr(self, number, head):
        if type(number) is not int or number <= 0:
            raise IntentRefused("invalid publication PR")
        value = self.github.json(["api", f"{self.prefix}/pulls/{number}"])
        if (value.get("state") != "open" or value.get("draft") is not False
                or value.get("head", {}).get("sha") != head or value["head"].get("ref") != self.branch
                or value.get("base", {}).get("sha") != self.manifest["source_sha"] or value["base"].get("ref") != "main"
                or value.get("user", {}).get("login") != policy.APP_LOGIN):
            raise IntentRefused("publication PR moved or identity differs")
        return value

    def merge(self, number, head):
        self._exact_pr(number, head)
        checks = self.github.json(["pr", "checks", str(number), "-R", policy.REPOSITORY,
                                   "--required", "--json", "name,bucket"])
        if (not isinstance(checks, list) or not checks or any(row.get("bucket") != "pass" for row in checks)
                or not {"quick-authority", "trust-root-authority"}.issubset({row.get("name") for row in checks})):
            raise IntentRefused("publication requires nonempty passing required checks")
        self._exact_pr(number, head)
        self.before("merge")  # Last remote authorization observation before exact-head spend.
        def spend():
            self.github.run_as_app(["pr", "merge", str(number), "-R", policy.REPOSITORY,
                                    "--squash", "--match-head-commit", head], operation="merge_squash")
            return {"pr": number, "head_sha": head}
        return self.journal.spend("merge", spend)

    def observe_merged(self, number, head):
        """Read-only receipt; a failed observation is never reported as product completion."""
        value = self.github.json(["api", f"{self.prefix}/pulls/{number}"])
        merged = value.get("merge_commit_sha")
        if (value.get("merged") is not True or value.get("state") != "closed"
                or value.get("head", {}).get("sha") != head or not isinstance(merged, str)
                or len(merged) != 40 or any(c not in "0123456789abcdef" for c in merged)):
            raise IntentRefused("publication merge outcome is unverified")
        actual = self.github.json(["api", f"{self.prefix}/git/commits/{merged}"])
        expected = self.github.json(["api", f"{self.prefix}/git/commits/{head}"])
        main = self.github.json(["api", f"{self.prefix}/git/ref/heads/main"])
        if (actual.get("sha") != merged or expected.get("sha") != head
                or actual.get("tree", {}).get("sha") != expected.get("tree", {}).get("sha")
                or not isinstance(actual.get("tree", {}).get("sha"), str)
                or len(actual["tree"]["sha"]) != 40 or any(c not in "0123456789abcdef" for c in actual["tree"]["sha"])
                or [row.get("sha") for row in actual.get("parents", [])] != [self.manifest["source_sha"]]
                or main.get("object", {}).get("sha") != merged):
            raise IntentRefused("publication merged tree, parent or current main is unverified")
        return {"state": "publication-observed", "pr": number, "head_sha": head, "main_sha": merged,
                "tree_sha": actual["tree"]["sha"], "programme_sha256": self.manifest["programme_sha256"],
                "product_complete": False}
