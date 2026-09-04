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


# The application's own security surface, from CLAUDE.md "Protected paths" / MISSION.md §10.
# These are not factory trust root -- they are product code -- but they implement or gate the
# invariants a factory run must never quietly relax: authentication, owner-only conversation
# access, the hardcoded message cap, the signup abuse guard and their audit tables.
#
# The blinded holdout defends three of those invariants behaviourally (owner-only access, the cap
# being one number, per-user lock keying), which is stronger than a filename check because it
# survives a refactor. It does not cover token issuance and verification, password hashing, the
# admin dependency, the signup guard, or CORS. Those had neither a behavioural detector nor a path
# check, so an autonomous run could have widened them and no deterministic gate would have refused.
#
# CORS is the one entry CLAUDE.md states as a property rather than a path ("anywhere in the
# backend"). A path list cannot express that; main.py is where the middleware is installed, so
# protecting it covers the real call site without claiming more than a path check can deliver.
APPLICATION_SECURITY_PATHS = frozenset({
    "app/backend/config.py",
    "app/backend/main.py",
    "app/backend/rate_limit.py",
    "app/backend/signup_rate_limit.py",
    "app/backend/db/repository.py",
    "app/backend/db/signup_attempts_repo.py",
    "app/backend/db/user_messages_repo.py",
    "app/backend/db/users_repo.py",
    "app/backend/routes/admin.py",
    "app/backend/routes/auth.py",
    "app/backend/routes/conversations.py",
    "app/backend/routes/messages.py",
})


# Trust-root maintenance lane.
#
# The protected paths below are the judge. The autonomous factory must never be able to rewrite
# its own judge -- but a judge nobody can maintain is a different defect. PR #36 was itself a human
# security correction to this guard, and this guard refused it, while branch protection forbade the
# direct push to main that FACTORY_RULES used to prescribe. No compliant maintenance route existed.
#
# The lane is keyed to GitHub platform identity: the PR's resolved user object and its repository
# association, as GitHub reports them. It is never keyed to commit author/committer text, which
# anyone can type into a commit. The factory opens its PRs with the Actions token, so they resolve
# to a Bot; a maintainer's PR resolves to a User holding a repository role. Being human waives
# exactly one finding -- the protected-path veto that exists to keep the factory out of the trust
# root. Secret scanning, dependency policy and every later rung of quick-authority still run.
HUMAN_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def human_maintainer(actor: dict | None) -> bool:
    """True only for a GitHub user account that holds a role on this repository."""
    if not isinstance(actor, dict):
        return False
    return actor.get("type") == "User" and actor.get("association") in HUMAN_ASSOCIATIONS


def resolved_user(actor: dict | None) -> bool:
    return isinstance(actor, dict) and actor.get("type") == "User" and bool(actor.get("login"))


def commit_provenance_problems(commits: list[dict] | None) -> list[dict[str, str]]:
    """Second fence behind the PR-identity rule, not the proof of human control.

    Every commit a human PR carries into the trust root must itself resolve, on GitHub's side, to a
    user account for both author and committer. The kernel commits as github-actions[bot]
    identity and the Actions token commits as a Bot, so a factory commit pushed onto a human's
    branch shows up here as unresolved or Bot even though the PR author is human. Unknown
    provenance fails closed for the same reason.
    """
    if commits is None:
        return [{"kind": "protected_path_provenance", "path": "", "commit": "",
                 "detail": "commit provenance unavailable; trust-root changes need every commit "
                           "attributable to a GitHub user account"}]
    problems: list[dict[str, str]] = []
    for commit in commits:
        sha = str(commit.get("sha") or "")[:12]
        for role in ("author", "committer"):
            if not resolved_user(commit.get(role)):
                problems.append({"kind": "protected_path_provenance", "path": "", "commit": sha,
                                 "detail": f"commit {sha or '?'} {role} is not a resolved GitHub user account"})
    return problems


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
        or path.startswith(".factory/methods/")
        or path.startswith(".factory/holdout/")
        or path.startswith(".factory/benchmark/")
        or path.startswith(".github/")
        or path.startswith("deploy/systemd/")
        or path.startswith("harness/")
        # The factory's own tests are detectors, not product code: they are what turns an injected
        # trust-root mutation into a red suite. A mutation whose sole detector was quietly weakened
        # still escapes loudly, but a property with no corresponding mutation had no signal at all.
        or path.startswith("tests/factory/")
        or path.startswith("scripts/factory_")
        or path in APPLICATION_SECURITY_PATHS
        # Every auth module: token issuance, verification, password hashing, the request
        # dependencies the routes gate on. Protecting the routes but not what they call would
        # leave the invariant reachable one import away.
        or path.startswith("app/backend/auth/")
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


FLOOR_FILE = ".factory/locks/floor.json"


