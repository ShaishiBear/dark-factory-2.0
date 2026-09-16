"""Bounded static facts from committed source. No checkout execution or semantic proof."""
from __future__ import annotations

import ast
from pathlib import PurePosixPath
import re
import subprocess

from .canonical import sha256_bytes, sha256_value
from .frontdoor_intent import IntentRefused
from .manifest import GIT_OID


def inspect_repository(root, selected_paths):
    if (not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= 40
            or len(set(selected_paths)) != len(selected_paths)):
        raise IntentRefused("select 1–40 distinct source paths")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], timeout=15)

    commit = git("rev-parse", "HEAD").decode().strip()
    if not GIT_OID.fullmatch(commit):
        raise IntentRefused("repository commit identity is missing")
    files, total = {}, 0
    for name in selected_paths:
        if (not isinstance(name, str) or not re.fullmatch(r"app/[A-Za-z0-9_./-]+", name)
                or any(part in {"..", ".", ".git"} for part in name.split("/"))
                or PurePosixPath(name).suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}):
            raise IntentRefused("repository analysis accepts product source paths only")
        entry = git("ls-tree", commit, "--", name).decode().strip().split()
        if len(entry) != 4 or entry[0] not in {"100644", "100755"} or entry[3] != name:
            raise IntentRefused("source must be a committed regular file")
        size = int(git("cat-file", "-s", entry[2]))
        total += size
        if size > 50000 or total > 200000:
            raise IntentRefused("selected source exceeds analysis bound")
        raw = git("cat-file", "blob", entry[2])
        if len(raw) != size:
            raise IntentRefused("source size changed")
        source = raw.decode("utf-8")
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
        files[name] = {"sha256": sha256_bytes(raw), "bytes": size, "lines": len(source.splitlines()),
                       "imports": sorted(set(imports)), "definitions": sorted(set(definitions)), "gaps": gaps}
    policies = {}
    for name in (".factory/architecture.json", "FACTORY_RULES.md"):
        size = int(git("cat-file", "-s", f"{commit}:{name}"))
        if size > 100000:
            raise IntentRefused("protected policy context exceeds bound")
        raw = git("show", f"{commit}:{name}")
        policies[name] = {"sha256": sha256_bytes(raw), "text": raw.decode("utf-8")}
    context = {"commit": commit, "files": files, "policies": policies,
               "coverage": "selected-committed-source-only", "proof_status": "not-established"}
    return {**context, "identity": sha256_value(context)}
