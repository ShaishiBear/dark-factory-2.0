"""Bounded static facts from committed source. No checkout execution or semantic proof."""
from __future__ import annotations

import ast
import base64
import binascii
import hashlib
from pathlib import PurePosixPath
import re
import subprocess
import time

from .canonical import sha256_bytes, sha256_value
from .frontdoor_intent import IntentRefused
from .manifest import GIT_OID


POLICY_PATHS = (".factory/architecture.json", "FACTORY_RULES.md")


def _paths(selected_paths):
    if (not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= 40
            or any(not isinstance(name, str) for name in selected_paths)
            or len(set(selected_paths)) != len(selected_paths)):
        raise IntentRefused("select 1–40 distinct source paths")
    for name in selected_paths:
        if (not re.fullmatch(r"app/[A-Za-z0-9_./-]+", name)
                or any(part in {"", "..", ".", ".git"} for part in name.split("/"))
                or PurePosixPath(name).suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}):
            raise IntentRefused("repository analysis accepts product source paths only")


def inspect_repository(root, selected_paths):
    _paths(selected_paths)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], timeout=15)

    commit = git("rev-parse", "HEAD").decode().strip()
    if not GIT_OID.fullmatch(commit):
        raise IntentRefused("repository commit identity is missing")

    def read(name, limit):
        entry = git("ls-tree", commit, "--", name).decode().strip().split()
        if len(entry) != 4 or entry[0] not in {"100644", "100755"} or entry[3] != name:
            raise IntentRefused("source must be a committed regular file")
        size = int(git("cat-file", "-s", entry[2]))
        if size > limit:
            raise IntentRefused("selected source exceeds analysis bound")
        raw = git("cat-file", "blob", entry[2])
        if len(raw) != size:
            raise IntentRefused("source size changed")
        return raw

    return _analyse(commit, selected_paths, read)


def _analyse(commit, selected_paths, read):
    files, total = {}, 0
    for name in selected_paths:
        raw = read(name, 50000)
        total += len(raw)
        if total > 200000:
            raise IntentRefused("selected source exceeds analysis bound")
        source = _decode(raw)
        imports, definitions, gaps = [], [], []
        if name.endswith(".py"):
            try:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imports.append("." * node.level + (node.module or ""))
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        definitions.append(node.name)
                gaps.append("dynamic-imports-and-runtime-dispatch-not-resolved")
            except (SyntaxError, RecursionError):
                gaps.append("python-source-not-parsed")
        else:
            imports = re.findall(r'''(?:from\s+|import\s*\(\s*|require\s*\(\s*)["']([^"']+)["']''', source)
            gaps.append("javascript-imports-are-lexical-not-a-complete-module-graph")
        files[name] = {"sha256": sha256_bytes(raw), "bytes": len(raw), "lines": len(source.splitlines()),
                       "imports": sorted(set(imports)), "definitions": sorted(set(definitions)), "gaps": gaps}
    policies = {}
    for name in POLICY_PATHS:
        raw = read(name, 100000)
        policies[name] = {"sha256": sha256_bytes(raw), "text": _decode(raw)}
    context = {"commit": commit, "files": files, "policies": policies,
               "coverage": "selected-committed-source-only", "proof_status": "not-established"}
    return {**context, "identity": sha256_value(context)}


