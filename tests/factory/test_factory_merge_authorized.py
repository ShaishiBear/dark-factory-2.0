"""`merge_authorized` is INVOKED here, not read (DFE-024).

Every test written for this method when it was added asserted that its source contained the
right strings -- `merge-authorization.json`, `head moved after authorization`, `merge_verify.py`
-- and not one of them called it. It contained `worktree = create(...)` where the imported name
is `create_detached`, so it raised NameError on its first real invocation: the merge step of lap
34399514537, after an 89-minute ladder that had gone green on every rung. A method with an
undefined name passed its entire suite.

These tests drive the real method with a fake GitHub client and a fake `_exec`, so the body
executes. The refusal paths run to completion; the happy path runs as far as the worktree, which
needs a real repository and is exercised by the full harness instead. Anything that would have
caught the NameError is here.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.runtime import KernelRuntime, NeedsHuman  # noqa: E402

HEAD = "a" * 40
BASE = "b" * 40
OTHER = "c" * 40


def authorization(head: str = HEAD, base: str = BASE) -> dict:
    return {"version": "1.0", "base_ref": "main", "base_sha": base, "head_sha": head,
            "head_tree_sha": "d" * 40, "evidence_sha256": "e" * 64}


class FakeGitHub:
    """Only what `merge_authorized` reaches for."""

    def __init__(self, state="OPEN", head=HEAD):
        self.cwd = ""
        self._pr = {"state": state, "headRefOid": head, "body": "Fixes #103"}
        self.merged: list[tuple[int, str]] = []

    def pr(self, number, *, holdout_safe=False):
        return self._pr

    def merge_squash(self, number, *, expected_head):
        self.merged.append((number, expected_head))


def runtime_with(tmp: Path, github: FakeGitHub) -> KernelRuntime:
    rt = KernelRuntime.__new__(KernelRuntime)
    rt.repo_root = tmp
    rt.github = github
    rt._authority_cursor = None
    rt.pending_merge = None
    rt.config = mock.MagicMock()
    rt.config.runtime.work_root = tmp / "work"
    rt.config.default_branch = "main"
    return rt


def artifacts_dir(tmp: Path, *, auth: dict | None = None, bundle: bool = True) -> Path:
    d = tmp / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    if auth is not None:
        (d / "merge-authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    if bundle:
        (d / "evidence-bundle.json").write_text(json.dumps({"version": "5.0"}), encoding="utf-8")
    return d


class MergeAuthorizedRuns(unittest.TestCase):
    """The tests that would have caught the NameError."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_moved_head_refuses_and_names_both_shas(self):
        """The body runs all the way to the head comparison, so every name in it resolves."""
        gh = FakeGitHub(head=OTHER)
        rt = runtime_with(self.tmp, gh)
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman) as ctx:
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=authorization()))
        message = str(ctx.exception)
        self.assertIn(HEAD, message)
        self.assertIn(OTHER, message)
        self.assertIn("head moved after authorization", message)
        self.assertEqual(gh.merged, [], "nothing may merge once the head has moved")

    def test_a_closed_pr_refuses(self):
        gh = FakeGitHub(state="CLOSED")
        rt = runtime_with(self.tmp, gh)
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman):
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=authorization()))
        self.assertEqual(gh.merged, [])

    def test_a_missing_authorization_refuses_by_name(self):
        rt = runtime_with(self.tmp, FakeGitHub())
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman) as ctx:
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=None))
        self.assertIn("merge-authorization.json", str(ctx.exception))

    def test_a_missing_evidence_bundle_refuses_by_name(self):
        d = artifacts_dir(self.tmp, auth=authorization(), bundle=False)
        rt = runtime_with(self.tmp, FakeGitHub())
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman) as ctx:
                rt.merge_authorized(134, artifacts=d)
        self.assertIn("evidence-bundle.json", str(ctx.exception))

    def test_an_authorization_without_an_exact_head_refuses(self):
        auth = authorization(head="not-a-sha")
        rt = runtime_with(self.tmp, FakeGitHub())
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman) as ctx:
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=auth))
        self.assertIn("no exact head", str(ctx.exception))

    def test_the_stop_is_rechecked_before_anything_else(self):
        """An emergency stop must beat a valid authorization."""
        gh = FakeGitHub()
        rt = runtime_with(self.tmp, gh)
        boom = RuntimeError("FACTORY_STOPPED")
        with mock.patch.object(KernelRuntime, "check_stop", mock.Mock(side_effect=boom)):
            with self.assertRaises(RuntimeError):
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=authorization()))
        self.assertEqual(gh.merged, [])


