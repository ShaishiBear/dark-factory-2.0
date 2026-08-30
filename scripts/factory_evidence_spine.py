#!/usr/bin/env python3
"""Outer Evidence Bundle authority: legacy verification + protected 21-claim spine closure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from factory_kernel.canonical import canonical_bytes
from factory_kernel.credential_env import scoped_environment
from factory_kernel.evidence_closure import compile_full_spine
from factory_kernel.independence import externally_supplied_claims
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


def _number(text: str, pattern: str, name: str) -> int:
    match = re.search(pattern, text)
    if not match:
        fail(f"factory mutation observation missing {name}")
    return int(match.group(1))


def observe_factory_authority(legacy: dict) -> None:
    """Re-observe factory self-mutations independently of the inner full-harness parser."""
    proc = subprocess.run(
        [sys.executable, "harness/factory_mutations/run.py"],
        cwd=ROOT,
        env=scoped_environment(scope="none"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode:
        fail("factory trust-root mutations failed during spine closure: " + text[-2500:])
    if "FACTORY_MUTATIONS_OK" not in text:
        fail("factory mutation authority did not emit FACTORY_MUTATIONS_OK")
    total = _number(text, r"FACTORY_MUTATIONS_TOTAL=(\d+)", "FACTORY_MUTATIONS_TOTAL")
    caught = _number(text, r"FACTORY_MUTATIONS_CAUGHT=(\d+)", "FACTORY_MUTATIONS_CAUGHT")
    not_injected = _number(
        text, r"FACTORY_MUTATIONS_NOT_INJECTED=(\d+)", "FACTORY_MUTATIONS_NOT_INJECTED"
    )
    immunity = re.search(
        r"IMMUNITY_OK entries=(\d+) assertions=(\d+) sha256=([0-9a-f]{64})",
        text,
    )
    if not immunity:
        fail("factory mutation authority did not emit exact immunity evidence")
    if total != caught or not_injected != 0:
        fail("factory trust-root mutation evidence is incomplete")
    observed = legacy.get("observed")
    if not isinstance(observed, dict):
        fail("core Evidence Bundle observed field is invalid")
    observed.update(
        {
            "factory_mutations_total": total,
            "factory_mutations_caught": caught,
            "factory_mutations_not_injected": not_injected,
            "immunity_entries": int(immunity.group(1)),
            "immunity_assertions": int(immunity.group(2)),
            "immunity_sha256": immunity.group(3),
        }
    )
    print(
        f"SPINE_FACTORY_MUTATIONS_OK total={total} caught={caught} "
        f"not_injected={not_injected} immunity_sha256={immunity.group(3)}"
    )


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
    observe_factory_authority(legacy)
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
        # Certificates for independence-required claims are produced by separate validator-side
        # authorities before closure. Reading them by registry means a new independent claim
        # cannot be silently left unsupplied.
        certificates = {
            claim_id: load(artifact_root / "independent" / f"{claim_id}.json")
            for claim_id in sorted(externally_supplied_claims())
        }
        manifest, index = compile_full_spine(
            repo_root=ROOT,
            artifact_root=artifact_root,
            legacy_bundle=legacy,
            builder_pack=builder_pack,
            holdout=holdout,
            architecture_holdout=architecture_holdout,
            pr_number=int(args.pr),
            independent_certificates=certificates,
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
