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
    "review-spec": WRITE_TOOLS,
    "review-standards": WRITE_TOOLS,
    "repair": WRITE_TOOLS,
    "conformance": WRITE_TOOLS,
    "holdout": (),
    "architecture-holdout": (),
    "contract-certifier": (),
    "design-certifier": (),
    "governor-certifier": (),
}

REPO_MUTATION_ROLES = frozenset({"test_author", "implement", "repair"})

# The identity every kernel-made commit carries. It is the GitHub Actions bot's own noreply
# address, which GitHub attributes to the `github-actions[bot]` account (type Bot). An earlier
# invented noreply address mapped to no account at all, so kernel commits resolved to null:
# unattributable to anyone, and a possible trigger for the ruleset's extra-approval rule
# that the autonomous path can never satisfy. Attributing to the Bot is also what the trust-root
# guard's second fence expects of factory commits.
KERNEL_COMMIT_NAME = "github-actions[bot]"
KERNEL_COMMIT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
KERNEL_COMMIT_ARGS: tuple[str, ...] = (
    "-c", f"user.name={KERNEL_COMMIT_NAME}",
    "-c", f"user.email={KERNEL_COMMIT_EMAIL}",
)

# Paths that do not exist in a build worktree. Workers get Read/Glob/Grep over the checkout, and
# a protected file is only tamper-resistant, not secret: the holdout scenarios under
# .factory/holdout/ were readable by the very worker whose output they judge. The kernel creates
# every build worktree as a sparse checkout that excludes these patterns, so the files are absent
# from disk rather than merely denied. The validator worktree is never blinded: the full harness
# runs the holdout there. The immunity registry (immunity.json) stays visible; it is a record of
# lessons, not a set of assertions a builder could optimise against.
BUILDER_BLIND_PATHS: tuple[str, ...] = (".factory/holdout/**/*.py",)


def allowed_tools(role: str) -> tuple[str, ...]:
    try:
        return ROLE_TOOLS[role]
    except KeyError as exc:
        raise ValueError(f"no least-privilege worker policy for role {role!r}") from exc


def may_change_repo(role: str) -> bool:
    if role not in ROLE_TOOLS:
        raise ValueError(f"no least-privilege worker policy for role {role!r}")
    return role in REPO_MUTATION_ROLES
