#!/usr/bin/env python3
"""Publish/fetch exact-head builder provenance through a dedicated Git notes ref."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

# Code lives beside this file (HERE); the tree under test is the working directory (ROOT).
# The kernel runs every trust-root program from its own checkout of main with cwd set to the
# PR worktree, so a PR's copy of this program is never the authority that judges it (D-036).
# The repo-owned kernel is imported by module path from beside this file: the script runs
# standalone (CI, humans) and from the kernel's detached worktrees, so the code root is put on
# sys.path here rather than trusting the caller to export PYTHONPATH.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
ROOT = Path.cwd().resolve()

from factory_kernel.canonical import canonical_bytes
from factory_kernel.provenance import (
    GIT_OID, NOTE_REF, build_pack, materialize, pack_identity, pack_sha256, verify_pack,
)
from factory_kernel.worker_policy import KERNEL_COMMIT_ARGS


def fail(message: str) -> None:
    raise SystemExit(f"PROVENANCE_FAIL: {message}")


def _repo() -> str:
    value = os.environ.get("FACTORY_REPO", "").strip()
    if value:
        return value
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode or not proc.stdout.strip():
        fail("cannot resolve GitHub repository")
    return proc.stdout.strip()


def _token() -> str:
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not value:
        fail("Git notes handoff requires GH_TOKEN or GITHUB_TOKEN")
    return value


def _git_auth(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    token = _token()
    repo = _repo()
    with tempfile.TemporaryDirectory(prefix="dark-factory-note-auth-") as tmp:
        askpass = Path(tmp) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *Password*) printf '%s\\n' \"$FACTORY_GIT_TOKEN\" ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {
                "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "OPENROUTER_API_KEY", "SUPADATA_API_KEY",
            }
        }
        env.update(
            {
                "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0",
                "FACTORY_GIT_TOKEN": token,
            }
        )
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    if check and proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-2500:]
        fail(f"git {' '.join(args)} failed: {detail}")
    return proc


def _fetch_note_ref() -> None:
    repo = _repo()
    proc = _git_auth(
        [
            "fetch",
            f"https://github.com/{repo}.git",
            f"{NOTE_REF}:{NOTE_REF}",
        ],
        check=False,
    )
    if proc.returncode == 0:
        return
    detail = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if "couldn't find remote ref" in detail or "could not find remote ref" in detail:
        return
    fail("could not fetch provenance notes: " + detail[-1600:])


def _is_ancestor(base: str, head: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, head],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0


def publish(args: argparse.Namespace) -> None:
    artifacts = Path(args.artifacts).resolve()
    if not artifacts.is_dir():
        fail("ARTIFACTS_DIR does not exist")
    raw = subprocess.run(
        [
            "gh", "pr", "view", str(args.pr), "--json", "headRefOid,body",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if raw.returncode:
        fail("cannot resolve PR for provenance handoff")
    info = json.loads(raw.stdout)
    head = str(info.get("headRefOid") or "")
    # The base is the commit the branch was cut from, supplied by the kernel that cut it. It is
    # never read from GitHub's baseRefOid: that is the current tip of the base branch, which
    # moves whenever main advances mid-build, and the first production pack recorded exactly
    # that (a base that was not an ancestor of its own head; D-042).
    base = str(args.base or "")
    if not GIT_OID.fullmatch(base):
        fail("publish requires --base, the exact commit the branch was cut from")
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if local != head:
        fail("builder provenance can only be attached from the exact PR head")
    if not _is_ancestor(base, head):
        fail("builder provenance base is not an ancestor of its head")
    contract_path = artifacts / "task-contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        issue = int(contract["issue"]["number"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        fail(f"cannot resolve issue from validated contract: {exc}")

    try:
        pack = build_pack(
            artifact_root=artifacts,
            repo_root=ROOT,
            issue=issue,
            base_sha=base,
            head_sha=head,
        )
    except ValueError as exc:
        fail(str(exc))

    _fetch_note_ref()
    with tempfile.NamedTemporaryFile("wb", delete=False, prefix="dark-factory-provenance-", suffix=".json") as tmp:
        tmp.write(canonical_bytes(pack))
        note_file = Path(tmp.name)
    try:
        # `git notes add` writes a commit object on the notes ref, so it needs an author the
        # same way a worker commit does. The GitHub runner configures none; every kernel-made
        # object carries the kernel identity (D-037).
        note = subprocess.run(
            ["git", *KERNEL_COMMIT_ARGS, "notes", f"--ref={NOTE_REF}", "add", "-f", "-F", str(note_file), head],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if note.returncode:
            fail("could not create exact-head provenance note: " + ((note.stderr or note.stdout)[-1600:]))
        repo = _repo()
        _git_auth(
            [
                "push",
                f"https://github.com/{repo}.git",
                f"{NOTE_REF}:{NOTE_REF}",
            ]
        )
    finally:
        note_file.unlink(missing_ok=True)
    print(
        f"PROVENANCE_PUBLISHED pr={args.pr} head={head} "
        f"claims={len(pack['artifacts'])} sha256={pack_sha256(pack)}"
    )


def _note(head: str, *, fetch_remote: bool = True) -> dict:
    if fetch_remote:
        _fetch_note_ref()
    proc = subprocess.run(
        ["git", "notes", f"--ref={NOTE_REF}", "show", head],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode:
        fail("exact PR head has no builder provenance note")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail(f"builder provenance note is not JSON: {exc}")
    if not isinstance(value, dict):
        fail("builder provenance note is not an object")
    return value


def peek(args: argparse.Namespace) -> None:
    """Print the binding a note declares without trusting its contents.

    A consumer reads the base from here and then verifies it; it never recomputes the base from
    the current branch tip (D-042)."""
    try:
        identity = pack_identity(_note(args.head, fetch_remote=not args.local_notes))
    except ValueError as exc:
        fail(str(exc))
    if identity["head_sha"] != args.head:
        fail("builder provenance is attached to a different PR head")
    print(json.dumps(identity, sort_keys=True, separators=(",", ":")))


def fetch(args: argparse.Namespace) -> None:
    value = _note(args.head, fetch_remote=not args.local_notes)
    try:
        pack = verify_pack(
            value,
            expected_head_sha=args.head,
            expected_base_sha=args.base,
            expected_issue=args.issue,
            is_ancestor=_is_ancestor,
        )
    except ValueError as exc:
        fail(str(exc))
    output = Path(args.output_dir).resolve()
    try:
        materialize(pack, output / "builder")
    except ValueError as exc:
        fail(str(exc))
    (output / "builder-provenance.json").write_bytes(canonical_bytes(pack))
    print(
        f"PROVENANCE_FETCHED head={args.head} claims={len(pack['artifacts'])} "
        f"sha256={pack_sha256(pack)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--base", required=True, help="the exact commit the branch was cut from")
    p.set_defaults(fn=publish)
    p = sub.add_parser("peek")
    p.add_argument("--head", required=True)
    # Read the notes ref already present locally instead of fetching it first. The kernel never
    # passes this; it exists so the script's judgement can be tested against a local repository
    # without a token or a remote. The judgement itself is identical either way.
    p.add_argument("--local-notes", action="store_true")
    p.set_defaults(fn=peek)
    p = sub.add_parser("fetch")
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--local-notes", action="store_true")
    p.set_defaults(fn=fetch)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
