"""A publication discharges one path veto, with current consent required by old-base code."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ACTIVE_PATH
from factory_kernel.publication_client import current_currency
from tests.factory.test_factory_trust_root_authority import load_guard
from tests.factory.test_publication_admission import publication_facts


class PublicationGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = load_guard()
        self.facts = publication_facts()
        self.result = self.guard.evaluate(
            changed_files=[ACTIVE_PATH], base_backend="", head_backend="", base_frontend="",
            head_frontend="", diff="", body="", author=self.facts["pr"]["user"], commits=[])
        self.observation = {"lane": "programme-publication", "owner_currency": "observed-current",
                            "unattended_merge_eligible": False}
        self.observer = self.enterContext(patch("factory_kernel.publication_observation.observe_publication",
                                               return_value=self.observation))
        self.enterContext(patch.object(self.guard, "repository_name", return_value="ShaishiBear/dark-factory-2.0"))
        self.enterContext(patch.object(self.guard, "git_show", return_value=json.dumps(self.facts["head_input"])))
        self.commands = []
        def run(args, **_kwargs):
            self.commands.append(args)
            return SimpleNamespace(returncode=1 if args[1] == "cat-file" else 0,
                                   stdout="100644 blob " + "e" * 40 + "\t" + ACTIVE_PATH)
        self.enterContext(patch.object(self.guard, "run", side_effect=run))

    def judge(self, mode="trusted-base", changed=None):
        return self.guard.publication_verdict(deepcopy(self.result), mode=mode, pr="12",
                                              base="a" * 40, head="b" * 40,
                                              changed=changed or [ACTIVE_PATH])

    def test_only_old_base_requires_the_authenticated_current_owner_exchange(self):
        result = self.judge()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["authority"]["lane"], "programme-publication")
        self.assertFalse(result["authority"]["unattended_merge_eligible"])
        self.assertIs(self.observer.call_args.kwargs["current"], current_currency)
        self.assertIsNone(self.observer.call_args.kwargs["base_input"])
        self.assertEqual(self.observer.call_args.kwargs["file_mode"], "100644")
        self.assertEqual(self.judge("head")["verdict"], "pass")
        self.assertIsNone(self.observer.call_args.kwargs["current"])

    def test_old_base_cannot_consume_head_only_observation(self):
        self.observation["owner_currency"] = "not-assessed-by-head-check"
        self.assertEqual(self.judge()["verdict"], "fail")

    def test_all_other_findings_survive_and_unattended_merge_stays_disabled(self):
        for finding in ({"kind": "secret", "path": ACTIVE_PATH, "detail": "fixture"},
                        {"kind": "protected_path", "path": "scripts/guard.py", "detail": "fixture"},
                        {"kind": "ratchet_regression", "path": "floor", "detail": "fixture"}):
            self.result["findings"] = [{"kind": "protected_path", "path": ACTIVE_PATH}, finding]
            result = self.judge()
            self.assertEqual(result["findings"], [finding])
            self.assertEqual(result["verdict"], "fail")
            self.assertFalse(result["authority"]["unattended_merge_eligible"])

    def test_extra_paths_and_human_maintenance_never_use_the_publication_exception(self):
        self.assertEqual(self.judge(changed=[ACTIVE_PATH, "scripts/factory_security.py"]), self.result)
        self.result["authority"]["lane"] = "human-maintenance"
        self.assertEqual(self.judge(), self.result)
        self.observer.assert_not_called()

    def test_missing_locator_failed_provenance_and_unreadable_currency_keep_veto(self):
        self.observer.return_value = None
        self.assertEqual(self.judge()["verdict"], "fail")
        for error in (IntentRefused("stopped"), TimeoutError("unreadable"), ValueError("malformed")):
            self.observer.side_effect = error
            self.assertEqual(self.judge()["verdict"], "fail")

    def test_actual_entrypoints_call_the_adapter_and_secret_is_only_in_base_job(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "scripts/factory_security.py").read_text()
        for mode in ("head", "trusted-base"):
            self.assertIn('result = publication_verdict(result, mode="' + mode + '"', source)
        workflow = (root / ".github/workflows/dark-factory-trust-root.yml").read_text()
        judge = workflow.split("- name: Base-anchored security and trust-root guard", 1)[1].split("run: |", 1)[0]
        self.assertIn("FRONTDOOR_AGE_IDENTITY: ${{ secrets.FRONTDOOR_AGE_IDENTITY }}", judge)
        self.assertNotIn("FRONTDOOR_AGE_IDENTITY", (root / ".github/workflows/dark-factory-ci.yml").read_text())


if __name__ == "__main__":
    unittest.main()
