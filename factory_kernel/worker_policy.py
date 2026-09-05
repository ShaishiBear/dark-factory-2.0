"""Least-privilege tool policy for untrusted model workers.

Workers may inspect the checkout and write files needed by their role. They never receive Bash/Git
as a general capability; Git state, commits, test execution and external authorities belong to the
repo-owned kernel.
"""
from __future__ import annotations

import math

READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = (*READ_TOOLS, "Write", "Edit")

# A judge gets no tools at all. Every validation authority runs in an empty temporary directory,
# deliberately away from the checkout, and everything it is entitled to see (the contract, the
# diff, the RED/GREEN proof summary, the builder pack) arrives inside its prompt; the holdout
# prompt tells it so in its first sentence. There is nothing on disk it needs to read and nothing
# it may write, so the tool surface is empty rather than read-only: a judge that can edit a tree
# is a defect, and a judge that can read one is no longer blinded. The provider renders this as
# `--tools ""` (every built-in tool disabled). Until D-052 the authorities were constructed with
# no `allowed_tools` at all and arrived here as `None`, which the provider happened to render
# the same way; the empty surface is now the policy's statement, not the default's accident.
# The triage worker is the same shape: MISSION.md, FACTORY_RULES.md and the candidate batch are
# in its prompt, and it decides, it does not investigate.
JUDGE_TOOLS: tuple[str, ...] = ()

# The five validation authorities: the blinded code holdout, the architecture holdout and the
# three pre-code certifiers. Each is a model call the kernel makes on the validation side, and
# each is bounded exactly as a build worker is (tools, turns, dollars).
AUTHORITY_ROLES = frozenset({
    "holdout",
    "architecture-holdout",
    "contract-certifier",
    "design-certifier",
    "governor-certifier",
})

# Every role invoked against a repository checkout writes either run artifacts or, for the three
# mutation roles below, candidate checkout files. The kernel separately asserts whether repository
# changes are permitted and commits them deterministically.
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "triage": JUDGE_TOOLS,
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
    "holdout": JUDGE_TOOLS,
    "architecture-holdout": JUDGE_TOOLS,
    "contract-certifier": JUDGE_TOOLS,
    "design-certifier": JUDGE_TOOLS,
    "governor-certifier": JUDGE_TOOLS,
}

REPO_MUTATION_ROLES = frozenset({"test_author", "implement", "repair"})

# Every worker is a bounded agentic loop. Before this table the only backstop was the
# 20-minute subprocess timeout, so a worker that kept exploring burned the whole budget
# silently; the first canary spent ~12 minutes per stage that way (D-020). A role that exceeds
# its cap fails the stage, which is the ordinary fail-closed path. Roles that only read a named
# artifact list get tight caps; the roles that must map a task onto the repository get room.
# A cap is only real if the CLI can reach it before the kernel's subprocess timeout kills the
# stage. The first measured stage (worker run 33908589032: investigate, 25 turns, 846 s on
# z-ai/glm-5.3-flash, 33.85 s per turn) put the 1200 s timeout at roughly 35 turns. Every cap
# above that was fiction, and worse than fiction: a timeout escapes the provider with no result
# envelope and no telemetry, whereas the CLI stopping at `--max-turns` returns an envelope the
# kernel records as a clean, measured, retryable failed stage. The invariant below keeps every
# cap under the timeout using a ceiling on seconds per turn; a cap that would outlive the timeout
# is a configuration error, not a generous budget (D-025).
#
# The ceiling is stated from data, not tuned. Build run 33987381035 (issue #103) measured four
# stages on z-ai/glm-5.3-flash: investigate 888.6 s / 22 turns = 40.4 s per turn, contract
# 280.3 s / 12 = 23.4, context 697.0 s / 30 = 23.2, architecture 262.4 s / 12 = 21.9. The
# earlier ceiling of 35 was already below the first of those, and a 30-turn worker at 40.4 s
# per turn needs 1212 s, which put the single 1200 s wall exactly on the turn budget. 45 is the
# highest observed rate with a margin (D-054). Raise it again only from a measured run.
OBSERVED_SECONDS_PER_TURN_CEILING = 45

# A role's wall clock is its turn budget with headroom for the work between turns that is not
# a model call (tool calls over a large tree) and for the spread the four observations above
# already show (21.9 to 40.4 s per turn). Beyond the wall the process is killed and the stage
# is a recorded, timed-out failure.
STAGE_WALL_HEADROOM = 1.5

