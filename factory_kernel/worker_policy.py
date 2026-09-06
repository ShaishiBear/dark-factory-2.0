"""Least-privilege tool policy for untrusted model workers.

Workers may inspect the checkout and write files needed by their role. They never receive Bash/Git
as a general capability; Git state, commits, test execution and external authorities belong to the
repo-owned kernel.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from .agents import PathScope

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

# The effort levels the pinned CLI accepts (`claude --help` on 2.1.245 and 2.1.259: "Effort
# level for the current session (low, medium, high, xhigh, max)"), lowest first. The CLI's
# documentation calls effort the control on adaptive reasoning, "whether and how much to think
# on each step"; `high` is the CLI's default on every model the factory could route to, and
# nothing the kernel sent before D-055 named a level, so every worker ran at the default.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Every worker runs at a stated effort. Build run 33992451400 (issue #103) measured the
# `test_author` stage of a three-criterion design (one new test file) at 2025 s, 14 turns and
# 76,248 stream events against 5,414-30,432 for the other stages: 145 s and ~5,400 events per
# turn where the others took 16-46 s and 476-1,790, and the stream's tail was an unbroken run
# of `thinking_tokens` events. The worker model reasons without bound at the CLI's default
# effort, and nothing the kernel sent bounded it. Two levels, chosen from the CLI's own
# documentation of the scale:
#
# - Workers (every role that edits or drafts against a checkout) run at `medium`, the level
#   the documentation describes as "reduces token usage for cost-sensitive work that can trade
#   off some intelligence": one notch below the default, so thinking is bounded but not
#   disabled. `low` is documented for "short, scoped, latency-sensitive tasks that are not
#   intelligence-sensitive", and effort is adaptive ("whether and how much to think"), so at
#   `low` a step may not think at all; choosing what a test should assert, or how a fix should
#   land, is intelligence-sensitive, and the defect being corrected is unbounded thinking, not
#   thinking. The mutation roles (`test_author`, `implement`, `repair`) therefore carry the
#   same level as the drafting roles rather than a lower one.
# - Judges (the five validation authorities and triage) run at `high`, the CLI's default:
#   tool-less, single-prompt calls where reasoning is the whole job and a ten-turn cap already
#   bounds the loop. `max` is documented as "prone to overthinking"; `xhigh` is not offered on
#   every model and would fall back silently.
#
# The scale is calibrated per model ("the same level name does not represent the same
# underlying value across models"), and the factory's route is OpenRouter's Anthropic-compatible
# endpoint to a non-Anthropic model, so whether the route honours the level at all is measured,
# not assumed: the worker workflow's preflight runs the model at the lowest and the highest
# level in this table and prints `FACTORY_PREFLIGHT_EFFORT_PROBE ... honoured=true|false`
# (`scripts/factory_effort_probe.py`). That line is data for the next tuning, never a gate.
# Changing a level is a trust-root change; `provider.effort_overrides` in kernel.json is the
# per-deployment override, validated against EFFORT_LEVELS and this table's roles (D-055).
WORKER_EFFORT = "medium"
JUDGE_EFFORT = "high"

ROLE_EFFORT: dict[str, str] = {
    "triage": JUDGE_EFFORT,
    "plan": WORKER_EFFORT,
    "investigate": WORKER_EFFORT,
    "contract": WORKER_EFFORT,
    "context": WORKER_EFFORT,
    "architecture": WORKER_EFFORT,
    "test_author": WORKER_EFFORT,
    "implement": WORKER_EFFORT,
    "review-spec": WORKER_EFFORT,
    "review-standards": WORKER_EFFORT,
    "repair": WORKER_EFFORT,
    "conformance": WORKER_EFFORT,
    "holdout": JUDGE_EFFORT,
    "architecture-holdout": JUDGE_EFFORT,
    "contract-certifier": JUDGE_EFFORT,
    "design-certifier": JUDGE_EFFORT,
    "governor-certifier": JUDGE_EFFORT,
}

# The CLI's thinking budget, the one lever `--effort` is not. The installed CLI (2.1.259; the
# variable is far older) reads `MAX_THINKING_TOKENS` from its environment as an integer: above
# zero it sends every request with `thinking: {type: "enabled", budget_tokens: N}`, raising N
# to 1024 if it was lower and holding it under the response's max_tokens; exactly zero sends
# `thinking: {type: "disabled"}`; unset leaves thinking adaptive, which is what `--effort`
# steers. There is no flag for it (`claude --help` lists only `--effort`). Six builds of
# issue #103 died or overran in `test_author` at `--effort medium` (the sixth: 2025 s, 15
# turns, 121,065 thinking tokens, ~22,500 of them in the last turn alone), and the effort
# probe read honoured=true and honoured=false on the same route from run to run, so the level
# is not a bound on this route; a stated budget may be, and is measured before it is used.
THINKING_CAP_ENV = "MAX_THINKING_TOKENS"
# The smallest budget the CLI sends as given; a positive value below it is raised to it, so
# a configured cap below it would be a lie the loader refuses (D-059).
THINKING_CAP_MIN_BUDGET = 1024
# The value that turns thinking off altogether.
THINKING_CAP_DISABLED = 0

# Per-role cap, exported by the provider as `MAX_THINKING_TOKENS` in the CLI's environment
# only when the row is not `None`; a `None` row sets nothing and the CLI behaves exactly as it
# did. Every row is `None` until the worker workflow's preflight
# (`scripts/factory_thinking_cap_probe.py`, `FACTORY_PREFLIGHT_THINKING_CAP_PROBE ...
# honoured=true|false`) says the route honours the budget: the worker model three times on
# the effort probe's prompt, uncapped, at a 1024-token budget and with thinking disabled.
# Setting a row is a trust-root change; `provider.thinking_cap_overrides` in kernel.json is
# the per-deployment `{role: cap}` override, validated at load like the effort table (D-059).
ROLE_THINKING_CAP: dict[str, int | None] = {
    "triage": None,
    "plan": None,
    "investigate": None,
    "contract": None,
    "context": None,
    "architecture": None,
    "test_author": None,
    "implement": None,
    "review-spec": None,
    "review-standards": None,
    "repair": None,
    "conformance": None,
    "holdout": None,
    "architecture-holdout": None,
    "contract-certifier": None,
    "design-certifier": None,
    "governor-certifier": None,
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

# What a tool-bearing worker may reach on disk, per role, as gitignore-style patterns relative
# to the worktree root. The provider renders them as the CLI's permission rules and adds the
# run's artifacts directory itself; the kernel's funnel refuses a repository-mutation request
# that carries no scope at all.
#
# The boundary is stated from the CLI's documented rule semantics, which the read-scope
# preflight probe (`scripts/factory_read_scope_probe.py`) verifies on the runner every run:
# a file inside the working directory is readable without any rule, so an allow rule cannot
# narrow reads and the read boundary is the `deny` list (`Read(<trust root>)` rules, which the
# CLI also applies to Grep, Glob, and to Edit/Write on the same path); a write inside the
# working directory needs an allow rule under `--permission-mode dontAsk`, so the write
# boundary is the `write` list (`Edit(<pattern>)` rules, which the CLI applies to Write too).
# The `read` list is rendered as allow rules for completeness and for paths a future scope
# might add outside the tree; it grants nothing the working directory did not already.
#
# The fourth build of issue #103 (run 34002520477) died in `test_author` at its 30-turn cap
# after 46 Read calls and no Write: it read kernel source, the harness, biome and tsconfig,
# and set out to "verify the kernel's deferred-repro check", none of which is its task. The
# holdout scenarios were already absent from its worktree (BUILDER_BLIND_PATHS); the rest of
# the trust root was on disk and readable (D-057).
PRODUCT_READ_PATHS: tuple[str, ...] = (
    "app/**", "docs/**", "README.md", "CLAUDE.md", "MISSION.md", "FACTORY_RULES.md",
)
# Where a mutation role may write: product code, its tests and its docs. The three governance
# files are readable and never writable by a worker; the security guard refuses them anyway.
PRODUCT_WRITE_PATHS: tuple[str, ...] = ("app/**", "docs/**", "README.md")
# The trust root, denied to every tool-bearing role. `.factory/architecture.json` is deliberately
# absent: the architecture governor, the conformance authority and the standards reviewer are
# told to read it (their prompts open with it) and a deny rule would override their allow.
TRUST_ROOT_DENY_PATHS: tuple[str, ...] = (
    "factory_kernel/**",
    "harness/**",
    "scripts/**",
    "tests/factory/**",
    ".github/**",
    ".factory/kernel.json",
    ".factory/evidence-spine.json",
    ".factory/decisions.md",
    ".factory/locks/**",
    ".factory/prompts/**",
    ".factory/methods/**",
    ".factory/holdout/**",
    ".factory/benchmark/**",
)
ARCHITECTURE_POLICY_PATH = ".factory/architecture.json"

MUTATION_SCOPE = PathScope(
    read=PRODUCT_READ_PATHS, write=PRODUCT_WRITE_PATHS, deny=TRUST_ROOT_DENY_PATHS,
)
# Drafting roles write only run artifacts, which the provider grants separately.
DRAFTING_SCOPE = PathScope(read=PRODUCT_READ_PATHS, write=(), deny=TRUST_ROOT_DENY_PATHS)
# The one documented exception: roles whose prompt names `.factory/architecture.json`.
ARCHITECTURE_SCOPE = PathScope(
    read=(*PRODUCT_READ_PATHS, ARCHITECTURE_POLICY_PATH), write=(), deny=TRUST_ROOT_DENY_PATHS,
)
# A judge has no tools, so nothing to scope.
JUDGE_SCOPE = PathScope()

ROLE_PATH_SCOPE: dict[str, PathScope] = {
    "triage": JUDGE_SCOPE,
    "plan": DRAFTING_SCOPE,
    "investigate": DRAFTING_SCOPE,
    "contract": DRAFTING_SCOPE,
    "context": DRAFTING_SCOPE,
    "architecture": ARCHITECTURE_SCOPE,
    "test_author": MUTATION_SCOPE,
    "implement": MUTATION_SCOPE,
    "review-spec": DRAFTING_SCOPE,
    "review-standards": ARCHITECTURE_SCOPE,
    "repair": MUTATION_SCOPE,
    "conformance": ARCHITECTURE_SCOPE,
    "holdout": JUDGE_SCOPE,
    "architecture-holdout": JUDGE_SCOPE,
    "contract-certifier": JUDGE_SCOPE,
    "design-certifier": JUDGE_SCOPE,
    "governor-certifier": JUDGE_SCOPE,
}

# A repository-mutation worker that has written nothing by this fraction of its turn cap is
# not going to: the process is killed and the stage refused as `no_draft_by_turn`, not retried.
# The data, three points. Issue #49's test author (run 33999901008, GLM) wrote its first file
# at turn ~5 of the 15 it used (11 Reads, 3 Edits, 618 s, $1.28, RED proved): healthy. Issue
# #103's fourth build (34002520477, GLM) never wrote in 31 turns (46 Reads, 1925 s, $4.54,
# `error_max_turns`): the run the deadline exists for, and 0.6 (turn 18 of 30) was set from
# these two (D-057). Issue #103's seventh build (34024234313), the first on MiniMax M3, was
# killed by that deadline at turn 18 after 247 s and 17 Reads at one per turn, 13 s a turn,
# every read inside scope and about the component test it was to write (the hook and its test,
# ChatArea, ChatInput, App, two existing component tests, package.json, biome.json,
# vite.config.ts, main.tsx, useMessages, authApi, useAuth, and the three artifacts): a fast,
# disciplined model doing a legitimate ~20-read task, ended four minutes in before its first
# write. 0.8 of a 30-turn cap is turn 24: a run that only reads is still ended as turn 25
# begins, seven turns short of the 31 the fourth build spent, and a one-read-per-turn model has
# room to read a component test's neighbourhood before it drafts (D-063).
DRAFT_DEADLINE_FRACTION = 0.8


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


def effort(role: str) -> str:
    """The effort level the role runs at, from the policy table; the provider renders it as
    `--effort <level>` on every request and applies `provider.effort_overrides` on top."""
    try:
        level = ROLE_EFFORT[role]
    except KeyError as exc:
        raise ValueError(f"no effort level for role {role!r}") from exc
    if level not in EFFORT_LEVELS:
        raise ValueError(f"effort level for role {role!r} is not one the CLI accepts: {level!r}")
    return level


def effort_rank(level: str) -> int:
    """Position on the CLI's scale, lowest first; refuses a level the CLI does not accept."""
    try:
        return EFFORT_LEVELS.index(level)
    except ValueError as exc:
        raise ValueError(
            f"effort level {level!r} is not one the CLI accepts: {', '.join(EFFORT_LEVELS)}"
        ) from exc


