"""Daily regression on main, draft-safe unattended merge, merged-branch cleanup.

Structure tests over the workflow files the way tests/factory/test_factory_github_e2e_bootstrap.py
does. The regression workflow must be the worker's environment with the full gate; the merge
job must skip drafts rather than fail on them; the cleanup job must fire only for real merges of
our own branches.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REGRESSION = WORKFLOWS / "dark-factory-main-regression.yml"
WORKER = WORKFLOWS / "dark-factory-worker.yml"
TRUST_ROOT = WORKFLOWS / "dark-factory-trust-root.yml"


def uses_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip().startswith(("- uses:", "uses:"))}


def pinned_versions(text: str) -> set[str]:
    return set(re.findall(r"(?:python-version|node-version|bun-version|version): '[^']+'", text))


class MainRegressionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = REGRESSION.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")

    def test_runs_daily_on_main_and_on_dispatch(self) -> None:
        self.assertIn("schedule:\n    - cron: '41 3 * * *'", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("ref: main", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("cron: '17 * * * *'", self.text, "must not collide with the worker's slot")

    def test_toolchain_pins_match_the_worker_exactly(self) -> None:
        worker_uses = {u.replace("- ", "") for u in uses_lines(self.worker)}
        mine = {u.replace("- ", "") for u in uses_lines(self.text)}
        self.assertTrue(mine, "regression workflow has no pinned actions")
        self.assertTrue(mine <= worker_uses, f"regression uses actions the worker does not pin: {mine - worker_uses}")
        for line in mine:
            self.assertRegex(line, r"@[0-9a-f]{40}$", line)
        self.assertEqual(pinned_versions(self.text), pinned_versions(self.worker))
        self.assertIn("npm install -g agent-browser@0.35.0", self.text)
        self.assertNotIn("claude-code", self.text, "the regression never launches a model worker")

    def test_environment_blocks_are_the_workers_verbatim(self) -> None:
        for block_start, block_end in (
            ("    services:\n      postgres:\n", "          --health-retries 5\n"),
            ("      - name: Create disposable validation environment\n", '          } >> "$GITHUB_ENV"\n'),
        ):
            w = self.worker.index(block_start); w_end = self.worker.index(block_end, w) + len(block_end)
            m = self.text.index(block_start); m_end = self.text.index(block_end, m) + len(block_end)
            self.assertEqual(self.text[m:m_end], self.worker[w:w_end], f"block differs: {block_start.strip()}")

    def test_holds_no_write_authority_over_code(self) -> None:
        head = self.text.split("jobs:", 1)[0]
        self.assertIn("permissions:\n  contents: read\n  issues: write\n", head)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("pr merge", self.text)

    def test_runs_the_full_gate_and_requires_its_marker(self) -> None:
        self.assertIn("python harness/ci.py 2>&1 | tee /tmp/main-regression.log", self.text)
        self.assertNotIn("harness/ci.py --quick", self.text)
        self.assertIn("grep -q '^GATE_OK mode=full' /tmp/main-regression.log", self.text)
        self.assertIn('echo "MAIN_REGRESSION_OK head=$(git rev-parse HEAD)"', self.text)
        self.assertIn("secrets.OPENROUTER_API_KEY", self.text)
        self.assertIn("secrets.SUPADATA_API_KEY", self.text)

    def test_failure_files_one_triageable_issue_and_escalates_on_the_second(self) -> None:
        step = self.text.split("- name: File or update the regression issue", 1)[1]
        self.assertIn("if: steps.gate.outputs.failed == 'true'", step)
        self.assertIn('title_prefix="regression: main failed the full harness"', step)
        self.assertIn('--search "\\"$title_prefix\\" in:title"', step, "must dedupe against open issues first")
        self.assertIn('--label "priority:high" --label "type:bug"', step)
        self.assertIn("gh issue comment", step)
        self.assertIn('if [ "$prior" -ge 1 ]; then', step)
        self.assertIn('--add-label "factory:needs-human"', step)
        # The only factory:* label the job may ever apply is the escalation.
        factory_labels = set(re.findall(r"factory:[a-z-]+", step))
        self.assertEqual(factory_labels, {"factory:needs-human"}, factory_labels)
        self.assertIn("<!-- dark-factory-main-regression -->", step)
        self.assertTrue(step.rstrip().endswith("exit 1"), "a failed regression must fail the run")


class TrustRootHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TRUST_ROOT.read_text(encoding="utf-8")
        cls.authority, rest = cls.text.split("\n  unattended-merge:\n", 1)
        cls.merge, cls.cleanup = rest.split("\n  delete-merged-branch:\n", 1)

    def test_merge_job_skips_drafts(self) -> None:
        self.assertIn(
            "if: needs.trust-root-authority.outputs.unattended == 'true' && github.event.pull_request.draft == false",
            self.merge,
        )

    def test_authority_job_still_runs_on_every_non_close_event(self) -> None:
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review, closed]", self.text)
        self.assertIn("if: github.event.action != 'closed'", self.authority)
        self.assertNotIn("draft", self.authority.split("steps:", 1)[1], "drafts are still judged")

    def test_cleanup_runs_only_for_real_merges_of_our_own_branches(self) -> None:
        self.assertIn(
            "if: github.event.action == 'closed' && github.event.pull_request.merged == true "
            "&& github.event.pull_request.head.repo.full_name == github.repository",
            self.cleanup,
        )
        self.assertIn('test "$HEAD_REF" != "main"', self.cleanup)
        self.assertIn('gh api -X DELETE "repos/$GITHUB_REPOSITORY/git/refs/heads/$HEAD_REF"', self.cleanup)
        self.assertNotIn("actions/checkout", self.cleanup)
        self.assertNotIn("uses:", self.cleanup)
        self.assertIn("    permissions:\n      contents: write\n", self.cleanup)
        self.assertNotIn("pull-requests: write", self.cleanup)


if __name__ == "__main__":
    unittest.main()