ROLE_MAX_TURNS: dict[str, int] = {
    "triage": 20,
    "plan": 30,
    "investigate": 30,
    "contract": 30,
    "context": 24,
    "architecture": 30,
    "test_author": 30,
    "implement": 30,
    "review-spec": 30,
    "review-standards": 30,
    "repair": 30,
    "conformance": 30,
    "holdout": 10,
    "architecture-holdout": 10,
    "contract-certifier": 10,
    "design-certifier": 10,
    "governor-certifier": 10,
}

# A dollar backstop beside the turn cap. Turns bound iterations; the money is in the resent
# conversation, which grows with every turn, so a per-role `--max-budget-usd` bounds the thing
# that actually costs. The values are three times the one observed builder stage ($4.00 as the
# CLI reported it) and exist to stop a runaway, not to trim a normal run. The CLI's reported
# cost for a non-Anthropic model is very likely a fallback-priced figure; until it is reconciled
# against the OpenRouter dashboard these numbers are backstops, not budgets (D-025).
ROLE_MAX_BUDGET_USD: dict[str, float] = {
    "triage": 2.0,
    "plan": 12.0,
    "investigate": 12.0,
    "contract": 12.0,
    "context": 12.0,
    "architecture": 12.0,
    "test_author": 12.0,
    "implement": 12.0,
    "review-spec": 4.0,
    "review-standards": 4.0,
    "repair": 12.0,
    "conformance": 12.0,
    # The authorities are ten-turn, tool-less, single-prompt judges, the same shape as triage,
    # so they carry triage's cap. These rows existed before D-052 but nothing read them: the
    # validation side constructed its requests without `max_budget_usd`, so the 934-second
    # code holdout of run 33960088633 ran against no dollar bound at all. Every authority
    # request now carries the row, and `_agent_stage` refuses a request that arrives without one.
    "holdout": 2.0,
    "architecture-holdout": 2.0,
    "contract-certifier": 2.0,
    "design-certifier": 2.0,
    "governor-certifier": 2.0,
}

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


def max_turns(role: str) -> int:
    try:
        cap = ROLE_MAX_TURNS[role]
    except KeyError as exc:
        raise ValueError(f"no turn cap for role {role!r}") from exc
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        raise ValueError(f"turn cap for role {role!r} must be a positive integer")
    return cap


def max_budget_usd(role: str) -> float:
    try:
        cap = ROLE_MAX_BUDGET_USD[role]
    except KeyError as exc:
        raise ValueError(f"no budget cap for role {role!r}") from exc
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        raise ValueError(f"budget cap for role {role!r} must be a positive number")
    return float(cap)


def stage_budget_seconds(role: str) -> int | None:
    """The wall clock a stage is expected to fit in: its turn cap at the per-turn ceiling.

    Telemetry only. A stage that runs longer is flagged `over_budget` in its record and its
    stage line and nothing else happens: the flag exists so the caps can be tuned from
    measured runs rather than from estimates (D-050). `None` for a role without a turn cap.
    """
    if role not in ROLE_MAX_TURNS:
        return None
    return max_turns(role) * OBSERVED_SECONDS_PER_TURN_CEILING


def stage_timeout_seconds(role: str) -> int:
    """The wall clock one CLI process for `role` may run: the turn budget with headroom.

    `ceil(max_turns(role) * OBSERVED_SECONDS_PER_TURN_CEILING * STAGE_WALL_HEADROOM)`. The
    provider kills the process at this wall and the stage is recorded as timed out with the
    turns, cost and events it had shown by then. The single global `provider.timeout_seconds`
    used to be every role's wall; it is now the maximum every role's wall must fit under
    (`assert_caps_fit_timeout`), because a ten-turn judge and a thirty-turn builder do not
    share one budget (D-054).
    """
    return math.ceil(max_turns(role) * OBSERVED_SECONDS_PER_TURN_CEILING * STAGE_WALL_HEADROOM)


def assert_caps_fit_timeout(timeout_seconds: int) -> None:
    """Refuse any role whose wall the configured maximum would cut off first."""
    for role, cap in ROLE_MAX_TURNS.items():
        wall = stage_timeout_seconds(role)
        if wall > timeout_seconds:
            raise ValueError(
                f"turn cap for role {role!r} ({cap}) needs a wall of {wall} s at "
                f"{OBSERVED_SECONDS_PER_TURN_CEILING} s/turn x{STAGE_WALL_HEADROOM}, which exceeds "
                f"the provider maximum timeout_seconds={timeout_seconds}"
            )


def may_change_repo(role: str) -> bool:
    if role not in ROLE_TOOLS:
        raise ValueError(f"no least-privilege worker policy for role {role!r}")
    return role in REPO_MUTATION_ROLES