def ratchet_regressions(base_text: str, head_text: str) -> list[dict[str, str]]:
    """The ratchet is monotonic: every numeric floor present at the base must be present at the
    head and at least as high. Notes (keys starting with `_`) are free text. New keys may be
    added; that is how a floor is first measured. This runs on both lanes: the human lane waives
    the protected-path veto and nothing else, so a maintainer PR that lowers a floor fails here
    just as a factory PR would.
    """
    try:
        base = json.loads(base_text) if base_text.strip() else {}
    except json.JSONDecodeError:
        base = {}
    try:
        head = json.loads(head_text)
    except json.JSONDecodeError:
        return [{"kind": "ratchet_regression", "path": FLOOR_FILE, "detail": "floor file is not valid JSON at the head"}]
    if not isinstance(base, dict) or not isinstance(head, dict):
        return [{"kind": "ratchet_regression", "path": FLOOR_FILE, "detail": "floor file is not a JSON object"}]
    findings: list[dict[str, str]] = []
    for key, value in sorted(base.items()):
        if key.startswith("_") or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        after = head.get(key)
        if isinstance(after, bool) or not isinstance(after, (int, float)):
            findings.append({"kind": "ratchet_regression", "path": FLOOR_FILE,
                             "detail": f"{key} removed (was {value})"})
        elif after < value:
            findings.append({"kind": "ratchet_regression", "path": FLOOR_FILE,
                             "detail": f"{key} lowered from {value} to {after}"})
    return findings


