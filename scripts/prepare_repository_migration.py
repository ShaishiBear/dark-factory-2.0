#!/usr/bin/env python3
"""Prepare an offline repository cutover package; never performs remote effects.

The report is an operator checklist, not an attestation or merge capability.
Historical exports remain observations. Source state and the live repo are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

REQUIRED = {"repository.json", "repository-at-end.json", "repository.bundle",
            "git-restore-check.json", "issues.json", "pulls.json", "issue-comments.json",
            "workflow-runs.json", "artifacts.json", "export-errors.json",
            "rulesets.json", "secret-names-only.json", "historical-receipt-index.json"}


class MigrationRefused(ValueError):
    pass


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MigrationRefused("duplicate JSON field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def verify_archive(directory):
    root = Path(directory).resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink():
        raise MigrationRefused("manifest must be a regular file")
    manifest = read_json(manifest_path)
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        raise MigrationRefused("invalid archive file inventory")
    seen = set()
    for row in rows:
        name = row.get("path")
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                or PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in seen):
            raise MigrationRefused("invalid or duplicate archive path")
        seen.add(name)
        path = root
        for part in name.split("/"):
            path = path / part
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise MigrationRefused("archive links are forbidden")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise MigrationRefused("archive file is missing or escapes its root")
        if (type(row.get("bytes")) is not int or path.stat().st_size != row["bytes"]
                or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256")))
                or digest(path) != row["sha256"]):
            raise MigrationRefused("archive content differs from manifest")
    if not REQUIRED <= seen:
        raise MigrationRefused("required recovery records are missing")
    if manifest.get("errors") != 0 or read_json(root / "export-errors.json") != []:
        raise MigrationRefused("archive has unresolved export errors")
    first, last = (read_json(root / name) for name in ("repository.json", "repository-at-end.json"))
    if (type(first.get("id")) is not int or first["id"] <= 0
            or first.get("full_name") != manifest.get("repository")
            or (first["id"], first["full_name"]) != (last.get("id"), last.get("full_name"))):
        raise MigrationRefused("repository identity changed or is missing")
    restored = read_json(root / "git-restore-check.json")
    if restored.get("verified") is not True or not re.fullmatch(r"[a-f0-9]{40}", str(restored.get("head"))):
        raise MigrationRefused("archive has no verified Git restore")
    return {"repository_id": first["id"], "repository": first["full_name"],
            "source_sha": restored["head"], "manifest_sha256": digest(manifest_path),
            "files_verified": len(seen), "quiesced": manifest.get("consistent_cutover_snapshot") is True}


def git(directory, *arguments):
    # Local bundle operations need no credentials, inherited Git config or hooks.
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    command = ["git", "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull]
    result = subprocess.run(command + list(arguments), cwd=directory, env=env,
                            capture_output=True, timeout=180)
    if result.returncode:
        raise MigrationRefused("local Git operation failed; source remains unchanged")
    return result.stdout.decode("utf-8")


def prerequisites(account_plan, quiesced):
    blockers = ["destination identity and enforced protections have not been observed",
                "credential recovery and private Actions spending limits need verification",
                "final private host recovery and pending-effect reconciliation need verification",
                "cutover patch needs protected review and fresh baseline qualification",
                "destructive metadata loss and final switch have not been executed"]
    if account_plan not in {"pro", "team", "enterprise"}:
        blockers.insert(0, "GitHub Free/unknown plan cannot establish private protected operation")
    if not quiesced:
        blockers.insert(0, "live archive must be refreshed in a coordinated quiet window")
    return blockers


def prepare(archive, destination, *, account_plan):
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(archive) or archive.is_relative_to(destination):
        raise MigrationRefused("output must be new and separate from the source archive")
    binding = verify_archive(archive)
    destination.mkdir(parents=True, mode=0o700)
    candidate = destination / "candidate"
    git(destination, "clone", "--no-checkout", str(archive / "repository.bundle"), str(candidate))
    git(candidate, "checkout", "--detach", binding["source_sha"])
    git(candidate, "fsck", "--full")
    # Keep every historical object in the archive, but never seed active carry/lease refs.
    notes = git(candidate, "for-each-ref", "--format=%(refname)", "refs/notes").splitlines()
    if notes:
        raise MigrationRefused("local candidate unexpectedly inherited runtime notes")
    active = candidate / ".factory/programmes/active.json"
    if (active.is_symlink() or not active.is_file() or not active.resolve().is_relative_to(candidate)
            or any(parent.is_symlink() for parent in (active.parent, active.parent.parent))):
        raise MigrationRefused("expected original active programme is missing")
    active_sha = digest(active)
    active.unlink()  # Only in the new disposable clone; old programme remains in Git history.
    patch = git(candidate, "diff", "--binary", "--", ".factory/programmes/active.json")
    (destination / "bootstrap.patch").write_text(patch, encoding="utf-8", newline="\n")
    state = destination / "fresh-intents"
    state.mkdir(mode=0o700)
    report = {"schema": "dark-factory/migration-preparation", "version": "1.0",
              "status": "prepared-offline", "cutover_ready": False, "execution_authority": False,
              "source": binding, "account_plan_reported": account_plan,
              "retired_active_input_sha256": active_sha,
              "bootstrap_patch_sha256": digest(destination / "bootstrap.patch"),
              "destination_state": "empty; no approvals, jobs, budgets, receipts or consent imported",
              "blockers": prerequisites(account_plan, binding["quiesced"])}
    (destination / "preparation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-plan", choices=["free", "pro", "team", "enterprise", "unknown"], default="unknown")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.archive, args.output, account_plan=args.account_plan), indent=2))
    except (MigrationRefused, OSError, ValueError, subprocess.SubprocessError) as error:
        print("MIGRATION_PREPARATION_REFUSED: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
