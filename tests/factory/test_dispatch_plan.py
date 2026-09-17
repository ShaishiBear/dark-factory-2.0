"""Read-only dispatch planning: the same decision as `choose_dispatch`, with zero effects.

The pure selector is checked on every cause the public conformance vectors name and on the
combined causes they deliberately isolate. The observer is checked against a GitHub double
whose every mutation raises, so a planner that reaps, labels, comments or syncs fails the
test rather than passing quietly. The CLI is checked to construct no runtime and no provider,
and the workflow to start no service, mint no token and hold no model secret before a plan
says a dispatch is worth starting (WP00, R00, C04).
"""
from __future__ import annotations

import contextlib
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel import dispatch_plan
from factory_kernel.config import load_config
from factory_kernel.dispatch_plan import (
    DispatchObservation, DispatchPlanner, PAID_ACTIONS, STOP_UNREADABLE, issue_dispatch_key,
    load_lease_policy, select_dispatch,
)
from factory_kernel.runtime import KernelRuntime

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/dark-factory-worker.yml"
CONFIG = load_config(ROOT / ".factory/kernel.json")
REPO = CONFIG.repository
SHA = "a" * 40


def row(number, *labels, updated="2026-01-01T00:00:00Z", **extra):
    return {"number": number, "updatedAt": updated, "labels": [{"name": name} for name in labels], **extra}


def observation(**overrides):
    base = dict(control_observed=True, stopped=False, fenced=False, reconciliation_required=False)
    base.update(overrides)
    return DispatchObservation(**base)


class SelectorTests(unittest.TestCase):
    """Pure decision: no I/O can happen here, so every case is a table lookup on inputs."""

    def test_idle_when_nothing_is_observed_and_budget_is_irrelevant(self):
        for budget in (True, False, None):
            plan = select_dispatch(observation(budget=budget))
            self.assertEqual((plan.status, plan.reason_codes, plan.action, plan.subject), ("idle", (), None, None))
            self.assertEqual(plan.mutation_count, 0)

    def test_review_outranks_rehead_and_build_and_picks_the_oldest_number(self):
        plan = select_dispatch(observation(review=(row(8), row(3)), rehead=(2,), build=(row(1),), budget=True))
        self.assertEqual((plan.status, plan.action, plan.subject), ("ready", "validate-pr", 3))
        older = select_dispatch(observation(review=(row(3, updated="2026-02-01"), row(8, updated="2026-01-01"))))
        self.assertEqual(older.subject, 8, "least recently updated first, exactly as the runtime")

    def test_rehead_outranks_build_and_needs_no_allowance(self):
        plan = select_dispatch(observation(rehead=(2,), build=(row(1),), budget=False))
        self.assertEqual((plan.status, plan.action, plan.subject), ("ready", "rehead-pr", 2))
        self.assertNotIn("rehead-pr", PAID_ACTIONS)

    def test_build_uses_the_runtime_priority_rule(self):
        issues = (row(7), row(9, "priority:high"), row(4, "priority:high", updated="2025-12-01"))
        plan = select_dispatch(observation(build=issues, budget=True))
        self.assertEqual((plan.action, plan.subject), ("build-issue", 4))
        self.assertEqual(issue_dispatch_key(issues[1]), (1, "2026-01-01T00:00:00Z", 9))

    def test_control_causes_take_precedence_in_order_and_hide_nothing_else(self):
        cases = (
            (dict(control_observed=False, stopped=True, fenced=True, reconciliation_required=True, budget=False), "control_unobserved"),
            (dict(stopped=True, fenced=True, reconciliation_required=True, budget=False), "stopped"),
            (dict(fenced=True, reconciliation_required=True, budget=False), "fenced"),
            (dict(reconciliation_required=True, budget=False), "reconciliation_required"),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                plan = select_dispatch(observation(build=(row(7),), **overrides))
                self.assertEqual((plan.status, plan.reason_codes, plan.action, plan.subject),
                                 ("blocked", (code,), None, None))
                self.assertEqual(plan.mutation_count, 0)

    def test_paid_work_without_allowance_is_visibly_budget_required(self):
        for work in (dict(build=(row(7),)), dict(review=(row(5),))):
            plan = select_dispatch(observation(budget=False, **work))
            self.assertEqual((plan.status, plan.reason_codes, plan.action), ("blocked", ("budget_required",), None))
        unknown = select_dispatch(observation(budget=None, build=(row(7),)))
        self.assertEqual((unknown.status, unknown.action), ("ready", "build-issue"))
        self.assertEqual(unknown.observations["budget"], "unobserved")

    def test_explicit_resume_and_continuation_are_planned_work_not_idle(self):
        resume = select_dispatch(observation(resume=41, review=(row(5),), budget=False))
        self.assertEqual((resume.status, resume.action, resume.subject), ("ready", "resume-pr", 41))
        continuation = select_dispatch(observation(continuation="c" * 64, budget=False))
        self.assertEqual((continuation.status, continuation.action, continuation.subject),
                         ("ready", "materialize-programme", None))
        ready = select_dispatch(observation(programme_ready=True))
        self.assertEqual(ready.action, "materialize-programme")
        stopped = select_dispatch(observation(stopped=True, resume=41))
        self.assertEqual(stopped.reason_codes, ("stopped",), "control still comes first")

    def test_record_is_a_proposal_with_the_declared_schema(self):
        record = select_dispatch(observation(build=(row(7),))).record(repository=REPO)
        self.assertEqual(record["schema"], "dark-factory/dispatch-plan")
        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["authority"], "proposal-only")
        self.assertEqual(record["inputs"], {"repository": REPO})
        self.assertEqual(record["mutation_count"], 0)
        json.dumps(record)


