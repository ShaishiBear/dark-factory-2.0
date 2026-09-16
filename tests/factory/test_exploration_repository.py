"""A hosted observation reads protected Git objects, not the service release checkout."""
import base64
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.exploration_repository import inspect_protected_repository, inspect_repository
from factory_kernel.frontdoor_intent import IntentRefused


def blob_oid(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class GitHubFixture:
    repository = "owner/product"

    def __init__(self, root):
        self.calls = []
        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args])
        commit = git("rev-parse", "HEAD").decode().strip()
        tree = git("rev-parse", "HEAD^{tree}").decode().strip()
        self.metadata = {"full_name": self.repository, "private": True, "default_branch": "main"}
        self.branch = {"protected": True, "commit": {"sha": commit, "commit": {"tree": {"sha": tree}}}}
        self.tree = {"sha": tree, "truncated": False, "tree": []}
        self.blobs = {}
        for line in git("ls-tree", "-r", "HEAD").decode().splitlines():
            mode, kind, oid, path = line.split()
            raw = git("cat-file", "blob", oid)
            self.tree["tree"].append({"path": path, "mode": mode, "type": kind, "sha": oid, "size": len(raw)})
            self.blobs[oid] = {"sha": oid, "size": len(raw), "encoding": "base64",
                               "content": base64.b64encode(raw).decode()}
        self.mutate = lambda path, row: row

    def json(self, args, *, timeout):
        assert args[0] == "api" and len(args) == 2 and 0 < timeout <= 15
        path = args[1]
        self.calls.append(path)
        if path == "repos/owner/product":
            row = self.metadata
        elif path.endswith("/branches/main"):
            row = self.branch
        elif "/git/trees/" in path:
            assert self.tree["sha"] in path
            row = self.tree
        elif "/git/blobs/" in path:
            row = self.blobs[path.rsplit("/", 1)[-1]]
        else:
            raise AssertionError(path)
        return self.mutate(path, deepcopy(row))


class ProtectedRepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = ["app/lookup.py", "app/view.tsx"]
        sources = {"app/lookup.py": "import bisect\nfrom collections import Counter\ndef lookup(): pass\n",
                   "app/view.tsx": 'import {useState} from "react";\n',
                   ".factory/architecture.json": '{}\n', "FACTORY_RULES.md": "Fresh independent qualification required.\n"}
        for name, source in sources.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        def git(*args):
            subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT)
        git("init")
        git("config", "user.name", "fixture")
        git("config", "user.email", "fixture@example.test")
        git("add", ".")
        git("commit", "-m", "fixture")
        self.gh = GitHubFixture(self.root)

    def inspect(self, **kwargs):
        return inspect_protected_repository(self.gh, self.paths, **kwargs)

    def test_remote_and_local_exact_commits_produce_identical_context(self):
        (self.root / self.paths[0]).write_text("import dirty_uncommitted_dependency\n")
        local = inspect_repository(self.root, self.paths)
        stop = Mock()
        remote = self.inspect(check_stop=stop)
        self.assertEqual(remote, local)
        self.assertEqual(remote["files"]["app/view.tsx"]["imports"], ["react"])
        self.assertEqual(remote["files"]["app/lookup.py"]["definitions"], ["lookup"])
        self.assertEqual(remote["proof_status"], "not-established")
        self.assertEqual(sum(p.endswith("/branches/main") for p in self.gh.calls), 2)
        self.assertEqual(self.gh.calls.count("repos/owner/product"), 2)
        self.assertGreaterEqual(stop.call_count, len(self.gh.calls))
        self.assertEqual(self.inspect(), remote)

    def test_incomplete_wrong_tree_missing_policy_and_nonregular_blobs_refuse(self):
        changes = [lambda tree: tree.update(truncated=True), lambda tree: tree.update(sha="f" * 40),
                   lambda tree: tree["tree"].pop(0),
                   lambda tree: tree["tree"].append(deepcopy(tree["tree"][0])),
                   lambda tree: tree["tree"][0].update(mode="120000"),
                   lambda tree: tree["tree"][0].update(type="tree"),
                   lambda tree: tree["tree"][0].update(size=True),
                   lambda tree: tree["tree"][0].update(sha="not-an-object")]
        for change in changes:
            self.gh = GitHubFixture(self.root)
            def mutate(path, row):
                if "/git/trees/" in path:
                    change(row)
                return row
            self.gh.mutate = mutate
            with self.subTest(change=change), self.assertRaises(IntentRefused):
                self.inspect()
            self.assertFalse(any("/git/blobs/" in path for path in self.gh.calls))

    def test_branch_repository_visibility_and_protection_changes_refuse(self):
        changes = [lambda row: row["commit"].update(sha="f" * 40),
                   lambda row: row["commit"]["commit"]["tree"].update(sha="f" * 40),
                   lambda row: row.update(protected=False)]
        for change in changes:
            self.gh = GitHubFixture(self.root)
            def mutate(path, row):
                if path.endswith("/branches/main") and self.gh.calls.count(path) == 2:
                    change(row)
                return row
            self.gh.mutate = mutate
            with self.subTest(change=change), self.assertRaisesRegex(IntentRefused, "protected|changed"):
                self.inspect()
        for field, value in (("private", False), ("full_name", "other/product"), ("default_branch", "other")):
            self.gh = GitHubFixture(self.root)
            def mutate(path, row):
                if path == "repos/owner/product" and self.gh.calls.count(path) == 2:
                    row[field] = value
                return row
            self.gh.mutate = mutate
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                self.inspect()

    def test_wrong_blob_content_hash_size_and_encoding_refuse(self):
        changes = [lambda row: row.update(sha="f" * 40), lambda row: row.update(size=row["size"] + 1),
                   lambda row: row.update(encoding="utf-8"), lambda row: row.update(content="!!invalid!!"),
                   lambda row: row.update(content=base64.b64encode(b"x" * row["size"]).decode())]
        for change in changes:
            self.gh = GitHubFixture(self.root)
            def mutate(path, row):
                if "/git/blobs/" in path:
                    change(row)
                return row
            self.gh.mutate = mutate
            with self.subTest(change=change), self.assertRaises(IntentRefused):
                self.inspect()

    def test_bad_paths_are_refused_before_any_remote_call(self):
        for paths in ([], [{}], ["app/../secrets.py"], ["app//lookup.py"], ["factory_kernel/runtime.py"],
                      ["app/lookup.py", "app/lookup.py"], ["app/.env"], ["app/lookup.py"] * 41):
            with self.subTest(paths=paths), self.assertRaises(IntentRefused):
                inspect_protected_repository(self.gh, paths)
        self.assertEqual(self.gh.calls, [])

    def test_oversized_source_and_total_refuse_before_blob_reads(self):
        for size in (50001, 200001):
            self.gh = GitHubFixture(self.root)
            for entry in self.gh.tree["tree"]:
                if entry["path"] in self.paths:
                    entry["size"] = size
            with self.assertRaisesRegex(IntentRefused, "analysis bound"):
                self.inspect()
            self.assertFalse(any("/git/blobs/" in path for path in self.gh.calls))

    def test_stop_and_expired_deadline_do_not_return_context(self):
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            self.inspect(check_stop=Mock(side_effect=RuntimeError("stopped")))
        self.assertEqual(self.gh.calls, [])
        with patch("factory_kernel.exploration_repository.time.monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(IntentRefused, "deadline"):
                self.inspect()
        self.assertEqual(self.gh.calls, [])

    def test_decoded_non_utf8_source_refuses_even_when_git_hash_is_correct(self):
        raw = b"\xff\xfe"
        oid = blob_oid(raw)
        for entry in self.gh.tree["tree"]:
            if entry["path"] == self.paths[0]:
                entry.update(sha=oid, size=len(raw))
        self.gh.blobs[oid] = {"sha": oid, "size": len(raw), "encoding": "base64", "content": base64.b64encode(raw).decode()}
        with self.assertRaisesRegex(IntentRefused, "UTF-8"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
