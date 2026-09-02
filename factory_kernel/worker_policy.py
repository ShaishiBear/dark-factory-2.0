"""Least-privilege tool policy for untrusted model workers.

Workers may inspect the checkout and write files needed by their role. They never receive Bash/Git
as a general capability; Git state, commits, test execution and external authorities belong to the
repo-owned kernel.
"""
from __future__ import annotations

READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = (*READ_TOOLS, "Write", "Edit")

# Every role invoked against a repository checkout writes either run artifacts or, for the three
# mutation roles below, candidate checkout files. The kernel separately asserts whether repository
# changes are permitted and commits them deterministically.
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "triage": (),
    "plan": WRITE_TOOLS,
    "investigate": WRITE_TOOLS,
    "contract": WRITE_TOOLS,
    "context": WRITE_TOOLS,
    "architecture": WRITE_TOOLS,
    "test_author": WRITE_TOOLS,
    "implement": WRITE_TOOLS,
    "review": WRITE_TOOLS,
    "repair": WRITE_TOOLS,
    "conformance": WRITE_TOOLS,
    "holdout": (),
    "architecture-holdout": (),
    "contract-certifier": (),
    "design-certifier": (),
    "governor-certifier": (),
}

REPO_MUTATION_ROLES = frozenset({"test_author", "implement", "repair"})


def allowed_tools(role: str) -> tuple[str, ...]:
    try:
        return ROLE_TOOLS[role]
    except KeyError as exc:
        raise ValueError(f"no least-privilege worker policy for role {role!r}") from exc


def may_change_repo(role: str) -> bool:
    if role not in ROLE_TOOLS:
        raise ValueError(f"no least-privilege worker policy for role {role!r}")
    return role in REPO_MUTATION_ROLES