def _decode(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntentRefused("repository context must be UTF-8 text") from exc


def inspect_protected_repository(github, selected_paths, *, check_stop=lambda: None):
    """Fresh protected-main observation for hosted exploration; read-only, no checkout.

    The authenticated GitHub transport is the source authority. Exact blob hashes and
    before/after repository and branch observations bind the bounded static analysis.
    This snapshot is not a publication capability or an atomic lock on future main.
    """
    _paths(selected_paths)
    return observe_protected_files(github, selected_paths, POLICY_PATHS, _analyse, check_stop=check_stop)


SELECTION_BOUND = 400000


def observe_protected_files(github, selected_paths, policy_paths, analyse, *, check_stop=lambda: None):
    """Internal reader for trusted, fixed-path consumers; callers validate their path policy.

    The analyser receives only exact committed bytes and must apply per-file read limits.
    It runs before the final currency fence so its result cannot escape on main drift.
    """
    repository = github.repository
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise IntentRefused("configured repository identity is invalid")
    deadline = time.monotonic() + 120

    def get(path):
        check_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntentRefused("repository observation deadline exhausted")
        result = github.json(["api", f"repos/{repository}" + path], timeout=min(15, remaining))
        if time.monotonic() >= deadline:
            raise IntentRefused("repository observation deadline exhausted")
        return result

    def metadata():
        row = get("")
        if (not isinstance(row, dict) or row.get("full_name") != repository
                or row.get("default_branch") != "main" or type(row.get("private")) is not bool):
            raise IntentRefused("repository identity, visibility or default branch is unknown")
        return {key: row[key] for key in ("full_name", "private", "default_branch")}

    def branch():
        row = get("/branches/main")
        if not isinstance(row, dict) or row.get("protected") is not True:
            raise IntentRefused("repository observation requires protected main")
        commit = row.get("commit") or {}
        if not isinstance(commit, dict) or not isinstance(commit.get("commit"), dict):
            raise IntentRefused("protected branch commit identity is missing")
        oid = commit.get("sha")
        tree_record = commit["commit"].get("tree")
        tree = tree_record.get("sha") if isinstance(tree_record, dict) else None
        if any(not isinstance(value, str) or not GIT_OID.fullmatch(value) for value in (oid, tree)):
            raise IntentRefused("protected branch commit or tree identity is missing")
        return oid, tree

    original_metadata = metadata()
    commit, tree_oid = branch()
    tree = get(f"/git/trees/{tree_oid}?recursive=1")
    if (not isinstance(tree, dict) or tree.get("truncated") is not False or tree.get("sha") != tree_oid
            or not isinstance(tree.get("tree"), list) or len(tree["tree"]) > 100000):
        raise IntentRefused("repository tree is incomplete or bound to the wrong revision")
    entries = {}
    wanted = set(selected_paths) | set(policy_paths)
    for entry in tree["tree"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise IntentRefused("invalid repository tree entry")
        name = entry["path"]
        if name not in wanted:
            continue
        if (name in entries or entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                or not isinstance(entry.get("sha"), str) or not GIT_OID.fullmatch(entry["sha"])
                or type(entry.get("size")) is not int or entry["size"] < 0):
            raise IntentRefused("repository context requires distinct committed regular blobs")
        entries[name] = entry
    if entries.keys() != wanted:
        raise IntentRefused("repository context source or protected policy is missing")
    # The selection bound for every trusted fixed-path consumer. Measured 2026-09-17 (WP01):
    # the execution-authority closure is 199,408 bytes once the project profile joins it, so
    # the former 200,000 left 592 bytes for any future edit of a closure file. Raised on that
    # measurement, not omitted around; `_analyse` keeps its own 200,000 read bound, so the
    # repository analysis path is not widened by this. tests/factory/test_project_profile.py
    # pins the closure size with headroom so growth is seen before this refuses on the host.
    if sum(entries[name]["size"] for name in selected_paths) > SELECTION_BOUND:
        raise IntentRefused("selected source exceeds analysis bound")

    def read(name, limit):
        entry = entries[name]
        if entry["size"] > limit:
            raise IntentRefused("selected source exceeds analysis bound")
        blob = get(f"/git/blobs/{entry['sha']}")
        if (not isinstance(blob, dict) or blob.get("sha") != entry["sha"] or blob.get("encoding") != "base64"
                or type(blob.get("size")) is not int or blob["size"] != entry["size"]
                or not isinstance(blob.get("content"), str) or len(blob["content"]) > 2 * limit + 100):
            raise IntentRefused("repository blob identity, encoding or size changed")
        try:
            raw = base64.b64decode("".join(blob["content"].split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise IntentRefused("invalid repository blob encoding") from exc
        digest = hashlib.sha1 if len(entry["sha"]) == 40 else hashlib.sha256
        actual_oid = digest(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if len(raw) != entry["size"] or actual_oid != entry["sha"]:
            raise IntentRefused("repository blob content differs from its exact Git identity")
        return raw

    context = analyse(commit, selected_paths, read)
    if branch() != (commit, tree_oid) or metadata() != original_metadata:
        raise IntentRefused("protected repository changed during observation")
    check_stop()
    return context
