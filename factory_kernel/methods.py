"""Engineering method text the kernel injects into isolated model workers.

Workers launch with ``--bare``, an empty strict MCP configuration and slash commands disabled:
they load no plugins, hooks, skills or project settings. That isolation is deliberate and is not
weakened here. Instead, the discipline a role is expected to follow arrives as plain text in its
prompt, read from the protected ``.factory/methods/`` directory according to ``manifest.json``.

The manifest is validated fail-closed: an unknown role, a missing or empty file, a duplicate id
or a duplicate file refuses the run rather than silently handing a worker a thinner prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from .worker_policy import ROLE_TOOLS

METHODS_DIR = ".factory/methods"
MANIFEST = f"{METHODS_DIR}/manifest.json"
KNOWN_SOURCES = frozenset({"mattpocock/skills", "ponytail", "dark-factory"})


@dataclass(frozen=True)
class Method:
    id: str
    path: Path
    source: str
    upstream_ref: str | None
    adaptation: str
    roles: tuple[str, ...]


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"methods manifest {name} must be a non-empty string")
    return value.strip()


def load_manifest(repo_root: str | Path) -> tuple[Method, ...]:
    root = Path(repo_root).resolve()
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise ValueError(f"methods manifest is missing: {MANIFEST}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"methods manifest is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("version") != "1.0":
        raise ValueError("methods manifest must be a version 1.0 object")
    entries = raw.get("methods")
    if not isinstance(entries, list) or not entries:
        raise ValueError("methods manifest must list at least one method")

    methods: list[Method] = []
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("methods manifest entries must be objects")
        method_id = _string(entry.get("id"), "id")
        file_name = _string(entry.get("file"), "file")
        if "/" in file_name or "\\" in file_name or file_name.startswith("."):
            raise ValueError(f"methods manifest file must be a plain name inside {METHODS_DIR}: {file_name}")
        source = _string(entry.get("source"), "source")
        if source not in KNOWN_SOURCES:
            raise ValueError(f"methods manifest source is unknown: {source}")
        ref = entry.get("upstream_ref")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise ValueError(f"methods manifest upstream_ref must be null or a non-empty string: {method_id}")
        adaptation = _string(entry.get("adaptation"), "adaptation")
        roles = entry.get("roles")
        if not isinstance(roles, list) or not roles:
            raise ValueError(f"methods manifest roles must be a non-empty list: {method_id}")
        for role in roles:
            if role not in ROLE_TOOLS:
                raise ValueError(f"methods manifest names an unknown role {role!r} for {method_id}")
        if len(set(roles)) != len(roles):
            raise ValueError(f"methods manifest repeats a role for {method_id}")
        if method_id in seen_ids:
            raise ValueError(f"methods manifest repeats id {method_id}")
        if file_name in seen_files:
            raise ValueError(f"methods manifest repeats file {file_name}")
        seen_ids.add(method_id)
        seen_files.add(file_name)
        path = (root / METHODS_DIR / file_name).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"methods manifest file is missing or unsafe: {file_name}")
        if not path.read_text(encoding="utf-8").strip():
            raise ValueError(f"method text is empty: {file_name}")
        methods.append(Method(
            id=method_id, path=path, source=source, upstream_ref=ref,
            adaptation=adaptation, roles=tuple(roles),
        ))
    return tuple(methods)


def methods_for_role(repo_root: str | Path, role: str) -> tuple[Method, ...]:
    if role not in ROLE_TOOLS:
        raise ValueError(f"no least-privilege worker policy for role {role!r}")
    return tuple(m for m in load_manifest(repo_root) if role in m.roles)


def method_block(repo_root: str | Path, role: str) -> str:
    """The text a role receives between its prompt and its context; empty for roles with none."""
    selected = methods_for_role(repo_root, role)
    if not selected:
        return ""
    parts = ["ENGINEERING METHODS (pinned, protected; follow them exactly):"]
    for method in selected:
        parts.append(method.path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)
