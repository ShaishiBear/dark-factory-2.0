"""Authenticated refusal import crosses real receipt, retention, archive and store boundaries."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile, ZipInfo

from factory_kernel.canonical import canonical_bytes, sha256_bytes, sha256_value
from factory_kernel.evidence_retention import stage, source_binding
from factory_kernel.factory_feedback import FactoryFeedback, recorded_handoff
from factory_kernel.feedback_observation import observe_feedback, read_archive
from factory_kernel.feedback_receipt import FILE, marker, refusal_receipt
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ACTIVE_PATH, compile_programme
from factory_kernel.refusal import describe, refusal_record
from factory_kernel.runtime import KernelRuntime
from tests.factory import test_exploration as exploration
from tests.factory import test_frontdoor_http as http
from tests.factory.test_frontdoor_intent import OWNER, WORKER

REPO = "ShaishiBear/dark-factory-2.0"
BASE, HEAD = "a" * 40, "b" * 40
NOW = datetime(2026, 9, 16, 1, tzinfo=timezone.utc)
START, END = "2026-09-16T00:00:00Z", "2026-09-16T00:10:00Z"
KERNEL = "pr-17-" + "c" * 12


def zip_bytes(files):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        for module in ("test_frontdoor_intent", "test_frontdoor_programme"):
            patcher = patch("tests.factory." + module + ".REPO", REPO)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fixture = exploration.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.fixture.add()
        self.fixture.recommend()
        self.engine.handoff("citations", self.fixture.command({"proposal": self.fixture.fixture.request["proposal"]}), principal=OWNER)
        prepared = self.engine.prepare_handoff("citations", "lookup",
            expected_project_version=self.fixture.command({})["expected_project_version"], principal=OWNER, include_strategy=True)
        self.programme = compile_programme(prepared["input"], repository=REPO)
        self.request = {"run_id": 42, "attempt": 1, "pr": 17, "item_id": "snippet"}
        title, body = self.programme.render(self.programme.items[0], {"snippet": 12})
        self.issue = {"number": 12, "title": title, "body": body, "state": "open",
                      "user": {"login": "factory[bot]", "type": "Bot"}}
        self.github = Mock(repository=REPO)
        self.github.programme_issues.side_effect = lambda: [deepcopy(self.issue)]
        self.github.issue.side_effect = lambda number: {**deepcopy(self.issue), "author": {"login": "factory[bot]"}}
        repo = {"id": 99, "full_name": REPO}
        run = {"id": 42, "run_attempt": 1, "repository": repo, "head_repository": repo,
            "head_sha": BASE, "head_branch": "main", "path": ".github/workflows/dark-factory-worker.yml",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
            "created_at": START, "updated_at": END}
        self.responses = {
            "actions/runs/42": run, "actions/runs/42/attempts/1": deepcopy(run),
            "branches/main": {"protected": True, "commit": {"sha": BASE}},
            f"git/trees/{BASE}?recursive=1": {"truncated": False, "tree": [{"path": ACTIVE_PATH, "mode": "100644", "sha": "c" * 40}]},
            "git/blobs/" + "c" * 40: {"encoding": "base64", "size": len(canonical_bytes(prepared["input"])),
                "content": base64.b64encode(canonical_bytes(prepared["input"])).decode()},
            "pulls/17": {"number": 17, "state": "open", "merged": False,
                "head": {"sha": HEAD, "repo": repo}, "base": {"sha": BASE, "ref": "main", "repo": repo},
                "user": {"login": "factory[bot]", "type": "Bot"}, "body": "Fixes #12"},
            "actions/runs/42/attempts/1/jobs?per_page=100": {"total_count": 1, "jobs": [{
                "name": "dispatch", "run_id": 42, "head_sha": BASE, "status": "completed",
                "steps": [{"name": name, "status": "completed", "conclusion": "success"} for name in
                    ("Stage bounded dispatch evidence", "Retain dispatch evidence", "Retain dispatch evidence index")]}]},
        }
        self.github.json.side_effect = lambda args: deepcopy(self.responses[args[1].removeprefix("repos/" + REPO + "/")])
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.artifacts = self.root / "runs" / KERNEL / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.refusal = refusal_record(describe("code_holdout", RuntimeError("candidate violates constraint")),
            pr=17, head=HEAD, base=BASE, stage="code_holdout", timestamp="2026-09-16T00:05:00Z")
        (self.artifacts / "validation-refusal.json").write_bytes(canonical_bytes(self.refusal))
        self.environment = {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": BASE,
                            "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
        self.receipt = self.mint()
        (self.artifacts / FILE).write_bytes(canonical_bytes(self.receipt))
        stage(runs=self.root / "runs", destination=self.root / "retained", index_directory=self.root / "index",
            binding=source_binding(repository=REPO, run_id=42, attempt=1, source_revision=BASE, phase="dispatch"),
            checkout_observation={"checkout_revision": BASE, "trust_root_tree_sha256": "d" * 64, "python_version": "3.12.14"})
        self.files = {p.relative_to(self.root / "retained").as_posix(): p.read_bytes()
                      for p in (self.root / "retained").rglob("*.json")}
        self.index = (self.root / "index" / "retention-index.json").read_bytes()
        self.responses["issues/17/comments?per_page=100"] = [{"id": 71,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "created_at": "2026-09-16T00:05:01Z", "updated_at": "2026-09-16T00:05:01Z",
            "body": marker(self.receipt) + "\nFactory validation refused."}]
        self.packages = {}
        self.repack()
        self.download = Mock(side_effect=lambda github, identity: self.packages[identity])
        self.service = FactoryFeedback(self.engine, self.github)

    def mint(self):
        return refusal_receipt(self.github, default_branch="main", issue_number=12, refusal=self.refusal,
            artifacts=self.artifacts, kernel_revision=BASE, environment=self.environment)

    def repack(self):
        rows = []
        for identity, kind, files in ((1, "evidence-index", {"retention-index.json": self.index}), (2, "evidence", self.files)):
            raw = zip_bytes(files)
            self.packages[identity] = raw
            row = {"id": identity, "name": f"dark-factory-{kind}-dispatch-42-1", "digest": "sha256:" + sha256_bytes(raw),
                "size_in_bytes": len(raw), "expired": False, "created_at": "2026-09-16T00:09:00Z",
                "expires_at": "2026-12-01T00:00:00Z", "workflow_run": {"id": 42, "head_sha": BASE,
                    "head_branch": "main", "repository_id": 99, "head_repository_id": 99}}
            rows.append(row)
            self.responses[f"actions/artifacts/{identity}"] = row
        self.responses["actions/runs/42/artifacts?per_page=100"] = {"total_count": 2, "artifacts": rows}

    def observe(self):
        return observe_feedback(self.github, self.request, download=self.download, now=NOW)

    def import_outcome(self, command=None, principal=OWNER):
        with patch("factory_kernel.factory_feedback.observe_feedback", side_effect=lambda github, request:
                   observe_feedback(github, request, download=self.download, now=NOW)):
            return self.service.import_outcome("citations", command or self.fixture.command(self.request), principal=principal)

    def test_real_producer_retention_archive_and_import_preserve_claims_and_spend(self):
        before = self.engine.records.read("citations", OWNER)[0]
        result = self.import_outcome()
        observation = result["outcome"]["observation"]
        self.assertEqual(observation["receipt"], self.receipt)
        self.assertEqual(observation["cause"], "unresolved")
        self.assertEqual(observation["qualification_status"], "UNPROVEN")
        self.assertFalse(observation["proof_reuse_allowed"])
        self.assertEqual(observation["independent_strategy_rejection"], "not-established")
        after = self.engine.records.read("citations", OWNER)[0]
        for key in ("sessions", "claims", "budgets", "feedback"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(len(after["factory_outcomes"]), 1)
        self.github.run.assert_not_called()
        self.github.run_as_app.assert_not_called()

    def test_forged_issuer_run_revision_job_and_artifact_metadata_refuse(self):
        cases = [("actions/runs/42", ["path"], ".github/workflows/other.yml"),
            ("actions/runs/42", ["run_attempt"], 2), ("actions/runs/42", ["status"], "in_progress"),
            ("actions/runs/42", ["head_repository", "id"], 100),
            ("branches/main", ["protected"], False), ("branches/main", ["commit", "sha"], "e" * 40),
            ("pulls/17", ["head", "sha"], "e" * 40), ("pulls/17", ["base", "sha"], "e" * 40),
            ("pulls/17", ["user", "login"], "attacker"), ("pulls/17", ["body"], "Fixes #13"),
            ("pulls/17", ["merged"], True),
            ("actions/runs/42/attempts/1/jobs?per_page=100", ["jobs", 0, "steps", 1, "conclusion"], "failure"),
            ("actions/runs/42/artifacts?per_page=100", ["total_count"], 3),
            ("actions/runs/42/artifacts?per_page=100", ["artifacts", 0, "expired"], True),
            ("actions/runs/42/artifacts?per_page=100", ["artifacts", 0, "digest"], "sha256:" + "0" * 64),
            ("issues/17/comments?per_page=100", [0, "user", "login"], "attacker"),
            ("issues/17/comments?per_page=100", [0, "updated_at"], END),
            ("issues/17/comments?per_page=100", [0, "body"], "copied refusal without receipt")]
        original = deepcopy(self.responses)
        for endpoint, keys, value in cases:
            self.responses = deepcopy(original)
            target = self.responses[endpoint]
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(endpoint=endpoint, keys=keys), self.assertRaises(IntentRefused):
                self.observe()

    def test_incomplete_or_mixed_packages_never_enter_ledger(self):
        before = self.engine.records.read("citations", OWNER)[0]
        original = deepcopy(self.files)
        for action in ("missing", "tampered", "extra", "receipt-for-another-programme"):
            self.files = deepcopy(original)
            key = KERNEL + "/artifacts/validation-refusal.json"
            if action == "missing":
                del self.files[key]
            elif action == "tampered":
                self.files[key] += b" "
            elif action == "extra":
                self.files["../escape"] = b"{}"
            else:
                self.files[KERNEL + "/artifacts/" + FILE] = canonical_bytes({**self.receipt, "programme_sha256": "e" * 64})
            self.repack()  # Even fresh valid platform ZIP digests cannot override index/receipt binding.
            with self.subTest(action=action), self.assertRaises(IntentRefused):
                self.import_outcome()
            self.assertEqual(before, self.engine.records.read("citations", OWNER)[0])

    def test_race_after_download_refuses(self):
        def changed(github, identity):
            self.responses["pulls/17"]["head"]["sha"] = "f" * 40
            return self.packages[identity]
        self.download.side_effect = changed
        with self.assertRaises(IntentRefused):
            self.import_outcome()

    def test_rehashed_foreign_receipt_cannot_borrow_platform_authority(self):
        original = deepcopy(self.receipt)
        for field, value in (("programme_sha256", "e" * 64), ("item_id", "other"),
                ("run_id", 43), ("run_attempt", 2), ("head_sha", "f" * 40), ("issue", 13),
                ("cause", "strategy-failure"), ("qualification_status", "QUALIFIED"), ("proof_reuse_allowed", True)):
            self.receipt = {**original, field: value}
            name = KERNEL + "/artifacts/" + FILE
            self.files[name] = canonical_bytes(self.receipt)
            index = json.loads(self.index)
            for row in index["files"]:
                row.update(bytes=len(self.files[row["path"]]), sha256=sha256_bytes(self.files[row["path"]]))
            index["retained_bytes"] = sum(row["bytes"] for row in index["files"])
            self.index = canonical_bytes(index)
            self.responses["issues/17/comments?per_page=100"][0]["body"] = marker(self.receipt)
            self.repack()
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.observe()

    def test_receipt_must_be_published_by_actual_failure_handler(self):
        runtime = KernelRuntime.__new__(KernelRuntime)
        runtime.github, runtime.repo_root = self.github, self.root
        runtime.config = SimpleNamespace(default_branch="main", labels={"needs_review": "review", "needs_fix": "fix"})
        runtime._git = Mock(return_value=BASE)
        with patch.dict("os.environ", self.environment), patch("factory_kernel.runtime._utc_now", return_value=self.refusal["timestamp"]):
            runtime._record_validation_failure(17, 12, RuntimeError("candidate violates constraint"),
                stage="code_holdout", paths=SimpleNamespace(artifacts=self.artifacts), head=HEAD, base=BASE)
        receipt = json.loads((self.artifacts / FILE).read_bytes())
        self.assertEqual(receipt["programme_sha256"], self.programme.sha256)
        self.assertEqual(receipt["refusal_sha256"], sha256_bytes((self.artifacts / "validation-refusal.json").read_bytes()))
        self.assertIn(marker(receipt), self.github.comment_pr.call_args.args[1])
        self.github.add_pr_label.assert_called_with(17, "fix")

    def test_missing_receipt_authority_preserves_existing_repair_route(self):
        runtime = KernelRuntime.__new__(KernelRuntime)
        runtime.github, runtime.repo_root = self.github, self.root
        runtime.config = SimpleNamespace(default_branch="main", labels={"needs_review": "review", "needs_fix": "fix"})
        runtime._git = Mock(return_value=BASE)
        with patch.dict("os.environ", {**self.environment, "GITHUB_SHA": "f" * 40}):
            runtime._record_validation_failure(17, 12, RuntimeError("outage"), stage=None,
                paths=SimpleNamespace(artifacts=self.artifacts), head=HEAD, base=BASE)
        self.assertNotIn("dark-factory-feedback:", self.github.comment_pr.call_args.args[1])
        self.github.add_pr_label.assert_called_with(17, "fix")

    def test_unrecorded_handoff_and_incomplete_index_are_refused(self):
        with self.assertRaisesRegex(IntentRefused, "no exact recorded"):
            recorded_handoff([], self.programme, "lookup")
        changed = self.programme.to_input()
        changed["proposal"]["items"][0]["id"] = "different"
        with self.engine.records.store._locked("citations") as path:
            events = self.engine.records.store._read(path)
        with self.assertRaisesRegex(IntentRefused, "no exact recorded"):
            recorded_handoff(events, compile_programme(changed, repository=REPO), "lookup")
        self.responses["actions/runs/42/artifacts?per_page=100"]["artifacts"].pop()
        with self.assertRaises(IntentRefused):
            self.import_outcome()

    def test_stop_activated_during_download_cannot_append(self):
        before = self.engine.records.read("citations", OWNER)[0]
        def stopped(github, identity):
            self.fixture.stop.side_effect = IntentRefused("stopped during observation")
            return self.packages[identity]
        self.download.side_effect = stopped
        with self.assertRaisesRegex(IntentRefused, "stopped during"):
            self.import_outcome()
        self.assertEqual(before, self.engine.records.read("citations", OWNER)[0])

    def test_same_command_replays_once_new_key_cannot_reimport(self):
        command = self.fixture.command(self.request)
        first = self.import_outcome(command)
        self.assertEqual(self.import_outcome(command), first)
        with self.assertRaisesRegex(IntentRefused, "already imported"):
            self.import_outcome()
        self.responses["pulls/17"]["head"]["sha"] = "f" * 40
        with self.assertRaises(IntentRefused):
            self.import_outcome(command)

    def test_stale_cas_scope_foreign_session_and_missing_handoff_refuse(self):
        command = self.fixture.command(self.request)
        command["expected_project_version"] -= 1
        with self.assertRaisesRegex(IntentRefused, "stale project"):
            self.import_outcome(command)
        self.fixture.open("unrelated")
        with self.assertRaisesRegex(IntentRefused, "another exploration"):
            self.import_outcome(self.fixture.command(self.request, session="unrelated"))
        self.engine.records.store.execute("citations", {"idempotency_key": "new-intent",
            "expected_project_version": self.fixture.command({})["expected_project_version"],
            "operation": "record-intent", "payload": {"wording": "A changed owner outcome"}}, principal=OWNER)
        with self.assertRaisesRegex(IntentRefused, "approval"):
            self.import_outcome()

    def test_authorization_stop_and_caller_claimed_cause_refuse(self):
        with self.assertRaises(IntentRefused):
            self.import_outcome(principal=WORKER)
        self.download.assert_not_called()
        self.fixture.stop.side_effect = IntentRefused("stopped")
        with self.assertRaisesRegex(IntentRefused, "stopped"):
            self.import_outcome()
        self.download.assert_not_called()
        self.fixture.stop.side_effect = None
        for key in ("cause", "claim_ids", "artifact_path", "principal"):
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                self.import_outcome(self.fixture.command({**self.request, key: "forged"}))

    def test_receipt_producer_refuses_wrong_checkout_or_unadmitted_issue(self):
        self.environment["GITHUB_SHA"] = "e" * 40
        with self.assertRaises(ValueError):
            self.mint()
        self.environment["GITHUB_SHA"] = BASE
        self.issue["body"] += " edited"
        with self.assertRaises(ValueError):
            self.mint()

    def test_authenticated_http_import_and_auth_refusals_use_real_service(self):
        fixture = http.FrontDoorHTTPTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.app.store = self.engine.records.store
        fixture.app.github = self.github
        fixture.app.explorer = Mock(engine=self.engine)
        for overrides, status in (({"HTTP_AUTHORIZATION": ""}, "401 Unauthorized"),
                                  ({"HTTP_ORIGIN": "https://evil.example"}, "403 Forbidden")):
            self.assertEqual(fixture.call("/api/exploration/import-feedback", body={}, **overrides)["status"], status)
        self.download.assert_not_called()
        with patch("factory_kernel.factory_feedback.observe_feedback", side_effect=lambda github, request:
                   observe_feedback(github, request, download=self.download, now=NOW)):
            response = fixture.call("/api/exploration/import-feedback", body=self.fixture.command(self.request))
        self.assertEqual(response["status"], "200 OK", response["body"])
        self.assertEqual(response["json"]["outcome"]["observation"]["cause"], "unresolved")


class ArchiveTests(unittest.TestCase):
    def test_duplicate_symlink_traversal_and_expansion_are_rejected(self):
        for name, mode, raw, duplicate in (("../bad", 0, b"{}", False),
                ("bad", stat.S_IFLNK, b"{}", False), ("bad", 0, b"x" * 250001, False),
                ("bad", 0, b"{}", True)):
            buffer = BytesIO()
            with ZipFile(buffer, "w") as archive:
                info = ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, raw)
                if duplicate:
                    archive.writestr(info, raw)
            raw = buffer.getvalue()
            with self.subTest(name=name, mode=mode, duplicate=duplicate), self.assertRaises(IntentRefused):
                read_archive(raw, {"digest": "sha256:" + sha256_bytes(raw)})


if __name__ == "__main__":
    unittest.main()
