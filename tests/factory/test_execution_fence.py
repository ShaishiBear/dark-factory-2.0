"""Protected persistent fences stop effects, including late publication and paid judges."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import re
import unittest
from unittest.mock import Mock, patch

from factory_kernel.execution_fence import ExecutionFenced, FENCE_PATH, fence_status, require_execution_open
from factory_kernel.frontdoor_exploration import require_clear_stop
from factory_kernel.runtime import FactoryStopped, KernelRuntime, RunPaths
from factory_kernel.publication_source import observe_publication_source
from tests.factory.test_programme_turnover import TurnoverGitHub
from tests.factory.test_factory_authority_bounds import _runtime, _Provider
from tests.factory.test_factory_stage_runs import _bounded


class FenceGitHub(TurnoverGitHub):
    def __init__(self):
        super().__init__()
        self.fence = None
        self.push_branch = Mock()
        self.create_pr = Mock()

    def json(self, args):
        result = super().json(args)
        if "/git/trees/" in args[1] and self.fence is not None:
            result["tree"].append(deepcopy(self.fence))
        return result


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.github = FenceGitHub()

    def fence(self, mode="100644", path=FENCE_PATH):
        self.github.fence = {"path": path, "mode": mode, "sha": "e" * 40}

    def runtime(self):
        runtime = object.__new__(KernelRuntime)
        runtime.github = self.github
        runtime.repo_root = Path.cwd()
        runtime.config = SimpleNamespace(repository=self.github.repository, default_branch="main",
                                        runtime=SimpleNamespace(work_root=Path.cwd()))
        runtime.provider = Mock()
        return runtime

    def test_absence_is_current_protected_observation_and_presence_has_no_permissive_payload(self):
        self.assertEqual(require_execution_open(self.github), "c" * 40)
        for mode, path in (("100644", FENCE_PATH), ("120000", FENCE_PATH),
                           ("040000", FENCE_PATH), ("100644", FENCE_PATH + "/child")):
            self.fence(mode, path)
            self.assertEqual(fence_status(self.github)["state"], "fenced")
            with self.assertRaises(ExecutionFenced):
                require_execution_open(self.github)
        # No fence blob, local checkout or JSON allow flag is ever consulted.
        self.assertFalse(any("/git/blobs/" in args[1] for args in self.github.calls))

    def test_unprotected_truncated_unknown_or_duplicate_inventory_stops(self):
        self.github.protected = False
        with self.assertRaises(ExecutionFenced):
            require_execution_open(self.github)
        self.assertEqual(len(self.github.calls), 1, "unprotected source must refuse before reading its tree")
        self.github.protected = True
        self.github.truncated = True
        with self.assertRaises(ExecutionFenced):
            require_execution_open(self.github)
        self.github.truncated = False
        for entry in ({}, {"path": 3}, {"path": ".factory/programmes/active.json"}):
            self.github.fence = entry
            with self.assertRaises(ExecutionFenced):
                require_execution_open(self.github)
        with patch.object(self.github, "json", side_effect=TimeoutError("private network detail")):
            with self.assertRaises(ExecutionFenced) as exc:
                require_execution_open(self.github)
            self.assertNotIn("private network", str(exc.exception))

    def test_moving_main_or_lost_protection_during_observation_stops(self):
        original = self.github.json
        for changed in ({"protected": True, "commit": {"sha": "b" * 40}},
                        {"protected": False, "commit": {"sha": "c" * 40}}):
            reads = 0
            def moved(args):
                nonlocal reads
                if "/branches/" in args[1]:
                    reads += 1
                    if reads == 2:
                        return changed
                return original(args)
            with patch.object(self.github, "json", side_effect=moved):
                with self.assertRaises(ExecutionFenced):
                    require_execution_open(self.github)

    def test_remote_fence_survives_new_process_and_does_not_hide_completion_observation(self):
        self.fence()
        with patch("factory_kernel.runtime.subprocess.run", return_value=Mock(returncode=0)):
            for _ in range(2):
                with self.assertRaises(FactoryStopped):
                    self.runtime().check_stop()
        from factory_kernel.programme_runtime import ProgrammeQueue
        self.assertIsNotNone(ProgrammeQueue(self.github, "main").current())

    def test_local_or_remote_stop_failure_is_not_overridden_by_absent_fence(self):
        with patch("factory_kernel.runtime.subprocess.run", return_value=Mock(returncode=1, stdout="STOPPED", stderr="")):
            with self.assertRaises(FactoryStopped):
                self.runtime().check_stop()
        self.assertEqual(self.github.calls, [])

    def test_fence_blocks_last_build_publication_before_branch_or_pr_effect(self):
        self.fence()
        runtime = self.runtime()
        with patch("factory_kernel.runtime.subprocess.run", return_value=Mock(returncode=0)):
            with self.assertRaises(FactoryStopped):
                runtime._publish_build(None, Path.cwd(), {}, {"number": 1}, 1, "factory/example")
        self.github.push_branch.assert_not_called()
        self.github.create_pr.assert_not_called()

    def test_paid_judge_cannot_skip_fence_by_entering_common_funnel_directly(self):
        self.fence()
        runtime = self.runtime()
        with patch("factory_kernel.runtime.subprocess.run", return_value=Mock(returncode=0)):
            with self.assertRaises(FactoryStopped):
                runtime._agent_stage(None, _bounded("holdout"))
        runtime.provider.run.assert_not_called()

    def test_transient_retry_rechecks_fence_before_restoring_or_spending_again(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _runtime(Path(directory), _Provider())
            paths = RunPaths.create(Path(directory), "fence")
            retry = Mock()
            runtime.check_stop = Mock(side_effect=[None, FactoryStopped("fenced")])
            def provider(_request, **kwargs):
                kwargs["before_retry"](2)
                self.fail("retry continued")
            runtime.provider.run = Mock(side_effect=provider)
            with self.assertRaises(FactoryStopped):
                runtime._agent_stage(paths, _bounded("holdout"), before_retry=retry)
            retry.assert_not_called()
            runtime.provider.run.assert_called_once()

    def test_clear_stop_issue_list_cannot_override_fence_for_hosted_exploration_or_publication(self):
        self.fence()
        with self.assertRaises(ExecutionFenced):
            require_clear_stop(self.github)
        with self.assertRaises(ExecutionFenced):
            observe_publication_source(self.github)

    def test_authenticated_hosted_call_checks_both_schema_paths_before_any_model(self):
        from factory_kernel.frontdoor_hosted_worker import execute_call
        from factory_kernel.hosted_exploration_call import SCHEMA
        self.fence()
        provider = Mock()
        for schema in (SCHEMA, "dark-factory/hosted-preparation-v1"):
            with self.assertRaises(ExecutionFenced):
                execute_call(None, self.github, {"schema": schema}, provider)
        provider.run.assert_not_called()

    def test_publication_and_worker_share_one_non_cancelling_execution_owner(self):
        root = Path(__file__).resolve().parents[2]
        for name in ("dark-factory-worker.yml", "dark-factory-programme-publish.yml"):
            workflow = (root / ".github/workflows" / name).read_text()
            block = workflow.split("\nconcurrency:\n", 1)[1].split("\njobs:", 1)[0]
            self.assertEqual(re.findall(r"(?m)^  group: (.+)$", block), ["dark-factory-worker"])
            self.assertEqual(re.findall(r"(?m)^  cancel-in-progress: (.+)$", block), ["false"])


if __name__ == "__main__":
    unittest.main()