def evaluate(*, changed_files: list[str], base_backend: str, head_backend: str,
             base_frontend: str, head_frontend: str, diff: str, body: str,
             author: dict | None = None, commits: list[dict] | None = None,
             base_floor: str = "", head_floor: str = "") -> dict:
    protected = sorted(path for path in changed_files if protected_path(path))
    lane = "human-maintenance" if human_maintainer(author) else "autonomous"
    py_changes = dependency_changes(backend_dependencies(base_backend), backend_dependencies(head_backend), "python")
    js_changes = dependency_changes(frontend_dependencies(base_frontend), frontend_dependencies(head_frontend), "javascript")
    dep_changes = py_changes + js_changes
    justification = dependency_justification(body)
    findings: list[dict[str, str]] = []

    if protected and lane == "autonomous":
        for path in protected:
            findings.append({"kind": "protected_path", "path": path, "detail": "protected factory/deployment path modified"})
    elif protected:
        findings.extend(commit_provenance_problems(commits))

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

    if FLOOR_FILE in changed_files:
        findings.extend(ratchet_regressions(base_floor, head_floor))

    verdict = "pass" if not findings else "fail"
    return {
        "version": "1.0",
        "verdict": verdict,
        "protected_paths": protected,
        "authority": {
            "lane": lane,
            "author": str((author or {}).get("login") or ""),
            "protected_paths_permitted": lane == "human-maintenance",
            # Unattended merge is a maintainer-lane property. An autonomous PR is merged only
            # by the kernel after the full evidence ladder; the guard passing is nowhere near
            # enough for it, so the eligibility bit is never set on the autonomous lane.
            "unattended_merge_eligible": lane == "human-maintenance" and verdict == "pass",
        },
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


def concatenated_json(text: str) -> list:
    """`gh api --paginate` emits one JSON document per page, back to back."""
    decoder = json.JSONDecoder()
    values: list = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            return values
        value, index = decoder.raw_decode(text, index)
        values.append(value)


def github_actor(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"login": str(raw.get("login") or ""), "type": str(raw.get("type") or "")}


def pr_identity(pr: str) -> tuple[dict, list[dict]]:
    """GitHub's view of who opened the PR and who its commits resolve to. Platform identity only."""
    info = json.loads(run(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"]).stdout)
    author = github_actor(info.get("user")) or {"login": "", "type": ""}
    author["association"] = str(info.get("author_association") or "")
    pages = concatenated_json(run(["gh", "api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{pr}/commits"]).stdout)
    commits: list[dict] = []
    for page in pages:
        for item in page if isinstance(page, list) else []:
            commits.append({
                "sha": str(item.get("sha") or ""),
                "author": github_actor(item.get("author")),
                "committer": github_actor(item.get("committer")),
            })
    return author, commits


def repository_name() -> str:
    return str(json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner"]).stdout).get("nameWithOwner") or "")


def bound(result: dict, *, mode: str, pr: str, base: str, head: str, changed: list[str]) -> dict:
    """Bind the verdict to exactly what was judged, so a consumer can refuse a stale result."""
    result["binding"] = {
        "mode": mode, "repository": repository_name(), "pr": int(pr),
        "base_sha": base, "head_sha": head, "changed_files": list(changed),
    }
    return result


def verify_pr(pr: str) -> dict:
    """Head mode: the validator worktree IS the PR head. Used by the head-based quick gate."""
    meta = json.loads(run(["gh", "pr", "view", pr, "--json", "body,baseRefOid,headRefOid"]).stdout)
    author, commits = pr_identity(pr)
    base, head = meta["baseRefOid"], meta["headRefOid"]
    local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if local != head:
        die(f"validator worktree is stale: HEAD={local} PR={head}")
    changed = sorted(x for x in run(["git", "diff", "--name-only", f"{base}...{head}"]).stdout.splitlines() if x)
    diff = run(["git", "diff", "--unified=0", "--no-ext-diff", "--no-color", f"{base}...{head}"]).stdout
    result = evaluate(
        changed_files=changed,
        base_backend=git_show(base, BACKEND_MANIFEST), head_backend=git_show(head, BACKEND_MANIFEST),
        base_frontend=git_show(base, FRONTEND_MANIFEST), head_frontend=git_show(head, FRONTEND_MANIFEST),
        diff=diff, body=meta.get("body") or "",
        author=author, commits=commits,
        base_floor=git_show(base, FLOOR_FILE), head_floor=git_show(head, FLOOR_FILE),
    )
    return bound(result, mode="head", pr=pr, base=base, head=head, changed=changed)


def verify_pr_trusted_base(pr: str, *, expect_base: str | None, expect_head: str | None) -> dict:
    """Trusted-base mode: this program runs from a commit already on the protected branch and
    judges the PR head as data. The PR cannot supply the guard that judges it.

    The trust comes from the caller: the trust-root workflow runs from the base of the pull
    request (a `pull_request_target` event executes the workflow definition from `main`) and
    checks out `github.sha`, the base tip; the kernel validator runs from its `main` checkout.
    This function verifies the shape of that promise -- HEAD is not the PR head, HEAD is reachable
    from the base branch, the fetched head is exactly the head GitHub reports, and any expected
    identities the caller passed match -- and refuses to run from the PR head.
    """
    meta = json.loads(run(["gh", "pr", "view", pr, "--json", "body,baseRefName,baseRefOid,headRefOid"]).stdout)
    author, commits = pr_identity(pr)
    head = str(meta["headRefOid"])
    base_ref = str(meta.get("baseRefName") or "main")
    local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if local == head:
        die("trusted-base mode refuses to run from the PR head: the change would be judging itself")
    if expect_base and local != expect_base:
        die(f"trusted base moved: HEAD={local} expected={expect_base}")
    if expect_head and head != expect_head:
        die(f"PR head moved after the check was scheduled: PR={head} expected={expect_head}")
    if run(["git", "merge-base", "--is-ancestor", local, f"origin/{base_ref}"], check=False).returncode != 0:
        die(f"trusted base {local} is not on origin/{base_ref}")
    run(["git", "fetch", "--quiet", "origin", f"refs/pull/{int(pr)}/head"])
    fetched = run(["git", "rev-parse", "FETCH_HEAD"]).stdout.strip()
    if fetched != head:
        die(f"fetched PR head {fetched} is not the head GitHub reports {head}")
    changed = sorted(x for x in run(["git", "diff", "--name-only", f"{local}...{head}"]).stdout.splitlines() if x)
    diff = run(["git", "diff", "--unified=0", "--no-ext-diff", "--no-color", f"{local}...{head}"]).stdout
    result = evaluate(
        changed_files=changed,
        base_backend=git_show(local, BACKEND_MANIFEST), head_backend=git_show(head, BACKEND_MANIFEST),
        base_frontend=git_show(local, FRONTEND_MANIFEST), head_frontend=git_show(head, FRONTEND_MANIFEST),
        diff=diff, body=meta.get("body") or "",
        author=author, commits=commits,
        base_floor=git_show(local, FLOOR_FILE), head_floor=git_show(head, FLOOR_FILE),
    )
    return bound(result, mode="trusted-base", pr=pr, base=local, head=head, changed=changed)


def verify_worktree() -> dict:
    changed = sorted(x for x in run(["git", "diff", "--name-only", "HEAD"]).stdout.splitlines() if x)
    diff = run(["git", "diff", "--unified=0", "--no-ext-diff", "--no-color", "HEAD"]).stdout
    return evaluate(
        changed_files=changed,
        base_backend=git_show("HEAD", BACKEND_MANIFEST), head_backend=worktree_text(BACKEND_MANIFEST),
        base_frontend=git_show("HEAD", FRONTEND_MANIFEST), head_frontend=worktree_text(FRONTEND_MANIFEST),
        diff=diff, body="",
        base_floor=git_show("HEAD", FLOOR_FILE), head_floor=worktree_text(FLOOR_FILE),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr")
    target.add_argument("--worktree", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--trusted-base", action="store_true",
                        help="judge the PR head from a base commit already on the protected branch")
    parser.add_argument("--expect-base", help="refuse unless HEAD is exactly this commit (trusted-base mode)")
    parser.add_argument("--expect-head", help="refuse unless the PR head is exactly this commit (trusted-base mode)")
    args = parser.parse_args()
    if (args.expect_base or args.expect_head) and not args.trusted_base:
        parser.error("--expect-base/--expect-head require --trusted-base")
    if args.trusted_base and not args.pr:
        parser.error("--trusted-base requires --pr")
    if args.worktree:
        result = verify_worktree()
    elif args.trusted_base:
        result = verify_pr_trusted_base(str(args.pr), expect_base=args.expect_base, expect_head=args.expect_head)
    else:
        result = verify_pr(str(args.pr))
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    if result["verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
