"""Command surface for the repo-owned Dark Factory control plane."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .manifest import RunManifest
from .runtime import FactoryStopped
from .triage import TriageEngine
from .worker_runtime import WorkerControlledRuntime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".factory" / "kernel.json"


def _emit_step_outputs(**values: str) -> None:
    """Hand the workflow what the next step needs, through GITHUB_OUTPUT when it exists.

    Printed as well as written, so a run's log says what was handed on even when this is not
    running under Actions.
    """
    import os

    destination = os.environ.get("GITHUB_OUTPUT", "")
    for key, value in values.items():
        print(f"FACTORY_OUTPUT {key}={value}", flush=True)
    if not destination:
        return
    with open(destination, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(key + "=" + value + chr(10))


def manifest_validate(path: str) -> int:
    manifest = RunManifest.load(path)
    print(f"MANIFEST_OK claims={len(manifest.claims)} sha256={manifest.sha256()}")
    return 0


def runtime(config_path: Path) -> WorkerControlledRuntime:
    return WorkerControlledRuntime(repo_root=ROOT, config=load_config(config_path))


PLAN_RECORD_LIMIT = 65536


def plan_dispatch(args) -> int:
    """`plan-dispatch`: observe, decide, write one bounded record, hand the workflow outputs.

    Constructs no runtime and no provider: the standard library, the kernel's read modules and
    an installed `gh` are all it needs. Calls nothing that dispatches, reaps or syncs. An
    explicit resume or continuation input is validated and represented as planned work; a
    typo in it refuses here rather than falling through to an ordinary dispatch. The exit
    code fails closed on a stopped, fenced or unobservable control plane so the dependent
    dispatch job cannot start; idle and a missing allowance are honest zero-exit outcomes.
    """
    import os
    import re

    from .canonical import canonical_bytes, sha256_bytes
    from .dispatch_plan import DispatchPlanner
    from .github_cli import GitHubClient

    resume_pr, resume_run = str(args.resume_pr or "").strip(), str(args.resume_run_id or "").strip()
    if bool(resume_pr) != bool(resume_run):
        print("FACTORY_PLAN_REFUSED resume needs both resume_pr and resume_run_id", flush=True)
        return 1
    if resume_pr and not (resume_pr.isdecimal() and resume_run.isdecimal() and int(resume_pr) > 0 and int(resume_run) > 0):
        print("FACTORY_PLAN_REFUSED resume inputs must be positive integers", flush=True)
        return 1
    continuation = str(args.expected_programme or "").strip()
    if continuation and not re.fullmatch(r"[0-9a-f]{64}", continuation):
        print("FACTORY_PLAN_REFUSED continuation programme must be a SHA-256 hex digest or empty", flush=True)
        return 1
    cfg = load_config(args.config)
    planner = DispatchPlanner(GitHubClient(cfg.repository, cwd=ROOT), cfg, repo_root=ROOT, observe_programme=True)
    plan = planner.plan(resume=int(resume_pr) if resume_pr else None, continuation=continuation)
    record = plan.record(resume_pr=int(resume_pr) if resume_pr else None,
                         resume_run_id=int(resume_run) if resume_run else None,
                         continuation_programme=continuation, repository=cfg.repository)
    raw = canonical_bytes(record)
    if len(raw) > PLAN_RECORD_LIMIT:
        raise ValueError("dispatch plan record exceeds its bound")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output)
    digest = sha256_bytes(raw)
    # Reconciliation is effectful work only the dispatcher may do (it reaps under its own
    # authority before selecting); the plan cannot select past it, so the job must run.
    ready = plan.status == "ready" or "reconciliation_required" in plan.reason_codes
    print(f"FACTORY_PLAN status={plan.status} action={plan.action or '-'} "
          f"subject={plan.subject if plan.subject is not None else '-'} "
          f"reasons={','.join(plan.reason_codes) or '-'} ready={'true' if ready else 'false'} "
          f"mutations={plan.mutation_count} sha256={digest}", flush=True)
    _emit_step_outputs(ready="true" if ready else "false", plan_sha256=digest, plan_status=plan.status,
                       plan_action=plan.action or "", plan_reasons=",".join(plan.reason_codes))
    if plan.status == "blocked" and any(code in {"control_unobserved", "stopped", "fenced"} for code in plan.reason_codes):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m factory_kernel")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest-validate")
    manifest.add_argument("path", type=Path)

    sub.add_parser("config-check")
    sub.add_parser("stop-check")
    sub.add_parser("reap")
    sub.add_parser("triage")
    programme_check = sub.add_parser("programme-check", help="compile a proposal without effects")
    programme_check.add_argument("path", type=Path)
    programme_sync = sub.add_parser("programme-sync", help="materialize one ready programme candidate via the App")
    programme_sync.add_argument("--expected-programme", default="")
    continuation = sub.add_parser("programme-continue", help="continue successful approved work within a fixed limit")
    for field in ("programme", "remaining", "advanced", "dispatch-result", "merge-result"):
        continuation.add_argument("--" + field, required=True)
    pulse = sub.add_parser("programme-pulse", help="validate and record bounded continuation inputs")
    for field in ("programme", "remaining", "parent"):
        pulse.add_argument("--" + field, required=True)
    sub.add_parser("programme-status", help="read programme progress without models or effects")
    for name in ("merge-export", "merge-import"):
        transfer = sub.add_parser(name, help="transport merge evidence within one Actions run")
        transfer.add_argument("--pr", type=int, required=True)
        transfer.add_argument("--source", type=Path, required=True)
        transfer.add_argument("--destination", type=Path, required=True)
        if name == "merge-import":
            transfer.add_argument("--sha256", required=True)

    # Read-only planning (WP00/R00): the same stop/fence/lease/priority observation the
    # dispatcher makes, decided purely, with zero mutations. Its outputs tell the workflow
    # whether a dispatch job is worth starting at all; they are never execution authority.
    plan = sub.add_parser("plan-dispatch", help="observe and decide whether a dispatch is needed, without effects")
    plan.add_argument("--output", type=Path, required=True, help="where to write the bounded plan record")
    plan.add_argument("--resume-pr", default="", help="operator resume input (needs --resume-run-id)")
    plan.add_argument("--resume-run-id", default="", help="operator resume input (needs --resume-pr)")
    plan.add_argument("--expected-programme", default="", help="continuation programme hash, or empty")

    # Claim views (WP04, shadow). Both read retained records and observed inputs and write a
    # bounded JSON record; neither contacts GitHub, a provider or the intent store's writers.
    for name, help_text in (("explain-claims", "project requirement/obligation claims and the graph over them, without effects"),
                            ("plan-obligations", "compile allowed actions in shadow and compare them with a recorded dispatch plan")):
            command = sub.add_parser(name, help=help_text)
            command.add_argument("--state-dir", type=Path, required=True, help="the Front Door intent store directory")
            command.add_argument("--owner", required=True, help="the configured owner identity (host operator)")
            command.add_argument("--project", required=True)
            command.add_argument("--programme", type=Path, default=None, help="programme candidate JSON (compile_programme input)")
            command.add_argument("--proof", type=Path, default=None, help="artifact root holding spine/attestations/index.json")
            command.add_argument("--observations", type=Path, default=None, help="JSON of trusted observations (items, heads, control)")
            command.add_argument("--output", type=Path, required=True)
            if name == "plan-obligations":
                command.add_argument("--dispatch-plan", type=Path, required=True, help="a plan-dispatch record to compare with")

    # Transition status (WP03, read-only): derive a governed replacement's phase from its journal
    # receipts and decide release eligibility from an observation file. No remote effect.
    transition = sub.add_parser("transition-status", help="derive a programme transition's phase and release eligibility, without effects")
    transition.add_argument("--state-dir", type=Path, required=True, help="the Front Door intent store directory")
    transition.add_argument("--owner", required=True, help="the configured owner identity (host operator)")
    transition.add_argument("--project", required=True)
    transition.add_argument("--transition", required=True, help="the transition id (64 hex)")
    transition.add_argument("--observation", type=Path, default=None, help="JSON of the independently observed release preconditions")
    transition.add_argument("--store-state", default=None, help="the effect store's grant state for the pending request, if known")
    transition.add_argument("--output", type=Path, required=True)

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--once", action="store_true", help="execute exactly one priority item")
    dispatch.add_argument("--no-merge", action="store_true")
    # Validate and authorise, but leave the merge to a step with a fresher identity.
    dispatch.add_argument("--defer-merge", action="store_true")
    dispatch.add_argument("--defer-publication", action="store_true")
    publish = sub.add_parser("publish", help="publish a prepared build behind a fresh App identity")
    publish.add_argument("--handoff", type=Path, required=True)
    publish.add_argument("--sha256", required=True)

    build = sub.add_parser("build")
    build.add_argument("--issue", type=int, required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--pr", type=int, required=True)
    validate.add_argument("--no-merge", action="store_true")

    # The merge as its own invocation, so the workflow can mint a fresh identity immediately
    # before it. Decides nothing: it reads the authorization the evidence already produced
    # (ACP-004).
    merge = sub.add_parser(
        "merge", help="merge a PR the evidence already authorised, behind a fresh identity"
    )
    merge.add_argument("--pr", type=int, required=True)
    merge.add_argument(
        "--from-authorization",
        required=True,
        help="artifacts directory holding merge-authorization.json and evidence-bundle.json",
    )

    rehead = sub.add_parser("rehead", help="re-head a stale-base refused PR onto current main")
    rehead.add_argument("--pr", type=int, required=True)

    resume = sub.add_parser(
        "resume",
        help="finish a pushed-but-unpublished factory PR from its uploaded build artifacts",
    )
    resume.add_argument("--pr", type=int, required=True)
    resume.add_argument("--artifacts", type=Path, required=True,
                        help="directory holding the run's artifacts/*.json (gh run download)")

    args = parser.parse_args()
    if args.command == "manifest-validate":
        return manifest_validate(str(args.path))
    if args.command == "config-check":
        cfg = load_config(args.config)
        print(
            f"KERNEL_CONFIG_OK repo={cfg.repository} provider={cfg.provider.provider_id} "
            f"prompts={len(cfg.prompts)}"
        )
        return 0
    if args.command == "programme-check":
        from .programme import compile_programme, parse_json
        cfg = load_config(args.config)
        compiled = compile_programme(parse_json(args.path.read_text(encoding="utf-8")),
                                     repository=cfg.repository)
        print(f"PROGRAMME_STRUCTURALLY_VALID sha256={compiled.sha256} items={len(compiled.items)}")
        return 0

    if args.command in {"merge-export", "merge-import"}:
        import os
        import subprocess
        from .merge_handoff import export_handoff, import_handoff

        cfg = load_config(args.config)
        run, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (not run.isdecimal() or not attempt.isdecimal() or args.pr <= 0
                or os.environ.get("GITHUB_REPOSITORY") != cfg.repository):
            raise ValueError("merge transport requires this repository's canonical Actions run")
        kernel = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        subject = {"repository": cfg.repository, "run": run, "attempt": attempt,
                   "kernel": kernel, "pr": args.pr}
        if args.command == "merge-export":
            digest = export_handoff(args.source, args.destination, subject=subject)
            _emit_step_outputs(merge_handoff_sha256=digest, kernel_sha=kernel)
        else:
            import_handoff(args.source, args.destination, expected_sha256=args.sha256, subject=subject)
        return 0

    if args.command == "plan-dispatch":
        return plan_dispatch(args)

    if args.command in {"explain-claims", "plan-obligations"}:
        from .claim_views import explain_claims, plan_obligations

        cfg = load_config(args.config)
        if args.command == "explain-claims":
            return explain_claims(args, cfg)
        return plan_obligations(args, cfg)

    if args.command == "transition-status":
        from .transition_views import transition_status

        cfg = load_config(args.config)
        return transition_status(args, cfg)

    if args.command == "programme-pulse":
        import json
        from .continuation import pulse_context

        print("FACTORY_PULSE " + json.dumps(pulse_context(
            programme=args.programme, remaining=args.remaining, parent=args.parent), sort_keys=True))
        return 0

    if args.command == "programme-status":
        import json
        from .github_cli import GitHubClient
        from .programme_runtime import ProgrammeQueue

        cfg = load_config(args.config)
        github = GitHubClient(cfg.repository, cwd=ROOT)
        print("FACTORY_PROGRAMME_STATUS " + json.dumps(
            ProgrammeQueue(github, cfg.default_branch).status(cfg.labels), sort_keys=True))
        return 0

    rt = runtime(args.config)
    try:
        if args.command == "programme-sync":
            import json
            from .programme_runtime import ProgrammeQueue
            status = ProgrammeQueue(rt.github, rt.config.default_branch).sync(
                rt.check_stop, expected_programme=args.expected_programme)
            print("FACTORY_PROGRAMME " + json.dumps(status, sort_keys=True))
            _emit_step_outputs(programme_sha256=status.get("programme", ""))
            return 0
        if args.command == "programme-continue":
            import json
            import os
            from .continuation import continue_programme
            from .programme_runtime import ProgrammeQueue

            result = continue_programme(
                ProgrammeQueue(rt.github, rt.config.default_branch), rt.check_stop,
                programme=args.programme, remaining=args.remaining, advanced=args.advanced,
                dispatch_result=args.dispatch_result, merge_result=args.merge_result,
                context=os.environ)
            print("FACTORY_CONTINUATION " + json.dumps(result, sort_keys=True))
            return 0
        if args.command == "stop-check":
            rt.check_stop()
            print("KERNEL_STOP_CHECK_OK")
            return 0
        if args.command == "reap":
            rt.check_stop()
            rt.reap_stale_claims()
            print("KERNEL_REAP_OK")
            return 0
        if args.command == "triage":
            count = TriageEngine(rt).run_once()
            print(f"KERNEL_TRIAGE decisions={count}")
            return 0
        if args.command == "dispatch":
            if not args.once:
                parser.error("dispatch currently requires --once; scheduling belongs outside kernel")
            rt.defer_publication = args.defer_publication
            decision = rt.dispatch_once(
                merge=not (args.no_merge or args.defer_merge)
            )
            if args.defer_merge and rt.pending_merge is not None:
                pr_number, authorization = rt.pending_merge
                _emit_step_outputs(
                    merge_pr=str(pr_number),
                    merge_authorization=str(authorization.parent),
                )
            prepared = getattr(rt, "pending_publication", None)
            if prepared is not None:
                from .canonical import sha256_file
                _emit_step_outputs(publication_handoff=str(prepared), publication_sha256=sha256_file(prepared))
            advanced = decision.kind in {"build-issue", "validate-pr", "rehead-pr"}
            if decision.kind == "idle":
                count = TriageEngine(rt).run_once()
                advanced = count > 0
                print(f"KERNEL_DISPATCH kind=triage decisions={count}")
            else:
                print(
                    f"KERNEL_DISPATCH kind={decision.kind} "
                    f"number={decision.number if decision.number is not None else '-'} "
                    f"reason={decision.reason!r}"
                )
            _emit_step_outputs(action_advanced="true" if advanced else "false")
            return 0
        if args.command == "build":
            pr = rt.build_issue(args.issue)
            print(f"KERNEL_BUILD_OK issue={args.issue} pr={pr}")
            return 0
        if args.command == "publish":
            pr = rt.publish_prepared(args.handoff, expected_sha256=args.sha256)
            print(f"KERNEL_PUBLICATION_OK pr={pr}")
            return 0
        if args.command == "merge":
            output = rt.merge_authorized(args.pr, artifacts=Path(args.from_authorization))
            print(output)
            return 0
        if args.command == "validate":
            output = rt.validate_pr(args.pr, merge=not args.no_merge)
            print(f"KERNEL_VALIDATE_OK pr={args.pr} output={output}")
            return 0
        if args.command == "rehead":
            new_head = rt.rehead_pr(args.pr)
            print(f"KERNEL_REHEAD_OK pr={args.pr} head={new_head}")
            return 0
        if args.command == "resume":
            head = rt.resume_pr(args.pr, args.artifacts)
            print(f"KERNEL_RESUME_OK pr={args.pr} head={head}")
            return 0
    except FactoryStopped as exc:
        print(f"KERNEL_STOPPED {exc}")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
