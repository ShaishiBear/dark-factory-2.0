#!/usr/bin/env python3
"""Outer Evidence Bundle authority: legacy verification + protected 21-claim spine closure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from factory_kernel.canonical import canonical_bytes
from factory_kernel.credential_env import scoped_environment
from factory_kernel.evidence_closure import compile_full_spine
from factory_kernel.provenance import verify_pack

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"EVIDENCE_SPINE_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON evidence must be an object: {path}")
    return value


def run(args: argparse.Namespace) -> None:
    output = Path(args.output).resolve()
    artifact_root = output.parent
    artifact_root.mkdir(parents=True, exist_ok=True)
    core = artifact_root / "evidence-bundle-core-v5.json"

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/factory_evidence.py",
            "--pr", str(args.pr),
            "--verdict", args.verdict,
            "--architecture-verdict", args.architecture_verdict,
            "--output", str(core),
        ],
        cwd=ROOT,
        env=scoped_environment(scope="github+validation"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3000,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode:
        if proc.stderr.strip():
            print(proc.stderr.strip()[-3000:], file=sys.stderr)
        fail("core Evidence Bundle authority rejected the PR")

    legacy = load(core)
    head = str(legacy.get("head_sha") or "")
    base = str(legacy.get("base_sha") or "")
    issue = legacy.get("issue")
    if not isinstance(issue, int):
        fail("core Evidence Bundle lacks issue identity")

    provenance_dir = artifact_root / "spine"
    fetch = subprocess.run(
        [
            sys.executable,
            "scripts/factory_provenance.py",
            "fetch",
            "--head", head,
            "--base", base,
            "--issue", str(issue),
            "--output-dir", str(provenance_dir),
        ],
        cwd=ROOT,
        env=scoped_environment(scope="github"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    if fetch.stdout.strip():
        print(fetch.stdout.strip())
    if fetch.returncode:
        if fetch.stderr.strip():
            print(fetch.stderr.strip()[-2400:], file=sys.stderr)
        fail("exact-head builder provenance could not be fetched")

    builder_pack = load(provenance_dir / "builder-provenance.json")
    try:
        verify_pack(
            builder_pack,
            expected_head_sha=head,
            expected_base_sha=base,
            expected_issue=issue,
        )
        holdout = load(artifact_root / "holdout.json")
        architecture_holdout = load(Path(args.architecture_verdict).resolve())
        manifest, index = compile_full_spine(
            repo_root=ROOT,
            artifact_root=artifact_root,
            legacy_bundle=legacy,
            builder_pack=builder_pack,
            holdout=holdout,
            architecture_holdout=architecture_holdout,
            pr_number=int(args.pr),
        )
    except ValueError as exc:
        fail(str(exc))

    final = dict(legacy)
    final["spine"] = index
    final["run_manifest_sha256"] = manifest.sha256()
    final["builder_provenance_sha256"] = index["builder_provenance_sha256"]
    output.write_bytes(canonical_bytes(final))
    print(
        f"EVIDENCE_SPINE_OK head={head} claims={len(index['claims'])} "
        f"completion={index['completion_level']} manifest_sha256={manifest.sha256()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", required=True)
    parser.add_argument("--verdict", required=True)
    parser.add_argument("--architecture-verdict", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
