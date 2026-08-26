from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from factory_kernel.runtime import KernelRuntime as BaseKernelRuntime, NeedsHuman
from factory_kernel.worker_runtime import WorkerControlledRuntime


MERGE = "a" * 40
TREE = "b" * 40


class PostMergeRuntimeTests(unittest.TestCase):
    def runtime(self, root: Path) -> WorkerControlledRuntime:
        rt = object.__new__(WorkerControlledRuntime)
        rt.repo_root = root
        rt.config = types.SimpleNamespace(
            default_branch="main",
            runtime=types.SimpleNamespace(work_root=root / "work"),
            labels={"needs_human": "factory:needs-human"},
        )
        rt.github = Mock()
        return rt

    def test_merge_path_always_invokes_post_merge_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "run" / "artifacts" / "merge-verification.json"
            result.parent.mkdir(parents=True)
            rt = self.runtime(root)
            calls = []

            def execute(argv, **kwargs):
                calls.append((argv, kwargs))
                return ""

            rt._exec = execute
            with patch.object(BaseKernelRuntime, "validate_pr", return_value=result):
                output = rt.validate_pr(42, merge=True)

            self.assertEqual(output, result.with_name("post-merge.json"))
            self.assertEqual(len(calls), 1)
            argv, kwargs = calls[0]
            self.assertEqual(argv[1], "harness/post_merge.py")
            self.assertEqual(kwargs["credential_scope"], "validation")
            self.assertEqual(kwargs["timeout"], 4800)

    def test_no_merge_validation_does_not_run_post_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "evidence-bundle.json"
            rt = self.runtime(root)
            rt._exec = Mock(side_effect=AssertionError("post-merge should not run"))
            with patch.object(BaseKernelRuntime, "validate_pr", return_value=result):
                self.assertEqual(rt.validate_pr(42, merge=False), result)

    def test_post_merge_failure_escalates_with_safe_revert_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "run" / "artifacts" / "merge-verification.json"
            result.parent.mkdir(parents=True)
            rt = self.runtime(root)
            rt._exec = Mock(side_effect=RuntimeError("browser failed"))
            rt._create_safe_revert_pr = Mock(return_value=77)
            with patch.object(BaseKernelRuntime, "validate_pr", return_value=result):
                with self.assertRaisesRegex(NeedsHuman, r"safe revert PR #77 opened"):
                    rt.validate_pr(42, merge=True)
            rt._create_safe_revert_pr.assert_called_once()

    def test_revert_is_refused_after_main_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "merge-verification.json"
            evidence.write_text(
                '{"version":"1.0","verdict":"verified","merge_sha":"' + MERGE +
                '","tree_sha":"' + TREE + '"}\n',
                encoding="utf-8",
            )
            rt = self.runtime(root)
            rt._fetch_main = Mock()
            rt._git = Mock(return_value="c" * 40)
            with patch("factory_kernel.worker_runtime.create_detached") as create:
                value = rt._create_safe_revert_pr(42, evidence, RuntimeError("e2e failed"))
            self.assertIsNone(value)
            create.assert_not_called()
            rt.github.add_pr_label.assert_called_once_with(42, "factory:needs-human")
            rt.github.comment_pr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
