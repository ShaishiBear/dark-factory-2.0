"""Base-anchored trust-root authority and exact-head unattended merge.

Two properties, each with a structural half and a behavioural half:

1. The program that decides whether a PR may alter the trust root runs from a commit that is
   already on the protected branch, never from the PR head. Structurally: the trust-root
   workflow is a `pull_request_target` workflow that checks out `github.sha`; the kernel
   validator runs the guard from its main checkout. Behaviourally: `verify_pr_trusted_base`
   refuses to run from the PR head, binds to the expected base and head, and judges a PR that
   rewrites the guard using the guard it did not rewrite.

2. Merge is bound to the exact judged head. The unattended-merge job arms GitHub auto-merge
   with `expectedHeadOid`, squash only, and only for the maintainer lane; the ruleset still
   requires every check green on that head.

These tests run the real `scripts/factory_security.py` against a real temporary Git repository
with a bare `origin`, and stand in only for `gh`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUARD = ROOT / "scripts" / "factory_security.py"
WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-trust-root.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-ci.yml"
RUNTIME = ROOT / "factory_kernel" / "runtime.py"

PR = "77"
MAINTAINER = {"login": "maintainer", "type": "User", "association": "OWNER"}
BOT = {"login": "github-actions[bot]", "type": "Bot", "association": "CONTRIBUTOR"}
STRANGER = {"login": "someone", "type": "User", "association": "NONE"}
USER_ACTOR = {"login": "maintainer", "type": "User"}
BOT_ACTOR = {"login": "github-actions[bot]", "type": "Bot"}


def load_guard():
    spec = importlib.util.spec_from_file_location("factory_security_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false",
         *args],
        cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


class Repo:
    """A working clone at `main` plus a bare origin that also serves refs/pull/<n>/head."""

    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        git(tmp, "init", "-q", "--bare", str(self.origin))
        self.work.mkdir()
        git(self.work, "init", "-q")
        git(self.work, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.write("scripts/factory_security.py", "VERDICT = 'computed'\n")
        self.write(".github/workflows/dark-factory-ci.yml", "steps:\n  - run: python scripts/factory_security.py\n")
        self.write("FACTORY_RULES.md", "rules\n")
        self.write("app/backend/routes/chat.py", "safe = True\n")
        self.base = self.commit("base")
        git(self.work, "push", "-q", "origin", "main")

    def write(self, rel: str, text: str) -> None:
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message: str) -> str:
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", message)
        return git(self.work, "rev-parse", "HEAD")

    def open_pr(self, changes: dict[str, str], *, publish: bool = True) -> str:
        """Create the PR branch from base, push it as refs/pull/PR/head, return to main."""
        git(self.work, "checkout", "-q", "-b", "pr", self.base)
        for rel, text in changes.items():
            self.write(rel, text)
        head = self.commit("pr")
        if publish:
            git(self.work, "push", "-q", "origin", f"HEAD:refs/pull/{PR}/head")
        git(self.work, "checkout", "-q", "main")
        return head


class TrustedBaseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-trusted-base-")
        self.repo = Repo(Path(self.tmp.name))
        self.m = load_guard()
        self.m.ROOT = self.repo.work
        self.real_run = self.m.run
        self.gh = {"author": MAINTAINER, "commits": [], "body": "Fixes #1\n", "head": None}
        self.m.run = self._run

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, argv, *, check=True):
        if argv[0] != "gh":
            return self.real_run(argv, check=check)
        head = self.gh["head"]
        if argv[:3] == ["gh", "pr", "view"]:
            payload = {"body": self.gh["body"], "baseRefName": "main",
                       "baseRefOid": self.repo.base, "headRefOid": head}
        elif argv[:3] == ["gh", "repo", "view"]:
            payload = {"nameWithOwner": "example/repo"}
        elif argv[1] == "api" and argv[-1].endswith("/commits"):
            payload = [
                {"sha": c["sha"], "author": c["author"], "committer": c["committer"]}
                for c in self.gh["commits"]
            ]
        elif argv[1] == "api":
            a = self.gh["author"]
            payload = {"user": {"login": a["login"], "type": a["type"]},
                       "author_association": a["association"]}
        else:
            raise AssertionError(f"unexpected gh call {argv}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    def judge(self, changes, *, author=MAINTAINER, commit_actor=USER_ACTOR, body="Fixes #1\n",
              expect_base=None, expect_head="auto", publish=True):
        head = self.repo.open_pr(changes, publish=publish)
        self.gh.update(author=author, body=body, head=head,
                       commits=[{"sha": head, "author": commit_actor, "committer": commit_actor}])
        return self.m.verify_pr_trusted_base(
            PR, expect_base=expect_base,
            expect_head=head if expect_head == "auto" else expect_head,
        )

    # -- attack 1: the PR rewrites the guard to say pass -------------------------------------
    def test_pr_that_forces_the_guard_to_pass_is_judged_by_the_base_guard(self):
        forged = "def evaluate(**kw):\n    return {'verdict': 'pass'}\n"
        result = self.judge({"scripts/factory_security.py": forged}, author=BOT, commit_actor=BOT_ACTOR)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["protected_paths"], ["scripts/factory_security.py"])
        self.assertEqual(result["binding"]["mode"], "trusted-base")
        self.assertEqual(result["binding"]["base_sha"], self.repo.base)
        self.assertFalse(result["authority"]["unattended_merge_eligible"])
        # The program that produced this verdict is the checked-in guard, not the PR's copy.
        self.assertEqual(Path(self.m.__file__).resolve(), GUARD.resolve())

    # -- attack 2: the PR edits the head-based workflow so its own guard step disappears ------
    def test_pr_that_removes_the_head_guard_from_ci_cannot_grant_itself_permission(self):
        result = self.judge({".github/workflows/dark-factory-ci.yml": "steps: []\n"},
                            author=BOT, commit_actor=BOT_ACTOR)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["protected_paths"], [".github/workflows/dark-factory-ci.yml"])

    # -- attack 3 / 4: identity -------------------------------------------------------------------
    def test_bot_trust_root_pr_fails(self):
        result = self.judge({"FACTORY_RULES.md": "new rules\n"}, author=BOT, commit_actor=BOT_ACTOR)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["authority"]["lane"], "autonomous")
        self.assertFalse(result["authority"]["unattended_merge_eligible"])

    def test_unprivileged_user_trust_root_pr_fails(self):
        result = self.judge({"FACTORY_RULES.md": "new rules\n"}, author=STRANGER)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["authority"]["lane"], "autonomous")

    # -- attack 5: legitimate maintainer --------------------------------------------------------
    def test_maintainer_trust_root_pr_passes_and_is_eligible_for_unattended_merge(self):
        result = self.judge({"FACTORY_RULES.md": "new rules\n", "scripts/factory_security.py": "VERDICT = 'v2'\n"},
                            expect_base=self.repo.base)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["authority"]["lane"], "human-maintenance")
        self.assertTrue(result["authority"]["unattended_merge_eligible"])
        self.assertEqual(result["protected_paths"], ["FACTORY_RULES.md", "scripts/factory_security.py"])
        binding = result["binding"]
        self.assertEqual(binding["repository"], "example/repo")
        self.assertEqual(binding["pr"], int(PR))
        self.assertEqual(binding["base_sha"], self.repo.base)
        self.assertEqual(binding["head_sha"], self.gh["head"])
        self.assertEqual(binding["changed_files"], ["FACTORY_RULES.md", "scripts/factory_security.py"])

    def test_bot_product_pr_passes_but_is_never_eligible_for_unattended_merge(self):
        """The kernel merges autonomous PRs after the evidence ladder; the guard never arms them."""
        result = self.judge({"app/backend/routes/chat.py": "safe = False\n"}, author=BOT, commit_actor=BOT_ACTOR)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["authority"]["lane"], "autonomous")
        self.assertFalse(result["authority"]["unattended_merge_eligible"])

    # -- attack 6 / 7: head binding -----------------------------------------------------------------
    def test_expected_head_mismatch_refuses(self):
        with self.assertRaises(SystemExit):
            self.judge({"FACTORY_RULES.md": "x\n"}, expect_head="0" * 40)

    def test_expected_base_mismatch_refuses(self):
        with self.assertRaises(SystemExit):
            self.judge({"FACTORY_RULES.md": "x\n"}, expect_base="0" * 40)

    def test_reported_head_that_is_not_the_fetched_head_refuses(self):
        """GitHub says the head is X but refs/pull/N/head serves Y: nothing is judged."""
        self.repo.open_pr({"FACTORY_RULES.md": "x\n"})
        git(self.repo.work, "checkout", "-q", "pr")
        self.repo.write("FACTORY_RULES.md", "y\n")
        unpublished = self.repo.commit("unpublished")
        git(self.repo.work, "checkout", "-q", "main")
        self.gh.update(head=unpublished, commits=[{"sha": unpublished, "author": USER_ACTOR, "committer": USER_ACTOR}])
        with self.assertRaises(SystemExit):
            self.m.verify_pr_trusted_base(PR, expect_base=None, expect_head=None)

    def test_running_from_the_pr_head_refuses_for_that_reason(self):
        """The refusal must be the self-judging check itself, not a later check that happens to
        also fail: a PR head is never on origin/main, so the ancestry check would mask a removed
        self-judging check and the mutation would escape."""
        head = self.repo.open_pr({"FACTORY_RULES.md": "x\n"})
        git(self.repo.work, "checkout", "-q", head)
        self.gh.update(head=head, commits=[{"sha": head, "author": USER_ACTOR, "committer": USER_ACTOR}])
        captured = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(captured):
            self.m.verify_pr_trusted_base(PR, expect_base=None, expect_head=None)
        self.assertIn("refuses to run from the PR head", captured.getvalue())

    def test_base_that_is_not_on_origin_main_refuses(self):
        head = self.repo.open_pr({"FACTORY_RULES.md": "x\n"})
        git(self.repo.work, "checkout", "-q", "-b", "local-only", self.repo.base)
        self.repo.write("app/backend/routes/chat.py", "local = True\n")
        self.repo.commit("never pushed")
        self.gh.update(head=head, commits=[{"sha": head, "author": USER_ACTOR, "committer": USER_ACTOR}])
        with self.assertRaises(SystemExit):
            self.m.verify_pr_trusted_base(PR, expect_base=None, expect_head=None)

    def test_maintainer_lowering_a_floor_is_refused_from_the_base(self):
        """The base guard reads floor.json at base and head from the object store and refuses
        the regression even though the author holds the human lane."""
        self.repo.write(".factory/locks/floor.json", json.dumps({"unit_tests": 1033, "static_checks": 5}) + "\n")
        self.repo.base = self.repo.commit("floors")
        git(self.repo.work, "push", "-q", "origin", "main")
        result = self.judge({".factory/locks/floor.json": json.dumps({"unit_tests": 500, "static_checks": 5}) + "\n"})
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["authority"]["lane"], "human-maintenance")
        self.assertTrue(any(x["kind"] == "ratchet_regression" and "unit_tests lowered from 1033 to 500" in x["detail"]
                            for x in result["findings"]))

    def test_maintainer_raising_a_floor_passes_from_the_base(self):
        self.repo.write(".factory/locks/floor.json", json.dumps({"unit_tests": 1033}) + "\n")
        self.repo.base = self.repo.commit("floors")
        git(self.repo.work, "push", "-q", "origin", "main")
        result = self.judge({".factory/locks/floor.json": json.dumps({"unit_tests": 1100, "e2e_steps": 8}) + "\n"})
        self.assertEqual(result["verdict"], "pass")

    # -- attack 8: the maintainer lane waives nothing else ------------------------------------------
    def test_secret_in_maintainer_trust_root_pr_still_fails(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        result = self.judge({"FACTORY_RULES.md": "x\n", "app/backend/routes/chat.py": f"KEY = '{marker}'\n"})
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "secret" for x in result["findings"]))
        self.assertFalse(result["authority"]["unattended_merge_eligible"])

    def test_bot_commit_on_maintainer_trust_root_pr_still_fails(self):
        result = self.judge({"FACTORY_RULES.md": "x\n"}, commit_actor=BOT_ACTOR)
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "protected_path_provenance" for x in result["findings"]))


class HeadModeBindingTests(unittest.TestCase):
    def test_head_mode_result_is_bound_too(self):
        m = load_guard()

        def fake_run(argv, *, check=True):
            if argv[:3] == ["gh", "repo", "view"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"nameWithOwner": "example/repo"}), "")
            raise AssertionError(argv)

        m.run = fake_run
        result = m.bound({"verdict": "pass"}, mode="head", pr="9", base="a" * 40, head="b" * 40, changed=["x.py"])
        self.assertEqual(result["binding"], {
            "mode": "head", "repository": "example/repo", "pr": 9,
            "base_sha": "a" * 40, "head_sha": "b" * 40, "changed_files": ["x.py"],
        })


class TrustRootWorkflowStructureTests(unittest.TestCase):
    """The workflow is the part of the authority GitHub executes; its shape is the property."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.authority, cls.merge = cls.text.split("\n  unattended-merge:\n", 1)

    def test_workflow_definition_comes_from_the_base_branch(self):
        self.assertIn("on:\n  pull_request_target:\n    branches: [main]", self.text)
        self.assertNotRegex(self.text, r"^\s+pull_request:\s*$", "a pull_request trigger would run the PR's copy")

    def test_only_the_base_tip_is_ever_checked_out(self):
        self.assertIn("ref: ${{ github.sha }}", self.authority)
        for line in self.text.splitlines():
            if line.strip().startswith("ref:"):
                self.assertNotIn("head", line, line)
        self.assertIn('test "$(git rev-parse HEAD)" = "$TRUSTED_BASE"', self.authority)
        self.assertIn('test "$(git rev-parse HEAD)" != "$PR_HEAD"', self.authority)
        self.assertIn("git merge-base --is-ancestor HEAD origin/main", self.authority)
        self.assertIn("persist-credentials: false", self.authority)

    def test_guard_runs_in_trusted_base_mode_bound_to_event_identities(self):
        self.assertIn('--pr "$PR_NUMBER" --trusted-base', self.authority)
        self.assertIn('--expect-base "$TRUSTED_BASE"', self.authority)
        self.assertIn('--expect-head "$PR_HEAD"', self.authority)
        self.assertIn("TRUSTED_BASE: ${{ github.sha }}", self.authority)
        self.assertIn("PR_HEAD: ${{ github.event.pull_request.head.sha }}", self.authority)
        self.assertIn('assert b["mode"] == "trusted-base"', self.authority)
        self.assertIn("unattended_merge_eligible", self.authority)

    def test_authority_job_holds_a_read_only_token(self):
        self.assertIn("permissions: {}", self.text.split("jobs:", 1)[0])
        self.assertIn("    permissions:\n      contents: read\n      pull-requests: read\n      issues: read", self.authority)
        self.assertNotIn("contents: write", self.authority)

    def test_conflicting_pr_turns_the_required_check_red_after_the_verdict(self):
        """A dirty PR gets no pull_request run, so only this base-run job can make it visible."""
        judge = self.authority.index("id: judge")
        mergeable = self.authority.index("id: mergeable")
        self.assertLess(judge, mergeable, "mergeability is asked after the verdict, not instead of it")
        step = self.authority[mergeable:]
        self.assertIn('--jq \'.mergeable_state // "unknown"\'', step)
        self.assertIn('if [ "$state" = "dirty" ]; then', step)
        self.assertIn("TRUST_ROOT_REFUSED pr is not mergeable (conflicting with base); rebase", step)
        # The refusal is an exit, not a warning: the ruleset only sees a red check.
        refused = step.index("TRUST_ROOT_REFUSED")
        self.assertIn("exit 1", step[refused:refused + 200])
        self.assertIn("for attempt in 1 2 3 4 5 6; do", step)
        self.assertIn("TRUST_ROOT_MERGEABILITY_UNKNOWN", step)
        # unknown after retries must not exit: the verdict on the diff is still valid.
        unknown = step.index("TRUST_ROOT_MERGEABILITY_UNKNOWN")
        self.assertNotIn("exit 1", step[unknown:unknown + 200])
        # The merge job depends on the whole authority job, so a red mergeability step arms nothing.
        self.assertIn("needs: trust-root-authority", self.merge)

    def test_unattended_merge_is_gated_on_the_trusted_decision_and_the_stop_button(self):
        self.assertIn("if: needs.trust-root-authority.outputs.unattended == 'true'", self.merge)
        self.assertIn("bash scripts/factory-stop.sh", self.authority)
        self.assertIn('[ "${{ steps.stop.outputs.stopped }}" = "false" ]', self.authority)

    def test_unattended_merge_executes_no_code_and_binds_to_the_exact_head(self):
        self.assertNotIn("actions/checkout", self.merge)
        self.assertNotIn("uses:", self.merge)
        self.assertIn('test "$EXPECTED_HEAD" = "$EVENT_HEAD"', self.merge)
        self.assertIn("EXPECTED_HEAD: ${{ needs.trust-root-authority.outputs.head }}", self.merge)
        self.assertIn("expectedHeadOid: $head, mergeMethod: SQUASH", self.merge)
        self.assertIn("enablePullRequestAutoMerge", self.merge)
        self.assertIn("    permissions:\n      contents: write\n      pull-requests: write", self.merge)

    def test_actions_are_pinned_by_commit(self):
        for line in self.text.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"uses: [\w./-]+@[0-9a-f]{40}$", line)

    def test_head_based_ci_still_runs_the_guard_as_defence_in_depth(self):
        ci = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python scripts/factory_security.py --pr", ci)
        self.assertNotIn("--trusted-base", ci, "the head gate must not claim base authority")


class KernelValidatorTests(unittest.TestCase):
    def test_kernel_validator_runs_the_guard_from_main_in_trusted_base_mode(self):
        from harness.rehearsal import Scenario, rehearse

        trace = rehearse(Scenario("happy"))
        step = next(s for s in trace.steps if s.name == "factory_security.py")
        self.assertIn("--trusted-base", step.argv)
        self.assertIn("--expect-head", step.argv)
        self.assertEqual(Path(step.cwd).resolve(), ROOT.resolve(), "guard must run from the main checkout")
        # Everything that tests the proposed code still runs in the PR-head worktree.
        for tool in ("factory_evidence.py", "merge_verify.py:pre"):
            other = next(s for s in trace.steps if s.name == tool)
            self.assertNotEqual(Path(other.cwd).resolve(), ROOT.resolve(), tool)

    def test_runtime_source_states_the_property(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('"--trusted-base", "--expect-head", head,', source)
        self.assertIn("The guard is base-anchored", source)


if __name__ == "__main__":
    unittest.main()
