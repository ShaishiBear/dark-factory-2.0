"""Only a unique owner dispatch from protected main can validate publication input."""
from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_worker import authorize_job, compile_payload, refuse_replay, validated_manifest, main
from factory_kernel import publication_policy as policy
from tests.factory.test_publication_admission import publication_facts
from tests.factory import test_publication_observation as observation_fixtures


class PublicationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.facts = publication_facts()
        self.manifest = self.facts["manifest"]
        self.run = deepcopy(self.facts["run"])
        self.run["display_title"] = "programme-" + self.manifest["request_id"]
        self.event = {"inputs": {"request_id": self.manifest["request_id"], "ciphertext": "opaque"}}
        self.env = {"GITHUB_REPOSITORY": policy.REPOSITORY, "GITHUB_REPOSITORY_OWNER": policy.OWNER,
                    "GITHUB_ACTOR": policy.OWNER, "GITHUB_TRIGGERING_ACTOR": policy.OWNER,
                    "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_WORKFLOW_REF": f"{policy.REPOSITORY}/{policy.WORKFLOW_PATH}@refs/heads/main",
                    "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "234"}
        self.github = Mock()
        self.github.json.return_value = self.run

    def test_every_environment_identity_and_actual_platform_run_must_match(self):
        self.assertEqual(authorize_job(self.env, self.event, self.github), ("c" * 32, "a" * 40, 234))
        for key in self.env:
            env = {**self.env, key: "wrong"}
            with self.subTest(key=key), self.assertRaises((IntentRefused, ValueError)):
                authorize_job(env, self.event, self.github)
        self.run["actor"] = {"login": policy.OWNER, "type": "Bot"}
        with self.assertRaises(IntentRefused):
            authorize_job(self.env, self.event, self.github)

    def test_duplicate_truncated_expired_and_rerun_dispatches_refuse(self):
        run = {"id": 234, "run_attempt": 1, "display_title": "programme-" + "c" * 32}
        self.github.json.return_value = {"total_count": 1, "workflow_runs": [run]}
        refuse_replay(self.github, "c" * 32, 234, 1000, now=1001)
        for payload in ({"total_count": 2, "workflow_runs": [run]},
                        {"total_count": 2, "workflow_runs": [run, {**run, "id": 235}]},
                        {"total_count": 1, "workflow_runs": [{**run, "run_attempt": 2}]}):
            self.github.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(IntentRefused):
                refuse_replay(self.github, "c" * 32, 234, 1000, now=1001)
        for issued in (True, 1002, -2600):
            with self.subTest(issued=issued), self.assertRaises(IntentRefused):
                refuse_replay(self.github, "c" * 32, 234, issued, now=1000)

    def payload(self):
        payload = {key: self.manifest[key] for key in ("repository", "project", "request_id", "request_sha256",
                                                      "source_sha", "input", "input_sha256", "programme_sha256")}
        return {**payload, "schema": "dark-factory/publication-dispatch-v1", "issued_at": 1000}

    def test_encrypted_payload_cannot_add_private_wording_or_change_compiled_scope(self):
        value = compile_payload(self.payload(), "c" * 32, "a" * 40, 234)
        self.assertEqual(value, self.manifest)
        for field in ("private_intake", "approval_wording", "model", "budget"):
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                compile_payload({**self.payload(), field: "not public"}, "c" * 32, "a" * 40, 234)
        with self.assertRaises(IntentRefused):
            compile_payload(self.payload(), "f" * 32, "a" * 40, 234)

    def test_high_confidence_secret_refuses_before_public_artifact(self):
        from factory_kernel.canonical import sha256_value
        from factory_kernel.programme import compile_programme
        payload = deepcopy(self.payload())
        payload["input"]["spec"]["outcome"] = "Do not publish " + "ghp_" + "A" * 35
        payload["input"]["proposal"]["spec_sha256"] = sha256_value(payload["input"]["spec"])
        payload["input_sha256"] = sha256_value(payload["input"])
        payload["programme_sha256"] = compile_programme(payload["input"], repository=policy.REPOSITORY).sha256
        with self.assertRaisesRegex(IntentRefused, "secret"):
            compile_payload(payload, "c" * 32, "a" * 40, 234)

    def test_workflow_separates_validation_waiting_and_fresh_merge_identity(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / policy.WORKFLOW_PATH).read_text()
        self.assertIn("needs: validate-publication", text)
        self.assertIn("needs: [publish, wait-required-checks]", text)
        self.assertEqual(text.count("uses: actions/create-github-app-token@"), 2)
        wait = text.split("  wait-required-checks:\n", 1)[1].split("  merge:\n", 1)[0]
        self.assertNotIn("secrets.", wait)
        self.assertNotIn("DARK_FACTORY_APP_TOKEN", wait)
        self.assertEqual(text.count("persist-credentials: false"), 4)
        self.assertEqual(text.count("ref: ${{ github.sha }}"), 4)
        for forbidden in ("pull_request:", "--auto", "--admin", "OPENROUTER_API_KEY", "continue-on-error:",
                          "factory:needs-review", "ANTHROPIC_AUTH_TOKEN"):
            self.assertNotIn(forbidden, text)

    def test_later_jobs_consume_digest_bound_artifact_only_after_validation_succeeds(self):
        fixture = observation_fixtures.PublicationObservationTests()
        fixture.setUp()
        with patch("factory_kernel.publication_worker.download_archive", side_effect=lambda *_args: fixture.raw):
            def read():
                return validated_manifest(fixture.github, "c" * 32, "a" * 40, 234)
            self.assertEqual(read(), fixture.facts["manifest"])
            fixture.facts["jobs"][0]["status"] = "in_progress"
            with self.assertRaisesRegex(IntentRefused, "validation job"):
                read()
            fixture.facts["jobs"][0]["status"] = "completed"
            fixture.raw += b"altered"
            with self.assertRaisesRegex(IntentRefused, "digest"):
                read()

    def test_uncertain_merge_requests_containment_once_but_pre_effect_refusal_does_not(self):
        for effects in ([], [{"phase": "merge", "state": "uncertain"}]):
            with self.subTest(effects=effects), tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                event = Path(directory) / "event.json"
                event.write_text(json.dumps(self.event))
                env = {"GITHUB_EVENT_PATH": str(event), "RUNNER_TEMP": directory,
                       "PUBLICATION_PR": "12", "PUBLICATION_HEAD": "b" * 40}
                stack.enter_context(patch.dict(os.environ, env))
                stack.enter_context(patch("sys.argv", ["publication_worker", "merge"]))
                stack.enter_context(patch("factory_kernel.publication_worker.authorize_job", return_value=("c" * 32, "a" * 40, 234)))
                stack.enter_context(patch("factory_kernel.publication_worker.subprocess.check_output", return_value="a" * 40))
                stack.enter_context(patch("factory_kernel.publication_worker.GitHubClient", return_value=self.github))
                self.github.json.return_value = {"created_at": "2026-09-16T10:00:00Z"}
                stack.enter_context(patch("factory_kernel.publication_worker.validated_manifest", return_value=self.manifest))
                stack.enter_context(patch("factory_kernel.publication_worker.refuse_replay"))
                publisher = Mock(manifest=self.manifest)
                publisher.journal.record = {"effects": effects}
                publisher.merge.side_effect = IntentRefused("private diagnostic text must not leak")
                stack.enter_context(patch("factory_kernel.publication_worker.ProgrammePublisher", return_value=publisher))
                stop = stack.enter_context(patch("factory_kernel.publication_worker.request_stop"))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(main(), 1)
                self.assertNotIn("private diagnostic", output.getvalue())
                self.assertEqual(stop.call_count, 1 if effects else 0)


if __name__ == "__main__":
    unittest.main()
