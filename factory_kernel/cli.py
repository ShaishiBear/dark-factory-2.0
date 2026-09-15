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
    for name in ("merge-export", "merge-import"):
        transfer = sub.add_parser(name, help="transport merge evidence within one Actions run")
        transfer.add_argument("--pr", type=int, required=True)
        transfer.add_argument("--source", type=Path, required=True)
        transfer.add_argument("--destination", type=Path, required=True)
        if name == "merge-import":
            transfer.add_argument("--sha256", required=True)

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
