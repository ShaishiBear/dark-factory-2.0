"""Render the assembled worker prompt with absolute run paths.

Prompts under `.factory/prompts/` and `.factory/methods/` name the run's output files as
`$ARTIFACTS_DIR/<file>`. That token is a placeholder by contract, not a shell variable: workers run
`--bare` with Read/Glob/Grep/Write/Edit and no shell, so nothing on their side ever expands it.
Canary attempt 5 (worker run 33910993905) made the consequence exact: a briefed, capped worker
followed the prompt literally and wrote its artifacts into a directory called `$ARTIFACTS_DIR`
inside the build worktree, which the clean-tree check then refused.

The kernel therefore renders every placeholder itself, before the prompt reaches the CLI, from the
same request environment it hands the worker (`ClaudeCliProvider.REQUEST_ENV`). Only those names
may appear as placeholders; anything else left in the prompt is refused, so a new placeholder can
never reach a worker unexpanded again. The artifacts directory must be an existing absolute path
because it is also the one extra directory the CLI is told it may write to (`--add-dir`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

# Placeholders a prompt may carry: exactly the request-local names the provider forwards to the
# worker. Kept as a literal here rather than imported so a change to the provider's set is a
# deliberate change to what prompts may say, and vice versa; a test pins the two together.
RENDERABLE_PLACEHOLDERS: tuple[str, ...] = (
    "ARTIFACTS_DIR",
    "FACTORY_BASE_REF",
    "FACTORY_REPO",
    "FACTORY_WORKDIR",
)

# `$NAME` or `${NAME}` where NAME is upper-case with underscores. `$1`-style and lower-case
# references are left alone; they are shell syntax inside quoted example commands, not ours.
_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}|\$([A-Z][A-Z0-9_]*)\b")

LITERAL_ARTIFACTS_DIR_NAMES = ("$ARTIFACTS_DIR", "${ARTIFACTS_DIR}")


class PromptRenderError(RuntimeError):
    """The prompt could not be rendered into something a shell-less worker can act on."""


def artifacts_dir(env: Mapping[str, str]) -> Path:
    raw = str(env.get("ARTIFACTS_DIR", "")).strip()
    if not raw:
        raise PromptRenderError("worker environment lacks ARTIFACTS_DIR; the prompt cannot be rendered")
    path = Path(raw)
    if not path.is_absolute():
        raise PromptRenderError(f"ARTIFACTS_DIR must be absolute, got {raw!r}")
    if not path.is_dir():
        raise PromptRenderError(f"ARTIFACTS_DIR does not exist or is not a directory: {raw!r}")
    return path


def render_prompt(prompt: str, env: Mapping[str, str]) -> str:
    """Substitute every renderable placeholder; refuse if any other `$UPPER_NAME` remains."""
    values = {"ARTIFACTS_DIR": str(artifacts_dir(env))}
    for name in RENDERABLE_PLACEHOLDERS:
        if name == "ARTIFACTS_DIR":
            continue
        value = str(env.get(name, "")).strip()
        if value:
            values[name] = value

    unknown: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in values:
            return values[name]
        unknown.add(name)
        return match.group(0)

    rendered = _PLACEHOLDER.sub(replace, prompt)
    if unknown:
        raise PromptRenderError(
            "prompt carries placeholders the kernel cannot render: "
            + ", ".join(sorted(f"${n}" for n in unknown))
            + "; only " + ", ".join(f"${n}" for n in RENDERABLE_PLACEHOLDERS) + " may appear"
        )
    return rendered


def literal_artifacts_dir_entries(porcelain: str) -> list[str]:
    """Paths in `git status --porcelain` output that live under a literal `$ARTIFACTS_DIR`."""
    hits: list[str] = []
    for line in porcelain.splitlines():
        path = line[3:] if len(line) > 3 else ""
        if any(path == name or path.startswith(name + "/") for name in LITERAL_ARTIFACTS_DIR_NAMES):
            hits.append(path)
    return hits