def validate_effort_overrides(raw: object, name: str = "provider.effort_overrides") -> dict[str, str]:
    """`{role: level}` from kernel.json, refused unless every role is one this policy knows and
    every level is one the CLI accepts. Absent (`None`) is no override."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"kernel {name} must be an object of role to effort level")
    overrides: dict[str, str] = {}
    for role, level in raw.items():
        if not isinstance(role, str) or role not in ROLE_EFFORT:
            raise ValueError(f"kernel {name} names a role the worker policy does not know: {role!r}")
        if not isinstance(level, str) or level not in EFFORT_LEVELS:
            raise ValueError(
                f"kernel {name}.{role} must be one of {', '.join(EFFORT_LEVELS)}; got {level!r}"
            )
        overrides[role] = level
    return overrides


def thinking_cap(role: str) -> int | None:
    """The thinking budget the role's CLI environment carries as `MAX_THINKING_TOKENS`, or
    `None` for no cap (nothing set, the CLI as it was). The provider applies
    `provider.thinking_cap_overrides` on top (D-059)."""
    try:
        cap = ROLE_THINKING_CAP[role]
    except KeyError as exc:
        raise ValueError(f"no thinking cap row for role {role!r}") from exc
    if cap is None:
        return None
    return check_thinking_cap(cap, f"thinking cap for role {role!r}")


def check_thinking_cap(value: object, what: str) -> int:
    """An integer the CLI would honour as given: `THINKING_CAP_DISABLED` (0), or a budget of
    at least `THINKING_CAP_MIN_BUDGET` tokens. A bool, a string, a negative number or a
    positive budget below the minimum (which the CLI would silently raise) is refused."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{what} must be an integer: {THINKING_CAP_DISABLED} to disable thinking, or a "
            f"budget of at least {THINKING_CAP_MIN_BUDGET} tokens; got {value!r}"
        )
    if value != THINKING_CAP_DISABLED and value < THINKING_CAP_MIN_BUDGET:
        raise ValueError(
            f"{what} must be {THINKING_CAP_DISABLED} (thinking disabled) or at least "
            f"{THINKING_CAP_MIN_BUDGET} (the smallest budget the CLI sends as given); got {value!r}"
        )
    return value