class RecordingGitHub:
    """Every read is recorded; every mutation raises. The planner must never reach one."""

    MUTATIONS = (
        "add_issue_label", "remove_issue_label", "add_pr_label", "remove_pr_label", "comment_issue",
        "comment_pr", "create_pr", "create_issue", "push_branch", "merge_squash", "run_as_app",
        "create_programme_issue",
    )

    def __init__(self, *, review=(), needs_fix=(), accepted=(), comments=None, in_progress=(), prs=(),
                 issue_comments=None, fenced=False):
        self.repository = REPO
        self.calls = []
        self.review, self.needs_fix, self.accepted = list(review), list(needs_fix), list(accepted)
        self.comments = comments or {}
        self.in_progress, self.prs, self.issue_comments = list(in_progress), list(prs), issue_comments or {}
        self.fenced = fenced
        self.fail_reads = False
        for name in self.MUTATIONS:
            setattr(self, name, self._mutation(name))

    @staticmethod
    def _mutation(name):
        def refuse(*_args, **_kwargs):
            raise AssertionError(f"read-only planning performed a mutation: {name}")
        return refuse

    def list_prs(self, label):
        self.calls.append(("prs", label))
        if self.fail_reads:
            raise RuntimeError("gh pr list failed")
        return list({CONFIG.labels["needs_review"]: self.review, CONFIG.labels["needs_fix"]: self.needs_fix}[label])

    def list_issues(self, label):
        self.calls.append(("issues", label))
        return list(self.accepted)

    def pr_comments(self, number):
        self.calls.append(("comments", number))
        return list(self.comments.get(number, []))

    def issue(self, number):
        self.calls.append(("issue", number))
        return next(dict(r) for r in self.accepted if r["number"] == number)

    def programme_issues(self):
        return []

    @staticmethod
    def labels(value):
        return {item["name"] for item in value.get("labels", [])}

    def json(self, args, **_kwargs):
        self.calls.append(("json", tuple(args)))
        joined = " ".join(args)
        if args[:2] == ["issue", "list"]:
            return list(self.in_progress)
        if args[:2] == ["pr", "list"]:
            return list(self.prs)
        if "/issues/" in joined and "comments?per_page=100" in joined:
            number = int(re.search(r"/issues/(\d+)/comments", joined).group(1))
            return [list(self.issue_comments.get(number, []))]
        if joined.endswith(f"/branches/{CONFIG.default_branch}"):
            return {"protected": True, "commit": {"sha": SHA}}
        if "/git/trees/" in joined:
            tree = [{"path": "README.md"}]
            if self.fenced:
                tree.append({"path": ".factory/programmes/execution-fence.json"})
            return {"truncated": False, "tree": tree}
        raise AssertionError(f"unexpected read {args}")


