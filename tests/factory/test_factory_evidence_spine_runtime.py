from pathlib import Path
import unittest
from unittest.mock import patch

from factory_kernel.runtime import KernelRuntime
from factory_kernel.worker_runtime import WorkerControlledRuntime


class EvidenceSpineRuntimeTests(unittest.TestCase):
    @patch.object(KernelRuntime, "_exec", return_value="ok")
    def test_production_runtime_routes_legacy_evidence_call_through_spine(self, base_exec):
        runtime = object.__new__(WorkerControlledRuntime)
        result = runtime._exec(
            ["python", "scripts/factory_evidence.py", "--pr", "42", "--output", "/tmp/evidence.json"],
            cwd=Path("/tmp"),
            credential_scope="github+validation",
        )
        self.assertEqual(result, "ok")
        argv = base_exec.call_args.args[0]
        self.assertEqual(argv[0], "python")
        self.assertEqual(argv[1], "scripts/factory_evidence_spine.py")
        self.assertNotIn("scripts/factory_evidence.py", argv)

    @patch.object(KernelRuntime, "_exec", return_value="ok")
    def test_unrelated_trusted_commands_are_not_rewritten(self, base_exec):
        runtime = object.__new__(WorkerControlledRuntime)
        runtime._exec(["python", "harness/post_merge.py"], cwd=Path("/tmp"))
        self.assertEqual(
            base_exec.call_args.args[0],
            ["python", "harness/post_merge.py"],
        )


if __name__ == "__main__":
    unittest.main()
