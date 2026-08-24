#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ENV = (
    "DATABASE_URL", "OPENROUTER_API_KEY", "JWT_SECRET", "SUPADATA_API_KEY",
    "YOUTUBE_CHANNEL_ID", "DARK_FACTORY_E2E_EMAIL", "DARK_FACTORY_E2E_PASSWORD",
)
OID = re.compile(r"^[0-9a-f]{40,64}$")


def die(message: str) -> None:
    print(f"OBSERVATION_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def run(argv: list[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )
    if check and proc.returncode:
        die(f"{' '.join(argv)} failed: {((proc.stdout or '') + (proc.stderr or ''))[-1600:]}")
    return proc


def oid(rev: str) -> str:
    value = run(["git", "rev-parse", rev], timeout=30).stdout.strip()
    if not OID.fullmatch(value):
        die(f"cannot resolve git object id for {rev}")
    return value


def number(text: str, pattern: str, name: str) -> int:
    match = re.search(pattern, text)
    if not match:
        die(f"full harness transcript is missing {name}")
    return int(match.group(1))


def parse_transcript(text: str) -> dict:
    if "GATE_OK mode=full" not in text:
        die("full harness did not reach GATE_OK")
    if "APP_STARTED" not in text:
        die("full harness never emitted APP_STARTED")
    observed = {
        "static_checks": number(text, r"STATIC_OK checks=(\d+)", "STATIC_OK count"),
        "unit_tests": number(text, r"UNIT_PASSED tests=(\d+)", "UNIT_PASSED count"),
        "e2e_steps": number(text, r"E2E_PASSED steps=(\d+)", "E2E_PASSED count"),
        "holdout_assertions": number(text, r"HOLDOUT_PASSED[^\n]*assertions=(\d+)", "HOLDOUT assertions"),
        "mutations_total": number(text, r"MUTATIONS_TOTAL=(\d+)", "MUTATIONS_TOTAL"),
        "mutations_caught": number(text, r"MUTATIONS_CAUGHT=(\d+)", "MUTATIONS_CAUGHT"),
        "mutations_independent_caught": number(text, r"MUTATIONS_INDEPENDENT_CAUGHT=(\d+)", "MUTATIONS_INDEPENDENT_CAUGHT"),
        "mutations_security_caught": number(text, r"MUTATIONS_SECURITY_CAUGHT=(\d+)", "MUTATIONS_SECURITY_CAUGHT"),
        "mutations_not_injected": number(text, r"MUTATIONS_NOT_INJECTED=(\d+)", "MUTATIONS_NOT_INJECTED"),
        "factory_mutations_total": number(text, r"FACTORY_MUTATIONS_TOTAL=(\d+)", "FACTORY_MUTATIONS_TOTAL"),
        "factory_mutations_caught": number(text, r"FACTORY_MUTATIONS_CAUGHT=(\d+)", "FACTORY_MUTATIONS_CAUGHT"),
        "factory_mutations_not_injected": number(text, r"FACTORY_MUTATIONS_NOT_INJECTED=(\d+)", "FACTORY_MUTATIONS_NOT_INJECTED"),
        "immunity_entries": number(text, r"IMMUNITY_OK entries=(\d+)", "IMMUNITY_OK entries"),
        "immunity_assertions": number(text, r"IMMUNITY_OK entries=\d+ assertions=(\d+)", "IMMUNITY_OK assertions"),
    }
    immunity_hash = re.search(r"IMMUNITY_OK[^\n]*sha256=([0-9a-f]{64})", text)
    if not immunity_hash:
        die("full harness transcript is missing immunity registry hash")
    observed["immunity_registry_sha256"] = immunity_hash.group(1)
    if observed["e2e_steps"] < 1:
        die("observed E2E step count is zero")
    if observed["mutations_total"] != observed["mutations_caught"] or observed["mutations_not_injected"] != 0:
        die("application mutation observation is incomplete")
    if observed["factory_mutations_total"] != observed["factory_mutations_caught"] or observed["factory_mutations_not_injected"] != 0:
        die("factory meta-mutation observation is incomplete")
    return observed


def tree_clean() -> bool:
    return not run(["git", "status", "--porcelain"], timeout=30).stdout.strip()


def observe(*, allow_non_main: bool = False) -> dict:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        die("validation environment is missing required names: " + ", ".join(missing))
    if not tree_clean():
        die("validation-host observation requires a clean worktree")

    run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    head_before, main_sha = oid("HEAD"), oid("origin/main")
    tree_before = oid("HEAD^{tree}")
    ratchet_eligible = head_before == main_sha
    if not ratchet_eligible and not allow_non_main:
        die("ratchet observation requires HEAD == origin/main; use --allow-non-main for diagnostics only")

    harness = run([sys.executable, "harness/ci.py"], timeout=3600, check=False)
    transcript = (harness.stdout or "") + (harness.stderr or "")
    if harness.returncode:
        die("full validation-host harness failed: " + transcript[-2000:])
    observed = parse_transcript(transcript)

    head_after, tree_after = oid("HEAD"), oid("HEAD^{tree}")
    if head_after != head_before or tree_after != tree_before:
        die("repository HEAD/tree changed during validation-host observation")
    if not tree_clean():
        die("full harness left the validation-host worktree dirty")

    return {
        "version": "1.0", "verdict": "observed",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ratchet_eligible": ratchet_eligible,
        "head_sha": head_before, "tree_sha": tree_before, "origin_main_sha": main_sha,
        "harness_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "observed": observed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-non-main", action="store_true")
    args = parser.parse_args()
    result = observe(allow_non_main=args.allow_non_main)
    Path(args.output).write_bytes(canonical(result))
    print(
        "VPS_OBSERVATION_OK "
        f"head={result['head_sha']} e2e_steps={result['observed']['e2e_steps']} "
        f"ratchet_eligible={str(result['ratchet_eligible']).lower()} "
        f"harness_sha256={result['harness_sha256']}"
    )


if __name__ == "__main__":
    main()