class TheAuthorisedMergeRunsEndToEnd(unittest.TestCase):
    """The one that reaches the worktree line, which is where the NameError was.

    The six refusal tests above all return before it. This drives the whole body with the
    repository boundary mocked, so every statement between the head check and the post-merge
    verification executes.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _drive(self):
        gh = FakeGitHub()
        rt = runtime_with(self.tmp, gh)
        d = artifacts_dir(self.tmp, auth=authorization())
        tree = mock.MagicMock()
        tree.path = self.tmp / "wt"
        execs: list[list[str]] = []
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None),              mock.patch.object(KernelRuntime, "_git", lambda self, *a, **k: ""),              mock.patch.object(KernelRuntime, "_run_env", lambda self, *a, **k: {}),              mock.patch.object(KernelRuntime, "_linked_issue_number", lambda self, b: 103),              mock.patch.object(KernelRuntime, "_carry_drop", lambda self, *a, **k: None),              mock.patch.object(KernelRuntime, "_exec",
                               lambda self, argv, **k: execs.append(list(argv)) or ""),              mock.patch("factory_kernel.runtime.create_detached", return_value=tree),              mock.patch("factory_kernel.runtime.remove", lambda *a, **k: None),              mock.patch("factory_kernel.runtime.RunPaths.create",
                        lambda work_root, run_id: __import__("factory_kernel.runtime",
                            fromlist=["RunPaths"]).RunPaths(
                                root=self.tmp, artifacts=d, transcripts=self.tmp)):
            out = rt.merge_authorized(134, artifacts=d)
        return gh, execs, out

    def test_the_merge_is_bound_to_the_authorised_head(self):
        gh, _, _ = self._drive()
        self.assertEqual(gh.merged, [(134, HEAD)],
                         "the merge must spend on exactly the head the evidence authorised")

    def test_post_merge_verification_runs_against_the_same_artifacts(self):
        _, execs, _ = self._drive()
        post = [a for a in execs if "merge_verify.py" in " ".join(a) and "post" in a]
        self.assertEqual(len(post), 1, execs)
        joined = " ".join(post[0])
        self.assertIn("merge-authorization.json", joined)
        self.assertIn("evidence-bundle.json", joined)

    def test_no_authority_is_open_while_the_merge_runs(self):
        """DFE-014: a failure here must not borrow the name of the last thing that passed."""
        gh = FakeGitHub()
        rt = runtime_with(self.tmp, gh)
        rt._authority_cursor = "merge_preauth"
        with mock.patch.object(KernelRuntime, "check_stop", lambda self: None):
            with self.assertRaises(NeedsHuman):
                rt.merge_authorized(134, artifacts=artifacts_dir(self.tmp, auth=None))
        self.assertIsNone(rt._authority_cursor)


class EveryNameInTheMethodResolves(unittest.TestCase):
    def test_no_unresolved_global_in_merge_authorized(self):
        """The generalisation of the NameError: a load with no import, global or local behind it.

        This is a source check and says so -- it is here as a net under the tests above, not in
        place of them, which is the whole point of DFE-024.
        """
        import ast
        import builtins

        tree = ast.parse((ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "merge_authorized")
        known = {a.asname or a.name.split(".")[0]
                 for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        known |= {a.asname or a.name
                  for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        known |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        known |= {t.id for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)}
        local = {a.arg for a in fn.args.args}
        local |= {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)}
        local |= {h.name for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler) and h.name}
        local |= {n.target.id for n in ast.walk(fn)
                  if isinstance(n, (ast.For, ast.AsyncFor)) and isinstance(n.target, ast.Name)}
        local |= {g.target.id for c in ast.walk(fn)
                  if isinstance(c, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
                  for g in c.generators if isinstance(g.target, ast.Name)}
        local |= {n.optional_vars.id for n in ast.walk(fn) if isinstance(n, ast.withitem)
                  and isinstance(getattr(n, "optional_vars", None), ast.Name)}
        loaded = {n.id for n in ast.walk(fn)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        self.assertEqual(sorted(loaded - known - local - set(dir(builtins))), [])


if __name__ == "__main__":
    unittest.main()
