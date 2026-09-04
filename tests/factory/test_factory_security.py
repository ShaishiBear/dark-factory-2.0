import importlib.util
import json
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_security.py"
spec = importlib.util.spec_from_file_location("factory_security", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SecurityGuardTests(unittest.TestCase):
    def backend(self, deps=None, dev=None, build=None):
        deps = deps or ["fastapi", "httpx"]
        dev = dev or ["pytest==8.3.4"]
        build = build or ["hatchling"]
        q = lambda xs: ", ".join(json.dumps(x) for x in xs)
        return (
            "[project]\nname='x'\nversion='0.1'\n"
            f"dependencies=[{q(deps)}]\n"
            "[project.optional-dependencies]\n"
            f"dev=[{q(dev)}]\n"
            "[build-system]\n"
            f"requires=[{q(build)}]\nbuild-backend='hatchling.build'\n"
        )

    def frontend(self, deps=None, dev=None):
        return json.dumps({
            "dependencies": deps or {"react": "^18.3.1"},
            "devDependencies": dev or {"vite": "^5.2.13"},
        })

    def evaluate(self, **overrides):
        values = {
            "changed_files": ["app/backend/routes/chat.py"],
            "base_backend": self.backend(),
            "head_backend": self.backend(),
            "base_frontend": self.frontend(),
            "head_frontend": self.frontend(),
            "diff": "diff --git a/app/backend/routes/chat.py b/app/backend/routes/chat.py\n"
                    "+++ b/app/backend/routes/chat.py\n+safe = True\n",
            "body": "Fixes #42\n",
        }
        values.update(overrides)
        return m.evaluate(**values)

    def test_clean_code_change_passes(self):
        self.assertEqual(self.evaluate()["verdict"], "pass")

    def test_repo_owned_kernel_paths_are_blocked(self):
        paths = [
            "factory_kernel/runtime.py",
            ".factory/kernel.json",
            ".factory/evidence-spine.json",
            ".factory/prompts/implement.md",
            ".factory/holdout/run.py",
            ".factory/benchmark/public.json",
            "harness/ci.py",
            "scripts/factory_evidence.py",
            "deploy/systemd/dark-factory.service",
            "tests/factory/test_factory_evidence_closure.py",
        ]
        for path in paths:
            with self.subTest(path=path):
                result = self.evaluate(changed_files=[path])
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["protected_paths"], [path])

    def test_factory_detector_tests_are_trust_root(self):
        """The tests are what turn an injected trust-root mutation into a red suite."""
        for path in (
            "tests/factory/test_factory_independence.py",
            "tests/factory/test_factory_merge_verify.py",
            "tests/factory/test_factory_bootstrap.py",
        ):
            with self.subTest(path=path):
                result = self.evaluate(changed_files=[path])
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["protected_paths"], [path])

    def test_mission_security_invariant_paths_are_blocked(self):
        """CLAUDE.md states the factory auto-rejects these; that must be true of the guard.

        The blinded holdout already defends owner-only access, the single cap value and per-user
        lock keying behaviourally. It does not cover token issuance and verification, password
        hashing, the admin dependency, the signup abuse guard or CORS -- those had no detector at
        all, so an autonomous run could have relaxed them with nothing deterministic refusing.
        """
        for path in (
            "app/backend/auth/tokens.py",
            "app/backend/auth/password.py",
            "app/backend/auth/dependencies.py",
            "app/backend/routes/auth.py",
            "app/backend/routes/admin.py",
            "app/backend/routes/conversations.py",
            "app/backend/routes/messages.py",
            "app/backend/db/users_repo.py",
            "app/backend/db/repository.py",
            "app/backend/db/user_messages_repo.py",
            "app/backend/db/signup_attempts_repo.py",
            "app/backend/main.py",
            "app/backend/config.py",
            "app/backend/rate_limit.py",
            "app/backend/signup_rate_limit.py",
        ):
            with self.subTest(path=path):
                result = self.evaluate(changed_files=[path])
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["protected_paths"], [path])

    def test_ordinary_application_work_is_still_ordinary(self):
        """The guard must not swallow the product. A gate that blocks everything blocks nothing:
        it would be routed around, and the security paths would lose the meaning of the refusal."""
        for path in (
            "app/backend/routes/channels.py",
            "app/backend/rag/retriever.py",
            "app/backend/rag/chunker.py",
            "app/backend/db/schema.py",
            "app/backend/services/supadata.py",
            "app/frontend/src/components/ChatArea.tsx",
            "app/frontend/src/lib/api.ts",
        ):
            with self.subTest(path=path):
                result = self.evaluate(changed_files=[path])
                self.assertEqual(result["protected_paths"], [])

    def test_application_tests_are_not_trust_root(self):
        """Only the factory's own detectors are protected; product tests stay ordinary work."""
        result = self.evaluate(changed_files=["app/backend/tests/test_messages.py"])
        self.assertEqual(result["protected_paths"], [])

    def test_legacy_archon_prompt_is_not_an_authority_anymore(self):
        path = ".archon/commands/dark-factory-engineering-plan.md"
        result = self.evaluate(changed_files=[path])
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["protected_paths"], [])

    def test_github_path_is_blocked(self):
        path = ".github/workflows/dark-factory.yml"
        result = self.evaluate(changed_files=[path])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["protected_paths"], [path])

    def test_env_file_is_blocked(self):
        self.assertEqual(self.evaluate(changed_files=["app/backend/.env.production"])["verdict"], "fail")

    # ---- trust-root maintenance lane -------------------------------------------------------
    # Identity is GitHub's resolved user object and repository association, never commit text.

    @staticmethod
    def human(login="maintainer", association="OWNER"):
        return {"login": login, "type": "User", "association": association}

    @staticmethod
    def bot(login="github-actions[bot]"):
        return {"login": login, "type": "Bot", "association": "CONTRIBUTOR"}

    @staticmethod
    def commit(sha="abc123def456", author=None, committer=None):
        user = {"login": "maintainer", "type": "User"}
        return {"sha": sha, "author": user if author is None else author,
                "committer": user if committer is None else committer}

    def test_factory_bot_pr_touching_trust_root_fails_closed(self):
        for path in ("scripts/factory_security.py", "factory_kernel/runtime.py", "FACTORY_RULES.md",
                     ".github/workflows/dark-factory-ci.yml", "app/backend/auth/tokens.py"):
            with self.subTest(path=path):
                result = self.evaluate(changed_files=[path], author=self.bot(), commits=[self.commit()])
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["authority"]["lane"], "autonomous")
                self.assertFalse(result["authority"]["protected_paths_permitted"])
                self.assertTrue(any(x["kind"] == "protected_path" for x in result["findings"]))

    def test_unknown_author_touching_trust_root_fails_closed(self):
        """Worktree mode and any caller that cannot prove identity stay on the autonomous lane."""
        result = self.evaluate(changed_files=["scripts/factory_security.py"])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["authority"]["lane"], "autonomous")

    def test_human_maintainer_pr_may_change_trust_root(self):
        result = self.evaluate(
            changed_files=["scripts/factory_security.py", "FACTORY_RULES.md", ".github/workflows/dark-factory-ci.yml"],
            author=self.human(), commits=[self.commit("1" * 40), self.commit("2" * 40)],
        )
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["authority"]["lane"], "human-maintenance")
        self.assertTrue(result["authority"]["protected_paths_permitted"])
        self.assertEqual(result["protected_paths"],
                         [".github/workflows/dark-factory-ci.yml", "FACTORY_RULES.md", "scripts/factory_security.py"])
        self.assertFalse(any(x["kind"] == "protected_path" for x in result["findings"]))

    def test_human_lane_accepts_member_and_collaborator_roles(self):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            with self.subTest(association=association):
                result = self.evaluate(changed_files=["FACTORY_RULES.md"],
                                       author=self.human(association=association), commits=[self.commit()])
                self.assertEqual(result["verdict"], "pass")

    def test_human_without_repository_role_stays_on_autonomous_lane(self):
        for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "NONE", ""):
            with self.subTest(association=association):
                result = self.evaluate(changed_files=["FACTORY_RULES.md"],
                                       author=self.human(association=association), commits=[self.commit()])
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["authority"]["lane"], "autonomous")

    def test_bot_with_owner_association_is_still_a_bot(self):
        result = self.evaluate(changed_files=["FACTORY_RULES.md"],
                               author={"login": "x[bot]", "type": "Bot", "association": "OWNER"},
                               commits=[self.commit()])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["authority"]["lane"], "autonomous")

    def test_human_lane_waives_nothing_but_the_protected_path_veto(self):
        """Secret scan and dependency policy still bite on the human lane."""
        marker = "-----BEGIN " + "PRIVATE KEY-----"  # split so this PR's own diff does not trip the scanner
        diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+" + marker + "\n"
        result = self.evaluate(changed_files=["scripts/factory_security.py", "x.py"], diff=diff,
                               author=self.human(), commits=[self.commit()])
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "secret" for x in result["findings"]))
        result = self.evaluate(
            changed_files=["FACTORY_RULES.md", m.BACKEND_MANIFEST],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nresend is required.\n",
            author=self.human(), commits=[self.commit()],
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "lockfile" for x in result["findings"]))

    def test_human_pr_carrying_a_bot_commit_into_trust_root_fails(self):
        """Second fence: a factory commit pushed onto a human's branch does not inherit the lane."""
        bot = {"login": "github-actions[bot]", "type": "Bot"}
        for role in ("author", "committer"):
            with self.subTest(role=role):
                commits = [self.commit("1" * 40), self.commit("2" * 40, **{role: bot})]
                result = self.evaluate(changed_files=["scripts/factory_security.py"],
                                       author=self.human(), commits=commits)
                self.assertEqual(result["verdict"], "fail")
                problems = [x for x in result["findings"] if x["kind"] == "protected_path_provenance"]
                self.assertEqual(len(problems), 1)
                self.assertEqual(problems[0]["commit"], "2" * 12)

    def test_human_pr_carrying_an_unresolved_commit_into_trust_root_fails(self):
        """A commit whose author GitHub cannot map to any account resolves to null."""
        commits = [self.commit("1" * 40), self.commit("2" * 40)]
        commits[0]["author"] = None
        result = self.evaluate(changed_files=["scripts/factory_security.py"],
                               author=self.human(), commits=commits)
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "protected_path_provenance" for x in result["findings"]))

    def test_human_pr_with_unknown_commit_provenance_fails_closed(self):
        result = self.evaluate(changed_files=["scripts/factory_security.py"], author=self.human(), commits=None)
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "protected_path_provenance" for x in result["findings"]))

    def test_bot_commits_are_ordinary_outside_the_trust_root(self):
        """The second fence guards the trust root only; factory product PRs are unaffected."""
        bot = {"login": "github-actions[bot]", "type": "Bot"}
        result = self.evaluate(changed_files=["app/backend/routes/channels.py"],
                               author=self.bot(), commits=[self.commit(author=bot, committer=bot)])
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["authority"]["lane"], "autonomous")

    def test_concatenated_json_pages_are_all_read(self):
        pages = m.concatenated_json('[{"sha": "a"}]\n[{"sha": "b"}, {"sha": "c"}]\n')
        self.assertEqual([c["sha"] for page in pages for c in page], ["a", "b", "c"])
        self.assertEqual(m.concatenated_json("   "), [])

    def test_github_actor_keeps_only_platform_identity(self):
        self.assertIsNone(m.github_actor(None))
        self.assertEqual(m.github_actor({"login": "x", "type": "User", "email": "x@y"}),
                         {"login": "x", "type": "User"})

    def test_backend_dependency_requires_lockfile(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nresend is required for transactional email.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "lockfile" for x in result["findings"]))

    def test_frontend_dependency_requires_lockfile(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST],
            head_frontend=self.frontend(deps={"react": "^18.3.1", "zod": "^4.0.0"}),
            body="## Dependency justification\nzod validates API payloads.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["path"] == m.FRONTEND_LOCK for x in result["findings"]))

    def test_added_dependency_requires_named_justification(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nNeeded for email.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("resend" in x["detail"] for x in result["findings"]))

    def test_added_dependency_with_lock_and_named_justification_passes(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nresend provides the required email transport.\n",
        )
        self.assertEqual(result["verdict"], "pass")

    def test_version_change_requires_justification(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            head_frontend=self.frontend(dev={"vite": "^6.0.0"}),
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("vite" in x["detail"] for x in result["findings"]))

    def test_removed_dependency_does_not_require_justification(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            base_frontend=self.frontend(deps={"react": "^18.3.1", "zod": "^4.0.0"}),
            head_frontend=self.frontend(deps={"react": "^18.3.1"}),
        )
        self.assertEqual(result["verdict"], "pass")

    def test_lockfile_only_refresh_requires_justification(self):
        result = self.evaluate(changed_files=[m.BACKEND_LOCK])
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "dependency_justification" for x in result["findings"]))

    def test_direct_python_url_dependency_is_blocked_and_source_is_redacted(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "thing @ https://user:secret@example.com/thing.whl"]),
            body="## Dependency justification\nthing is required.\n",
        )
        self.assertTrue(any(x["kind"] == "dependency_source" for x in result["findings"]))
        serialized = json.dumps(result)
        self.assertNotIn("user:secret", serialized)
        self.assertNotIn("example.com/thing.whl", serialized)

    def test_javascript_git_dependency_is_blocked(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            head_frontend=self.frontend(deps={"react": "^18.3.1", "thing": "git+https://github.com/x/y.git"}),
            body="## Dependency justification\nthing is required.\n",
        )
        self.assertTrue(any(x["kind"] == "dependency_source" for x in result["findings"]))
        self.assertNotIn("github.com/x/y.git", json.dumps(result))

    def test_private_key_added_line_is_blocked_without_echoing_secret(self):
        diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+-----BEGIN PRIVATE KEY-----\n"
        result = self.evaluate(diff=diff)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["secret_findings"], [{"kind": "private_key", "path": "x.py"}])
        self.assertNotIn("BEGIN PRIVATE KEY", json.dumps(result))

    def test_database_url_with_inline_credentials_is_blocked(self):
        diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+DB='postgresql://alice:supersecret@db.internal/app'\n"
        result = self.evaluate(diff=diff)
        self.assertTrue(any(x["kind"] == "secret" for x in result["findings"]))
        self.assertNotIn("alice:supersecret", json.dumps(result))

    def test_placeholder_secret_is_not_flagged_by_generic_rule(self):
        diff = "diff --git a/example.py b/example.py\n+++ b/example.py\n+API_KEY='your_api_key_placeholder'\n"
        self.assertEqual(self.evaluate(diff=diff)["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
