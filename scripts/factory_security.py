#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_MANIFEST = "app/backend/pyproject.toml"
BACKEND_LOCK = "app/backend/uv.lock"
FRONTEND_MANIFEST = "app/frontend/package.json"
FRONTEND_LOCK = "app/frontend/bun.lock"

SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential_url", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s/@]+:[^@\s/]+@",
        re.I,
    )),
)
GENERIC_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|access[_-]?token|password)\b\s*[:=]\s*[\"']([^\"']{16,})[\"']"
)
PLACEHOLDER_WORDS = ("example", "dummy", "fake", "placeholder", "changeme", "your_", "your-", "test-only")
DISALLOWED_PY_SOURCE = re.compile(r"(?:\s@\s|git\+|https?://|file:|path:)", re.I)
DISALLOWED_JS_PREFIX = ("git", "http:", "https:", "file:", "link:", "github:", "gitlab:", "bitbucket:", "npm:")


def die(message: str) -> None:
    print(f"SECURITY_GUARD_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if check and proc.returncode:
        die(f"{' '.join(argv)} failed: {((proc.stdout or '') + (proc.stderr or ''))[-1200:]}")
    return proc


def canonical_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else requirement.strip().lower()


def backend_dependencies(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    data = tomllib.loads(text)
    result: dict[str, str] = {}
    project = data.get("project", {})
    for requirement in project.get("dependencies", []):
        result[f"runtime:{canonical_name(requirement)}"] = requirement
    for group, requirements in project.get("optional-dependencies", {}).items():
        for requirement in requirements:
            result[f"optional/{group}:{canonical_name(requirement)}"] = requirement
    for requirement in data.get("build-system", {}).get("requires", []):
        result[f"build:{canonical_name(requirement)}"] = requirement
    return result


def frontend_dependencies(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    data = json.loads(text)
    result: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in data.get(section, {}).items():
            result[f"{section}:{name.lower()}"] = str(spec)
    return result


def dependency_changes(before: dict[str, str], after: dict[str, str], ecosystem: str) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        scope, name = key.split(":", 1)
        kind = "added" if old is None else "removed" if new is None else "changed"
        changes.append({"ecosystem": ecosystem, "scope": scope, "name": name,
                        "kind": kind, "before": old or "", "after": new or ""})
    return changes


def public_dependency_changes(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {key: change[key] for key in ("ecosystem", "scope", "name", "kind")}
        for change in changes
    ]


def protected_path(path: str) -> bool:
    name = Path(path).name
    return (
        path in {
            "FACTORY_RULES.md", "MISSION.md", "CLAUDE.md",
            ".factory/kernel.json", ".factory/evidence-spine.json",
            ".factory/architecture.json", ".factory/locks/floor.json",
            "scripts/frontier_filter.py",
        }
        or path.startswith("factory_kernel/")
        or path.startswith(".factory/prompts/")
        or path.startswith(".factory/holdout/")
        or path.startswith(".github/")
        or path.startswith("deploy/systemd/")
        or path.startswith("harness/")
        or path.startswith("scripts/factory_")
        or name == "Dockerfile"
        or re.fullmatch(r"docker-compose(?:\.[^.]+)?\.ya?ml", name) is not None
        or name.startswith(".env")
    )


def dependency_justification(body: str) -> str:
    match = re.search(
        r"(?ims)^#{1,6}\s+dependency justification\s*$\s*(.*?)(?=^#{1,6}\s+|\Z)", body
    )
    return match.group(1).strip() if match else ""


def source_problem(change: dict[str, str]) -> str | None:
    if change["kind"] == "removed":
        return None
    spec = change["after"]
    if change["ecosystem"] == "python" and DISALLOWED_PY_SOURCE.search(spec):
        return "non-registry Python dependency source"
    if change["ecosystem"] == "javascript" and spec.strip().lower().startswith(DISALLOWED_JS_PREFIX):
        return "non-registry or aliased JavaScript dependency source"
    return None


def added_lines(diff: str) -> list[tuple[str, str]]:
    current = ""
    result: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+++ /dev/null"):
            current = ""
        elif current and line.startswith("+") and not line.startswith("+++"):
            result.append((current, line[1:]))
    return result


def secret_findings(lines: list[tuple[str, str]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, text in lines:
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(text) and (path, kind) not in seen:
                findings.append({"kind": kind, "path": path})
                seen.add((path, kind))
        generic = GENERIC_SECRET.search(text)
        if generic:
            value = generic.group(2).lower()
            if not any(word in value for word in PLACEHOLDER_WORDS) and (path, "generic_secret") not in seen:
                findings.append({"kind": "generic_secret", "path": path})
                seen.add((path, "generic_secret"))
    return findings


def evaluate(*, changed_files: list[str], base_backend: str, head_backend: str,
             base_frontend: str, head_frontend: str, diff: str, body: str) -> dict:
    protected = sorted(path for path in changed_files if protected_path(path))
    py_changes = dependency_changes(backend_dependencies(base_backend), backend_dependencies(head_backend), "python")
    js_changes = dependency_changes(frontend_dependencies(base_frontend), frontend_dependencies(head_frontend), "javascript")
    dep_changes = py_changes + js_changes
    justification = dependency_justification(body)
    findings: list[dict[str, str]] = []

    for path in protected:
        findings.append({"kind": "protected_path", "path": path, "detail": "protected factory/deployment path modified"})

    if py_changes and BACKEND_LOCK not in changed_files:
        findings.append({"kind": "lockfile", "path": BACKEND_LOCK, "detail": "backend dependency change without uv.lock update"})
    if js_changes and FRONTEND_LOCK not in changed_files:
        findings.append({"kind": "lockfile", "path": FRONTEND_LOCK, "detail": "frontend dependency change without bun.lock update"})

    lock_only = []
    if BACKEND_LOCK in changed_files and not py_changes:
        lock_only.append(BACKEND_LOCK)
    if FRONTEND_LOCK in changed_files and not js_changes:
        lock_only.append(FRONTEND_LOCK)
    if lock_only and not justification:
        for path in lock_only:
            findings.append({"kind": "dependency_justification", "path": path,
                             "detail": "lockfile-only change requires Dependency justification"})

    for change in dep_changes:
        problem = source_problem(change)
        if problem:
            findings.append({"kind": "dependency_source", "path": BACKEND_MANIFEST if change["ecosystem"] == "python" else FRONTEND_MANIFEST,
                             "detail": f"{change['name']}: {problem}"})
        if change["kind"] in {"added", "changed"}:
            if not justification or change["name"].lower() not in justification.lower():
                findings.append({"kind": "dependency_justification",
                                 "path": BACKEND_MANIFEST if change["ecosystem"] == "python" else FRONTEND_MANIFEST,
                                 "detail": f"{change['name']} {change['kind']} without named Dependency justification"})

    secrets = secret_findings(added_lines(diff))
    for secret in secrets:
        findings.append({"kind": "secret", "path": secret["path"],
                         "detail": f"high-confidence {secret['kind']} pattern in added line"})

    return {
        "version": "1.0",
        "verdict": "pass" if not findings else "fail",
        "protected_paths": protected,
        "dependency_changes": public_dependency_changes(dep_changes),
        "secret_findings": secrets,
        "findings": findings,
    }


def git_show(ref: str, path: str) -> str:
    proc = run(["git", "show", f"{ref}:{path}"], check=False)
    return proc.stdout if proc.returncode == 0 else ""


def worktree_text(path: str) -> str:
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.is_file() else ""


def verify_pr(pr: str) -> dict:
    meta = json.loads(run(["gh", "pr", "view", pr, "--json", "body,baseRefOid,headRefOid"]).stdout)
    base, head = meta["baseRefOid"], meta["headRefOid"]
    local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if local != head:
        die(f"validator worktree is stale: HEAD={local} PR={head}")
    changed = sorted(x for x in run(["git", "diff", "--name-only", f"{base}...{head}"]).stdout.splitlines() if x)
    diff = run(["git", "diff", "--unified=0", "--no-ext-diff", "--no-color", f"{base}...{head}"]).stdout
    return evaluate(
        changed_files=changed,
        base_backend=git_show(base, BACKEND_MANIFEST), head_backend=git_show(head, BACKEND_MANIFEST),
        base_frontend=git_show(base, FRONTEND_MANIFEST), head_frontend=git_show(head, FRONTEND_MANIFEST),
        diff=diff, body=meta.get("body") or "",
    )


def verify_worktree() -> dict:
    changed = sorted(x for x in run(["git", "diff", "--name-only", "HEAD"]).stdout.splitlines() if x)
    diff = run(["git", "diff", "--unified=0", "--no-ext-diff", "--no-color", "HEAD"]).stdout
    return evaluate(
        changed_files=changed,
        base_backend=git_show("HEAD", BACKEND_MANIFEST), head_backend=worktree_text(BACKEND_MANIFEST),
        base_frontend=git_show("HEAD", FRONTEND_MANIFEST), head_frontend=worktree_text(FRONTEND_MANIFEST),
        diff=diff, body="",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr")
    target.add_argument("--worktree", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = verify_worktree() if args.worktree else verify_pr(str(args.pr))
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    if result["verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
