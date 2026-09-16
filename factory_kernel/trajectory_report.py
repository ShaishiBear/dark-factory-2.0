"""Read a local trajectory snapshot and write an advisory report to stdout."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from .canonical import canonical_bytes, sha256_bytes
from .programme import parse_json
from .trajectory import MAX_RECORD
from .trajectory_analysis import AnalysisRefused, MAX_RECORDS, build_report

MAX_SNAPSHOT_BYTES = 64000000


def load_snapshot(root: Path) -> tuple[list[dict], list[dict]]:
    if root.is_symlink() or not root.is_dir():
        raise AnalysisRefused("snapshot must be a directory, not a symlink")
    paths = []
    for path in root.iterdir():
        if len(paths) >= MAX_RECORDS:
            raise AnalysisRefused("snapshot inventory over bound")
        paths.append(path)
    records, sources, total = [], [], 0
    for path in sorted(paths):
        if (not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*\.json", path.name)
                or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())):
            raise AnalysisRefused("snapshot contains an unexpected entry")
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECORD + 1)
        total += len(raw)
        if len(raw) > MAX_RECORD or total > MAX_SNAPSHOT_BYTES:
            raise AnalysisRefused("snapshot bytes over bound")
        value = parse_json(raw.decode("utf-8"))
        if not isinstance(value, dict) or path.name != f"{value.get('run_id')}-{value.get('run_attempt')}.json":
            raise AnalysisRefused("snapshot filename does not match its observation")
        records.append(value)
        sources.append({"file": path.name, "sha256": sha256_bytes(raw), "bytes": len(raw)})
    return records, sources


def markdown(report: dict) -> str:
    coverage, totals = report["coverage"], report["totals"]
    cost = totals["agent_cost_usd"]

    def number(value: float | int | None) -> str:
        return "unknown" if value is None else f"{value:.6f}".rstrip("0").rstrip(".")

    lines = ["# Factory run observations", "", f"Repository: {report['repository']}", "",
             "Advisory only. No qualification, completion or proof-reuse authority.", "",
             f"Input set: `{report['input_set_sha256']}`", "",
             f"Accepted workflow attempts: {coverage['accepted_attempts']}; rejected: {coverage['rejected_records']}; "
             f"conflicting identities: {coverage['conflicting_identities']}; duplicates: {coverage['identical_duplicates']}.",
             f"Input complete: {str(coverage['input_complete']).lower()} (supplied snapshot only). "
             f"Runs without stages: {coverage['runs_without_stages']}; with collection gaps: {coverage['runs_with_collection_gaps']}.", "",
             f"Recorded agent cost: USD {number(cost['sum'])}; measured stages: {cost['observed']}; "
             f"unknown-cost stages: {cost['missing']}.",
             f"Non-ok stage observations: {totals['non_ok_observations']}; "
             f"repair invocations: {totals['repair_invocations']}; repeated stage observations: {report['repeated_stage_observations']}.",
             "Verified completions, cost per verified completion and human interventions: unavailable.", "",
             "## Workflow outcomes", ""]
    lines.extend(f"- {key}: {count}" for key, count in report["workflow_outcomes"].items())
    lines += ["", "## Stage and model measurements", "",
              "Stage seconds use nearest-rank percentiles; sums may overlap. Unknown model/effort remain separate.", "",
              "| Kind / stage / model / effort | Observations | Non-ok | Recorded USD | Missing cost | Seconds p50 / p95 |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in report["by_stage_model"]:
        money, seconds = row["agent_cost_usd"], row["stage_seconds"]
        lines.append(f"| {row['kind']} / {row['stage']} / {row['model']} / {row['effort']} | "
                     f"{row['stage_observations']} | {row['non_ok_observations']} | "
                     f"{number(money['sum']) if row['kind'] == 'agent' else 'n/a'} | {money['missing']} | "
                     f"{number(seconds['p50'])} / {number(seconds['p95'])} |")
    lines += ["", "## Non-ok stage observations", ""]
    for row in report["non_ok_stages"]:
        lines.append(f"- [Run {row['run_id']}/{row['run_attempt']}]({row['url']}): "
                     f"{row['phase']} / {row['kernel_run']} / {row['stage']} #{row['ordinal']}: "
                     f"{row['result']}; workflow {row['workflow_outcome']}. No causal inference.")
    if not report["non_ok_stages"]:
        lines.append("None in accepted observations; missing stages are not evidence of success.")
    lines += ["", "## Collection gaps", ""]
    lines.extend(f"- {row['scope']} / {row['phase']} / {row['reason']}: {row['count']}"
                 for row in report["collection_gaps"])
    lines += ["", "## Attempt inventory", ""]
    lines.extend(f"- [Run {row['run_id']}/{row['run_attempt']}]({row['url']}): "
                 f"{row['workflow_outcome']}; {row['metrics']['stage_observations']} stages; "
                 f"input `{row['input_sha256']}`." for row in report["runs"])
    lines += ["", "## Limits", "", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        records, sources = load_snapshot(args.archive)
        report = build_report(records, repository=args.repository)
        report["source_files"] = sources
        output = canonical_bytes(report).decode("utf-8") if args.format == "json" else markdown(report)
        sys.stdout.write(output)
        return 0 if report["coverage"]["input_complete"] else 2
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, RecursionError):
        # Do not echo malformed source text, file paths, credentials or private payloads.
        sys.stderr.write("TRAJECTORY_REPORT_REFUSED: invalid or unbounded snapshot\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
