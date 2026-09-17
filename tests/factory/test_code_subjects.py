"""Exact source subjects: byte spans in exact blobs, resolved in C03 order (LINE_LEVEL_CLAIMS 2).

Negative cases the specification names: stale/moved span, changed blob, parser disagreement
(unparsable source stays exact bytes with unknown analysis), traversal, split UTF-8, empty
interval without an insertion anchor, symlinks never followed, and coverage that unions
overlapping bindings and excludes comments only through the registered parser.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from factory_kernel.canonical import sha256_bytes
from factory_kernel.code_subjects import (
    CodeSubject, GitTreeReader, MemoryTreeReader, Refusal, blob_oid, index_source, map_change, resolve_span,
    safe_path, trace_coverage,
)

SOURCE = b'''"""Module."""


def lookup(items, key):
    # linear scan
    for item in items:
        if item.key == key:
            return item
    return None


class Store:
    def get(self, key):
        return lookup(self.items, key)
'''


def span_of(raw: bytes, needle: bytes, occurrence: int = 0) -> tuple[int, int]:
    start = -1
    for _ in range(occurrence + 1):
        start = raw.index(needle, start + 1)
    return start, start + len(needle)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.index = index_source(MemoryTreeReader({"pkg/mod.py": SOURCE, "notes.txt": b"plain \xc3\xa9\n"}, repository_id="r"))

    def request(self, path="pkg/mod.py", raw=SOURCE, **span):
        return {"path": path, "source_bytes_sha256": sha256_bytes(raw), **span}

    def test_exact_ast_correspondence_for_an_expression_and_a_function(self):
        start, end = span_of(SOURCE, b"item.key == key")
        subject = resolve_span(self.index, self.request(byte_start=start, byte_end=end))
        self.assertIsInstance(subject, CodeSubject)
        self.assertEqual((subject.text, subject.ast_kind, subject.symbol_path, subject.dependency_coverage),
                         ("item.key == key", "Compare", "lookup", "complete-for-profile"))
        self.assertEqual(subject.language, "python")
        self.assertEqual(subject.parser_id, "cpython-ast")
        self.assertIn("lookup", subject.context_refs)
        record = subject.to_dict()
        self.assertEqual((record["schema"], record["byte_start"], record["byte_end"]), ("dark-factory/code-subject", start, end))
        self.assertEqual(record["blob_oid"], blob_oid(SOURCE))
        self.assertEqual(len(subject.identity()), 64)
        # One operator inside its expression: exact bytes, partial AST correspondence.
        op_start = SOURCE.index(b"==")
        operator = resolve_span(self.index, self.request(byte_start=op_start, byte_end=op_start + 2))
        self.assertEqual((operator.text, operator.ast_kind, operator.dependency_coverage, operator.symbol_path),
                         ("==", None, "partial", "lookup"))
        whole = span_of(SOURCE, b"def lookup(items, key):")
        function = resolve_span(self.index, self.request(byte_start=whole[0], byte_end=SOURCE.index(b"\n\n\nclass")))
        self.assertEqual((function.ast_kind, function.symbol_path), ("FunctionDef", "lookup"))
        self.assertIsNotNone(function.ast_fingerprint)

    def test_refusals_follow_the_c03_order_and_name_one_cause(self):
        good = self.request(byte_start=4, byte_end=10)
        cases = (
            ({**good, "path": "../secret"}, "unsafe_path"),
            ({**good, "path": "/etc/passwd"}, "unsafe_path"),
            ({**good, "path": "pkg\\mod.py"}, "unsafe_path"),
            ({**good, "path": "pkg/other.py"}, "unknown_path"),
            ({**good, "source_bytes_sha256": "0" * 64}, "source_mismatch"),
            ({**good, "byte_end": 10_000}, "invalid_span"),
            ({**good, "byte_start": -1}, "invalid_span"),
            ({**good, "byte_start": True}, "invalid_span"),
            ({**good, "byte_start": 4, "byte_end": 4}, "invalid_span"),
            ({**good, "byte_start": 9, "byte_end": 4}, "invalid_span"),
            ({**good, "insertion_anchor": 1}, "invalid_span"),
        )
        for request, code in cases:
            with self.subTest(code=code, request=request):
                result = resolve_span(self.index, request)
                self.assertIsInstance(result, Refusal)
                self.assertEqual(result.reason_codes, (code,))
        # A stale hash outranks a bad span: the requester is told the blob moved, not the offset.
        stale = resolve_span(self.index, {**good, "source_bytes_sha256": "0" * 64, "byte_end": 10_000})
        self.assertEqual(stale.reason_codes, ("source_mismatch",))
        self.assertEqual(resolve_span(self.index, "nonsense").reason_codes, ("invalid_span",))

    def test_split_utf8_is_refused_and_insertion_anchor_is_the_only_empty_interval(self):
        raw = b"plain \xc3\xa9\n"
        start = raw.index(b"\xc3")
        ok = resolve_span(self.index, self.request("notes.txt", raw, byte_start=start, byte_end=start + 2))
        self.assertEqual((ok.text, ok.language, ok.parser_id, ok.dependency_coverage), ("\u00e9", "unknown", None, "unknown"))
        split = resolve_span(self.index, self.request("notes.txt", raw, byte_start=start + 1, byte_end=start + 2))
        self.assertEqual(split.reason_codes, ("invalid_utf8_boundary",))
        split = resolve_span(self.index, self.request("notes.txt", raw, byte_start=start, byte_end=start + 1))
        self.assertEqual(split.reason_codes, ("invalid_utf8_boundary",))
        anchor = resolve_span(self.index, self.request("notes.txt", raw, byte_start=6, byte_end=6, insertion_anchor=True))
        self.assertEqual((anchor.text, anchor.insertion_anchor, anchor.byte_start), ("", True, 6))

    def test_unparsable_python_keeps_exact_bytes_with_unknown_analysis(self):
        broken = b"def (:\n"
        index = index_source(MemoryTreeReader({"x.py": broken}))
        subject = resolve_span(index, {"path": "x.py", "byte_start": 0, "byte_end": 3, "source_bytes_sha256": sha256_bytes(broken)})
        self.assertEqual((subject.text, subject.ast_kind, subject.dependency_coverage, subject.parser_id), ("def", None, "unknown", "cpython-ast"))

    def test_symlinks_submodules_and_oversized_blobs_are_gaps_never_followed(self):
        reader = MemoryTreeReader({"link": b"../../etc/passwd", "sub": b"", "big.py": b"x" * 2_000_001, "ok.py": b"x = 1\n"},
                                  modes={"link": "120000", "sub": "160000"})
        index = index_source(reader)
        self.assertEqual(set(index.files), {"ok.py"})
        self.assertEqual({g["path"]: g["reason"] for g in index.gaps},
                         {"link": "symlink_not_followed", "sub": "submodule_not_indexed", "big.py": "blob_over_bound"})
        result = resolve_span(index, {"path": "link", "byte_start": 0, "byte_end": 1, "source_bytes_sha256": sha256_bytes(b"../../etc/passwd")})
        self.assertEqual(result.reason_codes, ("not_a_blob",))
        for bad in ("a//b", "./a", "a/", "", None, "a\x00b", "C:/x", "a/../b"):
            self.assertIsNone(safe_path(bad), bad)
        self.assertEqual(safe_path("a/b.py"), "a/b.py")


class GitReaderTests(unittest.TestCase):
    def test_exact_committed_objects_not_the_working_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@example"}

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            (repo / "pkg").mkdir()
            (repo / "pkg" / "mod.py").write_bytes(SOURCE)
            git("add", "pkg/mod.py")
            git("commit", "-q", "-m", "one")
            revision = git("rev-parse", "HEAD")
            # Working-tree edits after the commit must be invisible to the exact reader.
            (repo / "pkg" / "mod.py").write_bytes(SOURCE + b"# dirty\n")
            index = index_source(GitTreeReader(repo, revision, repository_id="r"))
            self.assertEqual(index.files["pkg/mod.py"].raw, SOURCE)
            self.assertEqual(index.files["pkg/mod.py"].blob_oid, blob_oid(SOURCE))
            self.assertEqual(index.tree_oid, git("rev-parse", "HEAD^{tree}"))
            self.assertEqual(index.gaps, ())


class ChangeMapTests(unittest.TestCase):
    def test_exact_blob_match_moved_span_changed_span_and_unresolved_are_distinct(self):
        new_source = SOURCE.replace(b"item.key == key", b"item.key <= key").replace(b'"""Module."""\n', b'"""Module."""\nimport x\n')
        old = index_source(MemoryTreeReader({"pkg/mod.py": SOURCE, "same.py": b"a = 1\n", "gone.py": b"x\n"}))
        new = index_source(MemoryTreeReader({"pkg/mod.py": new_source, "same.py": b"a = 1\n", "added.py": b"y\n"}))
        change = map_change(old, new)
        self.assertEqual({p: c.status for p, c in change.files.items()},
                         {"pkg/mod.py": "modified", "same.py": "unchanged", "gone.py": "deleted", "added.py": "added"})
        self.assertEqual(change.map_span("same.py", (0, 5)).status, "identical")
        self.assertEqual(change.map_span("gone.py", (0, 1)).status, "deleted")
        # The class body did not change: its span maps with the insertion shift, bytes identical.
        old_span = span_of(SOURCE, b"return lookup(self.items, key)")
        mapped = change.map_span("pkg/mod.py", old_span)
        self.assertEqual(mapped.status, "mapped")
        self.assertEqual(new_source[mapped.new_span[0]:mapped.new_span[1]], b"return lookup(self.items, key)")
        # The compared operator changed: no silent carry-over, an explicit changed mapping.
        changed = change.map_span("pkg/mod.py", span_of(SOURCE, b"item.key == key"))
        self.assertEqual((changed.status, changed.new_span), ("changed", None))
        self.assertEqual(change.map_span("nowhere.py", (0, 1)).status, "unresolved")
        self.assertEqual(change.map_span("added.py", (0, 1)).status, "unresolved")

    def test_line_numbers_looking_unchanged_never_make_a_changed_blob_current(self):
        old = index_source(MemoryTreeReader({"a.py": b"x = 1\n"}))
        new = index_source(MemoryTreeReader({"a.py": b"x = 2\n"}))
        mapping = map_change(old, new).map_span("a.py", (0, 5))
        self.assertEqual(mapping.status, "changed")


class CoverageTests(unittest.TestCase):
    def test_union_excludes_comments_only_through_the_parser_and_keeps_unknowns_visible(self):
        old = index_source(MemoryTreeReader({"a.py": b"x = 1\n", "b.txt": b"old\n"}))
        new_py = b"x = 1\n# explain\ny = 2\nz = 3\n"
        new = index_source(MemoryTreeReader({"a.py": new_py, "b.txt": b"old\nnew line\n"}))
        change = map_change(old, new)
        y_span = span_of(new_py, b"y = 2\n")
        z_span = span_of(new_py, b"z = 3\n")
        # Two observed bindings overlap on `y`; a proposed binding covers `z`; the comment is excluded.
        report = trace_coverage(change, new, proposed_bindings=[{"path": "a.py", "byte_start": z_span[0], "byte_end": z_span[1]}],
                                observed_bindings=[{"path": "a.py", "byte_start": y_span[0], "byte_end": y_span[1]},
                                                   {"path": "a.py", "byte_start": y_span[0], "byte_end": y_span[0] + 3}])
        a = report.per_file["a.py"]
        self.assertEqual(a["excluded"], len(b"# explain\n"))
        self.assertEqual(a["observed"], len(b"y = 2\n"), "overlapping bindings counted once")
        self.assertEqual(a["proposed_only"], len(b"z = 3\n"))
        self.assertEqual(a["unresolved"], 0)
        b = report.per_file["b.txt"]
        self.assertEqual((b["excluded"], b["unresolved"]), (0, len(b"new line\n")), "no classifier: nothing excluded, unresolved shown")
        self.assertTrue(any("no registered classifier" in g["reason"] for g in report.analysis_gaps))
        self.assertEqual(report.unresolved_intervals, ({"path": "b.txt", "byte_start": 4, "byte_end": 13},))
        self.assertLess(report.observed_ratio, 1.0)
        self.assertEqual(report.to_dict()["meaning"], "coverage of explanation, not proof of correctness")
        empty = trace_coverage(map_change(old, old), old, [], [])
        self.assertIsNone(empty.observed_ratio)


if __name__ == "__main__":
    unittest.main()