def validate_thinking_cap_overrides(
    raw: object, name: str = "provider.thinking_cap_overrides"
) -> dict[str, int]:
    """`{role: cap}` from kernel.json, refused unless every role is one this policy knows and
    every cap is one the CLI would honour as given (`check_thinking_cap`). Absent (`None`) is
    no override."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"kernel {name} must be an object of role to thinking cap")
    overrides: dict[str, int] = {}
    for role, cap in raw.items():
        if not isinstance(role, str) or role not in ROLE_THINKING_CAP:
            raise ValueError(f"kernel {name} names a role the worker policy does not know: {role!r}")
        overrides[role] = check_thinking_cap(cap, f"kernel {name}.{role}")
    return overrides


# The model per role, the one lever left on a route that bounds nothing else. Six builds of
# issue #103 died in `test_author` on z-ai/glm-5.3-flash over OpenRouter's Anthropic-compatible
# route, which honours neither `--effort` (D-055) nor `MAX_THINKING_TOKENS` (D-059) for it,
# and that one role reasons 5-10x more per turn there than any other stage does. The kernel
# had exactly one per-role model (`provider.architecture_model`, for the architecture
# holdout); `provider.model_overrides` in kernel.json is the general `{role: model_slug}`
# table, validated here against this policy's roles and applied by the provider as the
# second step of its resolution: the request's own model, else this table's row, else the
# architecture holdout's own model, else the worker model. The worker workflow's route probe
# runs every distinct model the table names (`scripts/factory_models.py --list`) before any
# stage does (D-061).
def validate_model_overrides(raw: object, name: str = "provider.model_overrides") -> dict[str, str]:
    """`{role: model_slug}` from kernel.json, refused unless every role is one this policy
    knows (`ROLE_MAX_TURNS`) and every slug is a non-empty string. Absent (`None`) is no
    override."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"kernel {name} must be an object of role to model slug")
    overrides: dict[str, str] = {}
    for role, model in raw.items():
        if not isinstance(role, str) or role not in ROLE_MAX_TURNS:
            raise ValueError(
                f"kernel {name} names a role the worker policy does not know: {role!r}"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"kernel {name}.{role} must be a non-empty model slug; got {model!r}")
        overrides[role] = model.strip()
    return overrides


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


def path_scope(role: str) -> PathScope:
    """The file boundary the role's tools run inside; empty for a tool-less judge."""
    try:
        return ROLE_PATH_SCOPE[role]
    except KeyError as exc:
        raise ValueError(f"no path scope for role {role!r}") from exc


def draft_deadline_turn(role: str, cap: int | None = None) -> int | None:
    """The last turn by which a repository-mutation worker must have written something:
    `ceil(cap * DRAFT_DEADLINE_FRACTION)`, from the role's cap unless the request's own is
    given. `None` for every other role: a drafting role writes artifacts outside the tree and
    a judge writes nothing (D-057)."""
    if role not in ROLE_TOOLS:
        raise ValueError(f"no least-privilege worker policy for role {role!r}")
    if role not in REPO_MUTATION_ROLES:
        return None
    turns = max_turns(role) if cap is None else cap
    if isinstance(turns, bool) or not isinstance(turns, int) or turns <= 0:
        raise ValueError(f"turn cap for role {role!r} must be a positive integer")
    return math.ceil(turns * DRAFT_DEADLINE_FRACTION)
