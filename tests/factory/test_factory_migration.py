"""Archive integrity and fresh-start behavior; no network or live state."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from prepare_repository_migration import MigrationRefused, REQUIRED, prerequisites, verify_archive
from factory_kernel.frontdoor_intent import IntentStore, Principal
from factory_kernel.programme import ProgrammeRefused
from factory_kernel.programme_runtime import ProgrammeQueue
from factory_kernel.exploration_records import approved_scope


class MigrationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        identity = {"id": 123, "full_name": "owner/repo"}
        values = {name: [] for name in REQUIRED}
        values.update({"repository.json": identity, "repository-at-end.json": identity,
                       "git-restore-check.json": {"verified": True, "head": "a" * 40}})
        for name, value in values.items():
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")
        self.manifest = {"repository": "owner/repo", "errors": 0,
                         "consistent_cutover_snapshot": False, "files": []}
        self.refresh()

    def refresh(self):
        self.manifest["files"] = [{"path": p.name, "bytes": p.stat().st_size,
                                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                  for p in sorted(self.root.iterdir()) if p.name != "manifest.json"]
        self.write_manifest()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_integrity_valid_archive_still_reports_live_capture(self):
        result = verify_archive(self.root)
        self.assertEqual(result["repository_id"], 123)
        self.assertFalse(result["quiesced"])

    def test_modified_receipt_is_not_accepted(self):
        (self.root / "historical-receipt-index.json").write_text('["forged"]')
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_missing_bundle_is_not_accepted(self):
        (self.root / "repository.bundle").unlink()
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_path_traversal_absolute_and_windows_paths_are_refused(self):
        for name in ["../outside", "/etc/passwd", "C:/secret", "a\\secret", "a//b", "a/./b"]:
            with self.subTest(name=name):
                self.refresh()
                self.manifest["files"][0]["path"] = name
                self.write_manifest()
                with self.assertRaises(MigrationRefused):
                    verify_archive(self.root)

    def test_duplicate_paths_are_refused(self):
        self.manifest["files"].append(copy.deepcopy(self.manifest["files"][0]))
        self.write_manifest()
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_link_cannot_supply_archive_content(self):
        path = self.root / "issues.json"
        path.unlink()
        try:
            path.symlink_to(self.root / "pulls.json")
        except OSError:
            self.skipTest("symlink creation unavailable on this platform")
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_same_name_different_numeric_identity_is_refused(self):
        (self.root / "repository-at-end.json").write_text(json.dumps({"id": 456, "full_name": "owner/repo"}))
        self.refresh()
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_export_error_is_not_success(self):
        (self.root / "export-errors.json").write_text('[{"name":"missing artifact"}]')
        self.refresh()
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_omitting_required_records_is_refused(self):
        self.manifest["files"] = [row for row in self.manifest["files"] if row["path"] != "secret-names-only.json"]
        self.write_manifest()
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_duplicate_json_fields_are_refused(self):
        (self.root / "manifest.json").write_text('{"errors":0,"errors":1}')
        with self.assertRaises(MigrationRefused):
            verify_archive(self.root)

    def test_free_plan_remains_blocked_after_good_backup(self):
        blockers = prerequisites("free", True)
        self.assertTrue(any("private protected" in row for row in blockers))

    def test_paid_plan_alone_does_not_grant_readiness(self):
        blockers = prerequisites("pro", True)
        self.assertTrue(any("enforced protections" in row for row in blockers))
        self.assertTrue(any("protected review" in row for row in blockers))


class FreshMigrationStateTests(unittest.TestCase):
    def test_fresh_state_has_no_approval_and_refuses_paid_exploration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IntentStore(Path(directory), repository="owner/repo", owner="owner")
            state = store.snapshot("project", principal=Principal("owner", "owner"))
            self.assertEqual(state["project_version"], 0)
            self.assertEqual(state["approvals"], [])
            self.assertIsNone(state["draft"])
            with self.assertRaises(ValueError):
                approved_scope(store, [])

    def test_absent_active_programme_produces_no_current_programme(self):
        class NewDestination:
            repository = "owner/repo"
            def json(self, args):
                if args[1].endswith("/branches/main"):
                    return {"protected": True, "commit": {"sha": "a" * 40}}
                if "/git/trees/" in args[1]:
                    return {"truncated": False, "tree": []}
                raise AssertionError("unexpected access")
        self.assertIsNone(ProgrammeQueue(NewDestination(), "main").current())

    def test_private_repo_without_protection_is_still_refused(self):
        class UnprotectedDestination:
            repository = "owner/repo"
            def json(self, args):
                return {"protected": False}
        with self.assertRaises(ProgrammeRefused):
            ProgrammeQueue(UnprotectedDestination(), "main").current()


if __name__ == "__main__":
    unittest.main()
