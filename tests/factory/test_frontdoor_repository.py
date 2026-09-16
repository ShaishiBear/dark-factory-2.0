"""Hosted preparation uses current protected facts and never releases a stale proposal."""
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.frontdoor_prepare import protected_repository_context, repository_context
from factory_kernel import frontdoor_http
from tests.factory.test_exploration_repository import GitHubFixture
from tests.factory import test_exploration_repository as repository_tests
from tests.factory import test_frontdoor_prepare as preparation_tests
from tests.factory import test_frontdoor_synthesis as synthesis_tests
from tests.factory.test_frontdoor_intent import OWNER


class PreparationContextTests(unittest.TestCase):
    def fixture(self, cls):
        fixture = cls()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def repository(self):
        fixture = self.fixture(repository_tests.ProtectedRepositoryTests)
        for name in ("MISSION.md", "README.md", "docs/API.md"):
            path = fixture.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Existing public product facts.\n", encoding="utf-8")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(fixture.root), *args], stderr=subprocess.STDOUT)
        git("add", ".")
        git("commit", "-m", "intent facts")
        return fixture.root, git

    def test_current_protected_facts_do_not_follow_pinned_or_dirty_service_checkout(self):
        root, git = self.repository()
        old = repository_context(root)
        (root / "docs/API.md").write_text("Newly shipped API behaviour.\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "product change")
        expected = repository_context(root)
        github = GitHubFixture(root)
        git("checkout", "--detach", old["commit"])
        (root / "docs/API.md").write_text("Uncommitted untrusted data.", encoding="utf-8")
        self.assertEqual(repository_context(root), old)
        self.assertEqual(protected_repository_context(github), expected)
        self.assertNotEqual(expected["commit"], old["commit"])

    def test_missing_nonregular_and_tampered_intake_files_refuse(self):
        root, _ = self.repository()
        for defect in ("missing", "symlink", "content"):
            with self.subTest(defect=defect):
                github = GitHubFixture(root)
                entry = next(row for row in github.tree["tree"] if row["path"] == "MISSION.md")
                if defect == "missing":
                    github.tree["tree"].remove(entry)
                elif defect == "symlink":
                    entry["mode"] = "120000"
                else:
                    github.blobs[entry["sha"]]["content"] = "dGFtcGVyZWQ="
                with self.assertRaises(IntentRefused):
                    protected_repository_context(github)

    def test_intake_total_is_bounded_even_when_each_file_fits(self):
        root, git = self.repository()
        for name in ("MISSION.md", "README.md", "docs/API.md"):
            (root / name).write_text("x" * 40000, encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "oversized intake")
        with self.assertRaisesRegex(IntentRefused, "bounded"):
            protected_repository_context(GitHubFixture(root))

    def test_drift_after_each_intent_worker_refuses_without_retry_or_draft(self):
        for stage in (1, 2):
            with self.subTest(stage=stage):
                fixture = self.fixture(preparation_tests.PreparationTests)
                original = fixture.preparer.context()
                changed = {**original, "commit": "b" * 40}
                fixture.preparer.context = Mock(side_effect=[original] * stage + [changed])
                result = fixture.prepare()
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["failure"], "IntentRefused")
                self.assertEqual(len(result["stages"]), stage)
                self.assertEqual(fixture.prepare(), result)
                self.assertEqual(fixture.provider.run.call_count, stage)
                self.assertIsNone(fixture.snapshot()["draft"])
                self.assertEqual(result["repository_commit"], original["commit"])
                self.assertEqual(result["repository_context_sha256"], sha256_value(original))

    def test_same_commit_changed_content_and_unavailable_currency_refuse(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                fixture = self.fixture(preparation_tests.PreparationTests)
                original = fixture.preparer.context()
                changed = {**original, "facts": "different facts"}
                fixture.preparer.context = Mock(side_effect=[original,
                    RuntimeError("unavailable") if unavailable else changed])
                result = fixture.prepare()
                self.assertEqual(result["state"], "failed")
                self.assertEqual(fixture.provider.run.call_count, 1)
                self.assertIsNone(fixture.snapshot()["draft"])

    def test_programme_drift_keeps_approval_and_refuses_review_without_second_spend(self):
        fixture = self.fixture(synthesis_tests.SynthesisTests)
        before = fixture.store.snapshot("citations", principal=OWNER)
        original = fixture.service.context()
        fixture.service.context = Mock(side_effect=[original, {**original, "commit": "b" * 40}])
        result = fixture.prepare()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["failure"], "IntentRefused")
        self.assertNotIn("review", result)
        self.assertEqual(fixture.prepare(), result)
        self.assertEqual(fixture.provider.run.call_count, 1)
        self.assertEqual(result["proposal_output"], fixture.proposal)
        self.assertEqual(result["repository_context_sha256"], sha256_value(original))
        self.assertEqual(fixture.store.snapshot("citations", principal=OWNER), before)

    def test_context_binding_is_durable_before_first_model_call(self):
        fixture = self.fixture(preparation_tests.PreparationTests)
        original_run = fixture.provider.run.side_effect
        def run(request):
            record = json.loads((fixture.preparer.directory / "citations-1.json").read_text())
            self.assertEqual(record["repository_commit"], fixture.preparer.context()["commit"])
            self.assertEqual(record["repository_context_sha256"], sha256_value(fixture.preparer.context()))
            return original_run(request)
        fixture.provider.run.side_effect = run
        self.assertEqual(fixture.prepare()["state"], "ready-for-review")

    def test_http_services_both_resolve_current_remote_context_on_each_read(self):
        fixture = self.fixture(preparation_tests.PreparationTests)
        token = fixture.store.directory / "owner-token"
        token.write_text("a" * 64)
        token.chmod(0o600)
        args = SimpleNamespace(enable_publication_requests=False, enable_programme_publication=False,
            enable_strategy_publication=False, hosted_preparation_identity=None, enable_preparation=True,
            token_file=token, state_dir=token.parent, owner=OWNER.identity, project="citations",
            app_login="factory[bot]", origin="https://factory.example.test", port=8765)
        config = SimpleNamespace(repository=fixture.store.repository, labels={}, provider=object())
        github = Mock(repository=fixture.store.repository)
        contexts = [{"commit": char * 40} for char in "ab"]
        with patch.object(frontdoor_http.argparse.ArgumentParser, "parse_args", return_value=args), \
                patch.object(frontdoor_http, "load_config", return_value=config), \
                patch.object(frontdoor_http, "GitHubClient", return_value=github), \
                patch.object(frontdoor_http, "verify_host_identity"), \
                patch.object(frontdoor_http, "IntentStore", return_value=fixture.store), \
                patch.object(frontdoor_http, "api_provider", return_value=fixture.provider), \
                patch.object(frontdoor_http, "protected_repository_context", side_effect=contexts) as remote, \
                patch.object(frontdoor_http, "make_server") as server:
            frontdoor_http.main()
            app = server.call_args.args[2]
            self.assertEqual(app.preparer.context(), contexts[0])
            self.assertEqual(app.synthesizer.context(), contexts[1])
            self.assertEqual([call.args for call in remote.call_args_list], [(github,), (github,)])
