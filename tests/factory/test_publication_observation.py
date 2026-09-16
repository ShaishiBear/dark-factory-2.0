"""Forged PR hints, truncated API inventories and unsafe archives cannot supply provenance."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import stat
import unittest
from unittest.mock import Mock
from zipfile import ZipFile, ZipInfo

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_observation import observe_publication, read_manifest_archive
from factory_kernel import publication_policy as policy
from tests.factory.test_publication_admission import publication_facts


def archive_bytes(manifest, *, name="manifest.json", extra=False, symlink=False):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        entry = ZipInfo(name)
        if symlink:
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, json.dumps(manifest))
        if extra:
            archive.writestr("extra.json", "{}")
    return buffer.getvalue()


class PublicationObservationTests(unittest.TestCase):
    def setUp(self):
        self.facts = publication_facts()
        self.raw = archive_bytes(self.facts["manifest"])
        self.facts["artifact"]["digest"] = "sha256:" + hashlib.sha256(self.raw).hexdigest()
        self.facts["artifact"]["size_in_bytes"] = len(self.raw)
        self.facts["pr"]["body"] = "<!-- dark-factory-publication:234:" + "c" * 32 + " -->"
        self.github = Mock(repository=policy.REPOSITORY)
        self.download = Mock(side_effect=lambda _github, _identity: self.raw)
        prefix = f"repos/{policy.REPOSITORY}"
        self.responses = {
            f"{prefix}/pulls/12": self.facts["pr"], f"{prefix}/actions/runs/234": self.facts["run"],
            f"{prefix}/actions/runs/234/attempts/1/jobs?per_page=100": {"total_count": 1, "jobs": self.facts["jobs"]},
            f"{prefix}/actions/runs/234/artifacts?per_page=100": {"total_count": 1, "artifacts": [self.facts["artifact"]]},
            f"{prefix}/pulls/12/commits?per_page=100": self.facts["commits"],
        }
        self.github.json.side_effect = lambda args: deepcopy(self.responses[args[1]])

    def observe(self, current=None):
        fields = {key: self.facts[key] for key in
                  ("base_sha", "head_sha", "changed_files", "file_mode", "base_input", "head_input", "now")}
        return observe_publication(self.github, pr_number=12, download=self.download, current=current, **fields)

    def test_real_archive_is_bound_to_platform_metadata_and_current_owner_read(self):
        manifest = self.facts["manifest"]
        current = Mock(return_value={"request_sha256": manifest["request_sha256"],
                                     "input_sha256": manifest["input_sha256"],
                                     "programme_sha256": manifest["programme_sha256"],
                                     "main_sha": self.facts["base_sha"], "decision": "current-owner-request"})
        self.assertEqual(self.observe(current)["owner_currency"], "observed-current")
        current.assert_called_once_with(manifest, "merge")
        self.github.run.assert_not_called()
        self.github.run_as_app.assert_not_called()
        self.assertEqual(self.observe()["owner_currency"], "not-assessed-by-head-check")

    def test_absent_locator_is_not_authority_and_duplicate_locator_refuses(self):
        self.facts["pr"]["body"] = "Arbitrary App PR"
        self.assertIsNone(self.observe())
        self.download.assert_not_called()
        marker = "<!-- dark-factory-publication:234:" + "c" * 32 + " -->"
        self.facts["pr"]["body"] = marker + "\n" + marker
        with self.assertRaises(IntentRefused):
            self.observe()

    def test_incomplete_or_duplicate_platform_inventories_refuse(self):
        for endpoint in [key for key in self.responses if "?per_page=" in key and "/commits?" not in key]:
            original = deepcopy(self.responses[endpoint])
            self.responses[endpoint]["total_count"] = 2
            with self.subTest(endpoint=endpoint), self.assertRaises(IntentRefused):
                self.observe()
            self.responses[endpoint] = original
        self.facts["pr"]["commits"] = 2
        with self.assertRaises(IntentRefused):
            self.observe()

    def test_digest_mismatch_and_locator_forgery_refuse(self):
        self.raw += b"changed"
        with self.assertRaisesRegex(IntentRefused, "digest"):
            self.observe()
        self.raw = archive_bytes(self.facts["manifest"])
        self.facts["pr"]["body"] = "<!-- dark-factory-publication:234:" + "f" * 32 + " -->"
        with self.assertRaisesRegex(IntentRefused, "locator"):
            self.observe()

    def test_archive_never_extracts_paths_and_requires_one_small_regular_manifest(self):
        for options in ({"name": "../manifest.json"}, {"extra": True}, {"symlink": True}):
            raw = archive_bytes(self.facts["manifest"], **options)
            with self.subTest(options=options), self.assertRaises(IntentRefused):
                read_manifest_archive(raw, {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()})
        raw = archive_bytes({"large": "x" * 250001})
        with self.assertRaises(IntentRefused):
            read_manifest_archive(raw, {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()})

    def test_fresh_currency_refusal_or_payload_drift_cannot_be_ignored(self):
        current = Mock(side_effect=IntentRefused("owner decisions changed"))
        with self.assertRaisesRegex(IntentRefused, "owner decisions"):
            self.observe(current)
        with self.assertRaisesRegex(IntentRefused, "current exact owner consent"):
            self.observe(Mock(return_value={"decision": "current-owner-request"}))


if __name__ == "__main__":
    unittest.main()
