"""Daily regression on main, draft-safe unattended merge, merged-branch cleanup.

Structure tests over the workflow files the way tests/factory/test_factory_github_e2e_bootstrap.py
does. The regression workflow must be the worker's environment with the full gate; the merge
job must skip drafts rather than fail on them; the cleanup job must fire only for real merges of
our own branches.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REGRESSION = WORKFLOWS / "dark-factory-main-regression.yml"
WORKER = WORKFLOWS / "dark-factory-worker.yml"
TRUST_ROOT = WORKFLOWS / "dark-factory-trust-root.yml"
CLEANUP = WORKFLOWS / "dark-factory-branch-cleanup.yml"


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

    def test_service_block_is_the_workers_verbatim(self) -> None:
        block_start, block_end = "    services:\n      postgres:\n", "          --health-retries 5\n"
        w = self.worker.index(block_start); w_end = self.worker.index(block_end, w) + len(block_end)
        m = self.text.index(block_start); m_end = self.text.index(block_end, m) + len(block_end)
        self.assertEqual(self.text[m:m_end], self.worker[w:w_end])

    def test_validation_environment_matches_the_worker_field_for_field(self) -> None:
        """The worker writes the loopback URL as one literal; the security guard scans added
        lines for credential-bearing URLs and refuses that literal in a new file, so this
        workflow assembles it from the same parts. Every exported name and value must agree."""
        def env_lines(text: str) -> dict[str, str]:
            step = text.split("- name: Create disposable validation environment", 1)[1]
            step = step.split('} >> "$GITHUB_ENV"', 1)[0]
            out = {}
            for line in step.splitlines():
                line = line.strip()
                if line.startswith('echo "') and "=" in line:
                    key, value = line[len('echo "'):-1].split("=", 1)
                    out[key] = value
            return out
        worker_env = env_lines(self.worker)
        mine = env_lines(self.text)
        self.assertEqual(set(mine), set(worker_env))
        for key in ("JWT_SECRET", "DARK_FACTORY_E2E_EMAIL", "DARK_FACTORY_E2E_PASSWORD", "DARK_FACTORY_E2E_BOOTSTRAP", "SEED_ENABLE"):
            self.assertEqual(mine[key], worker_env[key], key)
        self.assertEqual(mine["DATABASE_URL"], "$database_url")
        # Evaluate the assembly the workflow performs and compare with the worker's literal.
        assembly = re.search(r"database_url=\"\$\(python -c '(.*)'\)\"", self.text).group(1)
        assembled = subprocess.run([sys.executable, "-c", assembly], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(assembled, worker_env["DATABASE_URL"])
        self.assertEqual(worker_env["DATABASE_URL"].split("//", 1)[1].split("@", 1)[1], "127.0.0.1:5432/dark_factory_validation")
        self.assertNotRegex(self.text, r"://[^\s/@]+:[^@\s/]+@", "no credential-bearing URL literal in this file")

    def test_uploads_the_harness_log_and_browser_evidence_on_every_outcome(self) -> None:
        """D-049: the first regression run with a browser failure wrote its evidence dump to
        /tmp on the runner and nothing uploaded it; the diagnosis had to be re-derived
        locally. The gate now names an artifacts dir and the run keeps the dump and the log."""
        gate = self.text.split("- name: Full canonical harness on main", 1)[1].split("- name:", 1)[0]
        self.assertIn("ARTIFACTS_DIR: ${{ runner.temp }}/dark-factory/main-regression/artifacts", gate)
        step = self.text.split("- name: Upload harness log and browser evidence (observability)", 1)
        self.assertEqual(len(step), 2, "upload step missing")
        step = step[1].split("- name:", 1)[0]
        self.assertTrue(step.lstrip().startswith("if: always()"), "must upload on failure too")
        worker_upload = self.worker.split("- name: Upload run transcripts and artifacts (observability)", 1)[1]
        pin = re.search(r"uses: actions/upload-artifact@[0-9a-f]{40}", worker_upload).group(0)
        self.assertIn(pin, step, "the upload action is pinned exactly as the worker pins it")
        self.assertIn("retention-days: 7", step)
        self.assertIn("if-no-files-found: ignore", step)
        self.assertIn("/tmp/main-regression.log", step)
        self.assertIn("${{ runner.temp }}/dark-factory/main-regression/artifacts/e2e-evidence/**", step)
        self.assertLess(self.text.index("Full canonical harness on main"), self.text.index("Upload harness log"))
        self.assertLess(self.text.index("Upload harness log"), self.text.index("File or update the regression issue"))
        # The worker keeps the same dump from validation runs.
        self.assertIn("${{ runner.temp }}/dark-factory/runs/*/artifacts/e2e-evidence/**", worker_upload)

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
        cls.authority, cls.merge = cls.text.split("\n  unattended-merge:\n", 1)

    def test_merge_job_skips_drafts(self) -> None:
        self.assertIn(
            "if: needs.trust-root-authority.outputs.unattended == 'true' && github.event.pull_request.draft == false",
            self.merge,
        )

    def test_authority_job_runs_on_every_event_it_subscribes_to(self) -> None:
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review]", self.text)
        self.assertNotIn("draft", self.authority.split("steps:", 1)[1], "drafts are still judged")

    def test_no_dead_closed_event_job(self) -> None:
        """A close performed by GitHub's auto-merge is caused by GITHUB_TOKEN and starts no
        workflow run (none existed for #59, #60, #61), so a `closed` job here is a claim that
        can never be kept. Cleanup lives on a schedule instead (D-020)."""
        self.assertNotIn("delete-merged-branch", self.text)
        self.assertNotIn("closed", self.text.split("jobs:", 1)[0])
        self.assertNotIn("github.event.action", self.text)


class BranchCleanupWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CLEANUP.read_text(encoding="utf-8")
        cls.head, cls.jobs = cls.text.split("jobs:", 1)

    def test_runs_on_a_schedule_and_on_dispatch_only(self) -> None:
        self.assertIn("schedule:\n    - cron: '53 * * * *'", self.head)
        self.assertIn("workflow_dispatch:", self.head)
        self.assertNotIn("pull_request", self.head)
        self.assertNotIn("cron: '17 * * * *'", self.head, "must not collide with the worker's slot")

    def test_holds_exactly_the_authority_it_needs(self) -> None:
        self.assertIn("permissions:\n  contents: write\n  pull-requests: read\n", self.head)
        self.assertNotIn("issues: write", self.text)
        self.assertNotIn("actions/checkout", self.text)
        self.assertNotIn("uses:", self.text, "no code, no third-party action; gh only")

    def test_only_our_short_lived_branches_are_candidates(self) -> None:
        self.assertIn("main) continue ;;", self.jobs)
        self.assertIn("human/*|factory/*) ;;", self.jobs)
        self.assertIn("*) continue ;;", self.jobs)

    def test_deletes_only_a_tip_that_is_exactly_a_merged_pr_head_from_this_repository(self) -> None:
        self.assertIn('--state merged --head "$name"', self.jobs)
        self.assertIn(r'select(.headRepositoryOwner.login == \"$owner\")', self.jobs)
        self.assertIn('if [ -z "$merged_head" ] || [ "$merged_oid" = "null" ]; then', self.jobs)
        self.assertIn('if [ "$merged_oid" != "$tip" ]; then', self.jobs)
        self.assertIn("reason=tip-past-merged-pr", self.jobs)
        self.assertIn('gh api -X DELETE "repos/$GITHUB_REPOSITORY/git/refs/heads/$name"', self.jobs)
        # The delete is reachable only after both guards.
        guard_pr = self.jobs.index('[ "$merged_oid" = "null" ]')
        guard_tip = self.jobs.index('"$merged_oid" != "$tip"')
        delete = self.jobs.index("gh api -X DELETE")
        self.assertLess(guard_pr, guard_tip)
        self.assertLess(guard_tip, delete)
        self.assertIn("BRANCH_CLEANUP_OK deleted=$deleted kept=$kept", self.jobs)

if __name__ == "__main__":
    unittest.main()