def stop_runner(returncode=0, stdout="STOP_CHECK_OK"):
    def run(argv, **kwargs):
        assert argv == ["bash", "scripts/factory-stop.sh"], argv
        return subprocess.CompletedProcess(argv, returncode, stdout, "")
    return Mock(side_effect=run)


class PlannerTests(unittest.TestCase):
    def planner(self, github, *, runner=None, observe_programme=True, log=None):
        return DispatchPlanner(github, CONFIG, repo_root=ROOT, observe_programme=observe_programme,
                               runner=runner or stop_runner(), log=log or (lambda _line: None))

    def test_ready_plan_performs_zero_mutations_and_never_runs_the_reaper(self):
        policy = load_lease_policy(ROOT)
        fresh = row(3, "factory:accepted", "factory:in-progress", updated=policy.iso(policy.now_utc()))
        github = RecordingGitHub(review=[row(8)], in_progress=[fresh])
        runner = stop_runner()
        plan = self.planner(github, runner=runner).plan()
        self.assertEqual((plan.status, plan.action, plan.subject), ("ready", "validate-pr", 8))
        self.assertEqual(plan.mutation_count, 0)
        argvs = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(argvs, [["bash", "scripts/factory-stop.sh"]], "no lease script, no reap")
        self.assertFalse(any(call[0] == "issues" for call in github.calls), "review hides the issue list")

    def test_a_lease_the_reaper_would_release_is_reported_not_released(self):
        policy = load_lease_policy(ROOT)
        stale = policy.render({"lease_id": "x", "workflow_id": "9", "stage": "implement", "state": "active",
                               "heartbeat_at": policy.iso(policy.now_utc() - timedelta(days=2)), "pr": None})
        github = RecordingGitHub(
            accepted=[row(3, "factory:accepted", "factory:in-progress")],
            in_progress=[row(3, "factory:accepted", "factory:in-progress")],
            issue_comments={3: [{"id": 1, "body": stale}]},
        )
        plan = self.planner(github).plan()
        self.assertEqual((plan.status, plan.reason_codes), ("blocked", ("reconciliation_required",)))
        self.assertEqual(plan.observations["reconciliation"],
                         [{"issue": 3, "reason": "active lease heartbeat expired before PR handoff", "handoff": "none"}])
        self.assertFalse(any(call[0] in {"prs", "issues"} for call in github.calls), "work is not selected past it")
        fresh = policy.render({"lease_id": "x", "workflow_id": "9", "stage": "implement", "state": "active",
                               "heartbeat_at": policy.iso(policy.now_utc()), "pr": None})
        github.issue_comments[3] = [{"id": 1, "body": fresh}]
        github.calls.clear()
        plan = self.planner(github).plan()
        self.assertEqual(plan.status, "idle", "a fresh lease is kept and the claimed issue is not idle")

    def test_stop_and_fence_are_tri_state_and_unobservable_blocks(self):
        cases = (
            (stop_runner(1, "STOPPED: /tmp/.factory-stop present. Remove it to resume."), False, ("stopped",)),
            (stop_runner(1, f"STOPPED: {STOP_UNREADABLE} from GitHub, halting: boom"), False, ("control_unobserved",)),
            (Mock(side_effect=OSError("no bash")), False, ("control_unobserved",)),
            (stop_runner(), True, ("fenced",)),
        )
        for runner, fenced, codes in cases:
            with self.subTest(codes=codes):
                plan = self.planner(RecordingGitHub(review=[row(8)], fenced=fenced), runner=runner).plan()
                self.assertEqual((plan.status, plan.reason_codes, plan.action), ("blocked", codes, None))
        github = RecordingGitHub(review=[row(8)])
        github.json = Mock(side_effect=RuntimeError("gh api failed"))
        plan = self.planner(github).plan()
        self.assertEqual(plan.reason_codes, ("control_unobserved",), "an unreadable fence is not a clear fence")

    def test_the_unreadable_marker_is_the_stop_scripts_own_words(self):
        self.assertIn(STOP_UNREADABLE, (ROOT / "scripts/factory-stop.sh").read_text(encoding="utf-8"))

    def test_a_read_failure_blocks_dispatch_and_is_not_an_empty_queue(self):
        github = RecordingGitHub()
        github.fail_reads = True
        plan = self.planner(github).plan()
        self.assertEqual((plan.status, plan.reason_codes), ("blocked", ("control_unobserved",)))
        self.assertTrue(any(note.startswith("work: RuntimeError") for note in plan.observations["notes"]))

    def test_work_is_observed_lazily_in_the_legacy_order(self):
        github = RecordingGitHub(needs_fix=[row(12, headRefOid=SHA)], accepted=[row(42, "factory:accepted")])
        with patch.object(dispatch_plan, "rehead_eligible", return_value=True):
            plan = self.planner(github).plan()
        self.assertEqual((plan.action, plan.subject), ("rehead-pr", 12))
        self.assertFalse(any(call[0] == "issues" for call in github.calls))
        github = RecordingGitHub(accepted=[row(42, "factory:accepted"), row(7, "factory:accepted", "factory:in-progress")])
        plan = self.planner(github).plan()
        self.assertEqual((plan.action, plan.subject), ("build-issue", 42))

    def test_programme_item_awaiting_its_issue_is_planned_work_only_when_asked(self):
        github = RecordingGitHub()
        status = {"items": [{"status": "ready-for-candidate"}]}
        with patch("factory_kernel.dispatch_plan.ProgrammeQueue.status", return_value=status) as read:
            plan = self.planner(github).plan()
            self.assertEqual((plan.status, plan.action), ("ready", "materialize-programme"))
            read.assert_called_once()
        with patch("factory_kernel.dispatch_plan.ProgrammeQueue.status") as read:
            plan = self.planner(github, observe_programme=False).plan()
            self.assertEqual(plan.status, "idle")
            read.assert_not_called()

    def test_runtime_plan_dispatch_reaches_no_reap_and_choose_dispatch_still_does(self):
        rt = KernelRuntime(repo_root=ROOT, config=CONFIG)
        github = RecordingGitHub(review=[row(8)])
        rt.github = github
        rt.reap_stale_claims = Mock(side_effect=AssertionError("planning must not reap"))
        rt.check_stop = Mock(side_effect=AssertionError("planning must not raise-or-stop"))
        with patch.object(DispatchPlanner, "observe_control", return_value={"observed": True, "stopped": False,
                                                                          "fenced": False, "detail": "clear"}):
            plan = rt.plan_dispatch()
        self.assertEqual((plan.status, plan.action, plan.subject), ("ready", "validate-pr", 8))
        order = []
        rt.check_stop = lambda: order.append("stop")
        rt.reap_stale_claims = lambda: order.append("reap")
        decision = rt.choose_dispatch()
        self.assertEqual(order, ["stop", "reap"])
        self.assertEqual((decision.kind, decision.number, decision.reason), ("validate-pr", 8, "PR validation has priority"))


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.output = self.directory / "plan" / "dispatch-plan.json"
        self.step_outputs = self.directory / "outputs.txt"

    def invoke(self, plan, *extra):
        from factory_kernel import cli

        argv = ["factory_kernel", "plan-dispatch", "--output", str(self.output), *extra]
        stdout = io.StringIO()
        with patch("sys.argv", argv), patch.object(cli, "runtime", side_effect=AssertionError("execution runtime")), \
                patch("factory_kernel.github_cli.GitHubClient") as client, \
                patch.object(DispatchPlanner, "plan", return_value=plan) as planned, \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.step_outputs)}), contextlib.redirect_stdout(stdout):
            code = cli.main()
        return code, stdout.getvalue(), planned, client

    def outputs(self):
        return dict(line.split("=", 1) for line in self.step_outputs.read_text().splitlines())

    def test_plan_constructs_no_runtime_writes_the_record_and_hands_outputs_on(self):
        plan = select_dispatch(observation(build=(row(7),)))
        code, text, planned, _client = self.invoke(plan)
        self.assertEqual(code, 0)
        planned.assert_called_once_with(resume=None, continuation="")
        record = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual((record["status"], record["action"], record["subject"]), ("ready", "build-issue", 7))
        self.assertEqual(record["inputs"], {"resume_pr": None, "resume_run_id": None, "continuation_programme": "",
                                            "repository": REPO})
        outputs = self.outputs()
        self.assertEqual((outputs["ready"], outputs["plan_status"], outputs["plan_action"]), ("true", "ready", "build-issue"))
        self.assertRegex(outputs["plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn(f"sha256={outputs['plan_sha256']}", text)
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["dispatch-plan.json"], "no temp file left")

    def test_idle_and_missing_allowance_are_honest_zero_exits_that_start_nothing(self):
        for plan in (select_dispatch(observation()), select_dispatch(observation(build=(row(7),), budget=False))):
            with self.subTest(status=plan.status):
                code, text, _p, _c = self.invoke(plan)
                self.assertEqual(code, 0)
                self.assertEqual(self.outputs()["ready"], "false")
                self.assertIn(f"status={plan.status}", text)

    def test_stopped_fenced_and_unobserved_fail_closed(self):
        for overrides in (dict(stopped=True), dict(fenced=True), dict(control_observed=False)):
            with self.subTest(overrides=overrides):
                code, _t, _p, _c = self.invoke(select_dispatch(observation(**overrides)))
                self.assertEqual(code, 1)
                self.assertEqual(self.outputs()["ready"], "false")

    def test_reconciliation_needs_the_dispatcher_so_the_job_runs(self):
        code, text, _p, _c = self.invoke(select_dispatch(observation(reconciliation_required=True)))
        self.assertEqual(code, 0)
        self.assertEqual((self.outputs()["ready"], self.outputs()["plan_status"]), ("true", "blocked"))
        self.assertIn("reasons=reconciliation_required", text)

    def test_resume_and_continuation_inputs_are_validated_and_forwarded(self):
        plan = select_dispatch(observation(resume=41))
        code, _t, planned, _c = self.invoke(plan, "--resume-pr", "41", "--resume-run-id", "99", "--expected-programme", "c" * 64)
        self.assertEqual(code, 0)
        planned.assert_called_once_with(resume=41, continuation="c" * 64)
        self.assertEqual(json.loads(self.output.read_text())["inputs"]["resume_run_id"], 99)
        self.output.unlink()
        for extra in (("--resume-pr", "41"), ("--resume-run-id", "99"), ("--resume-pr", "x", "--resume-run-id", "1"),
                      ("--expected-programme", "not-a-digest")):
            with self.subTest(extra=extra):
                code, text, planned, _c = self.invoke(plan, *extra)
                self.assertEqual(code, 1)
                self.assertIn("FACTORY_PLAN_REFUSED", text)
                planned.assert_not_called()
                self.assertFalse(self.output.exists())


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.jobs = cls.text.split("\njobs:\n", 1)[1]
        cls.plan = cls.jobs.split("\n  plan:\n", 1)[1].split("\n  dispatch:\n", 1)[0]
        cls.dispatch = cls.jobs.split("\n  dispatch:\n", 1)[1].split("\n  merge:\n", 1)[0]

    def test_plan_job_runs_first_with_no_service_secret_mint_or_toolchain(self):
        self.assertLess(self.jobs.index("\n  plan:\n"), self.jobs.index("\n  dispatch:\n"))
        for forbidden in ("services:", "secrets.", "create-github-app-token", "DARK_FACTORY_APP_TOKEN",
                          "ANTHROPIC_", "OPENROUTER", "FRONTDOOR_AGE_IDENTITY", "npm install", "setup-uv",
                          "setup-bun", "setup-node", "dispatch --once", "programme-sync", "factory_kernel reap",
                          "continue-on-error"):
            self.assertNotIn(forbidden, self.plan, forbidden)
        self.assertIn("permissions:\n      contents: read\n      issues: read\n      pull-requests: read\n", self.plan)
        self.assertIn("persist-credentials: false", self.plan)
        self.assertIn("python -m factory_kernel plan-dispatch --output", self.plan)
        for forwarded in ('--resume-pr "$RESUME_PR"', '--resume-run-id "$RESUME_RUN_ID"',
                          '--expected-programme "$EXPECTED_PROGRAMME"', "RESUME_PR: ${{ inputs.resume_pr }}",
                          "EXPECTED_PROGRAMME: ${{ inputs.continuation_programme }}", "GH_TOKEN: ${{ github.token }}"):
            self.assertIn(forwarded, self.plan)
        self.assertIn("ready: ${{ steps.plan.outputs.ready }}", self.plan)
        self.assertIn("plan_sha256: ${{ steps.plan.outputs.plan_sha256 }}", self.plan)
        self.assertIn("if: github.repository == 'ShaishiBear/dark-factory-2.0'", self.plan)
        retain = self.plan.split("- name: Retain the dispatch plan", 1)[1]
        self.assertIn("if: always()", retain)
        self.assertIn("dispatch-plan.json", retain)

    def test_dispatch_depends_on_a_ready_plan_and_keeps_its_own_full_observation(self):
        head = self.dispatch.split("    steps:", 1)[0]
        self.assertIn("needs: plan", head)
        self.assertIn("needs.plan.outputs.ready == 'true'", head)
        self.assertIn("github.repository == 'ShaishiBear/dark-factory-2.0'", head)
        self.assertIn("services:", head, "the database still belongs to the dispatch job, behind the plan")
        # The dispatcher re-observes: stop check, reap and selection stay in the effectful job.
        self.assertIn("python -m factory_kernel stop-check", self.dispatch)
        self.assertIn("run: python -m factory_kernel dispatch --once", self.dispatch)
        self.assertNotIn("plan-dispatch", self.dispatch)
        self.assertIn("concurrency:\n  group: dark-factory-worker\n  cancel-in-progress: false", self.text)

    def test_every_probe_launch_is_retained_even_when_no_run_directory_exists(self):
        head = self.dispatch.split("    steps:", 1)[0]
        self.assertIn("FACTORY_DIAGNOSTICS_DIR: ${{ runner.temp }}/dark-factory/diagnostics", head)
        retain = self.dispatch.split("- name: Retain early diagnostic records", 1)[1].split("- name:", 1)[0]
        self.assertIn("if: always()", retain)
        self.assertIn("${{ runner.temp }}/dark-factory/diagnostics/*.json", retain)
        self.assertNotIn("continue-on-error", retain)
        self.assertLess(self.dispatch.index("- name: Retain early diagnostic records"),
                        self.dispatch.index("- name: Upload run transcripts and artifacts"))
        # The pinned launch line the authority tests read is unchanged; the record path is env.
        self.assertIn("python -m factory_kernel.execution_probe -- timeout 180 claude", self.dispatch)


if __name__ == "__main__":
    unittest.main()
