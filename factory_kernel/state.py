"""Deterministic Dark Factory stage/state transitions.

The outer runner may be GitHub Actions, Archon, or something else. The legal lifecycle belongs
here so orchestration cannot silently invent a new path around evidence requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Stage(str, Enum):
    INTAKE = "intake"
    SPEC = "spec"
    TICKETS = "tickets"
    FRONTIER = "frontier"
    CONTEXT = "context"
    DESIGN = "design"
    ARCHITECTURE = "architecture"
    TEST_PLAN = "test-plan"
    RED = "red"
    IMPLEMENT = "implement"
    GREEN = "green"
    IMPACT = "impact"
    CONFORMANCE = "conformance"
    HOLDOUT = "holdout"
    MUTATION = "mutation"
    RATCHET = "ratchet"
    EVIDENCE = "evidence"
    MERGE_AUTH = "merge-auth"
    MERGED = "merged"
    POST_MERGE = "post-merge"
    COMPLETE = "complete"
    WAITING = "waiting"
    DECOMPOSED = "decomposed"
    STOPPED = "stopped"
    NEEDS_HUMAN = "needs-human"


class Outcome(str, Enum):
    PASS = "pass"
    WAIT = "wait"
    DECOMPOSE = "decompose"
    STOP = "stop"
    NEEDS_HUMAN = "needs-human"


HAPPY_PATH: tuple[Stage, ...] = (
    Stage.INTAKE,
    Stage.SPEC,
    Stage.TICKETS,
    Stage.FRONTIER,
    Stage.CONTEXT,
    Stage.DESIGN,
    Stage.ARCHITECTURE,
    Stage.TEST_PLAN,
    Stage.RED,
    Stage.IMPLEMENT,
    Stage.GREEN,
    Stage.IMPACT,
    Stage.CONFORMANCE,
    Stage.HOLDOUT,
    Stage.MUTATION,
    Stage.RATCHET,
    Stage.EVIDENCE,
    Stage.MERGE_AUTH,
    Stage.MERGED,
    Stage.POST_MERGE,
    Stage.COMPLETE,
)

NEXT = {current: nxt for current, nxt in zip(HAPPY_PATH, HAPPY_PATH[1:])}
TERMINAL = {Stage.COMPLETE, Stage.DECOMPOSED, Stage.STOPPED, Stage.NEEDS_HUMAN}


@dataclass(frozen=True)
class FactoryState:
    issue: int
    stage: Stage
    attempt: int = 1

    def __post_init__(self) -> None:
        if self.issue <= 0:
            raise ValueError("issue must be a positive integer")
        if self.attempt <= 0:
            raise ValueError("attempt must be a positive integer")


def transition(state: FactoryState, outcome: Outcome) -> FactoryState:
    """Return the only legal next control-plane state for a completed stage.

    A retry is intentionally not an outcome here: retry policy increments `attempt` while
    preserving the same stage, and must be explicit in the caller's recovery policy.
    """
    if state.stage in TERMINAL:
        raise ValueError(f"cannot transition terminal stage {state.stage.value}")
    if outcome is Outcome.NEEDS_HUMAN:
        return FactoryState(issue=state.issue, stage=Stage.NEEDS_HUMAN, attempt=state.attempt)
    if outcome is Outcome.STOP:
        return FactoryState(issue=state.issue, stage=Stage.STOPPED, attempt=state.attempt)
    if outcome is Outcome.DECOMPOSE:
        if state.stage not in {Stage.TICKETS, Stage.ARCHITECTURE}:
            raise ValueError(f"decompose is not legal from {state.stage.value}")
        return FactoryState(issue=state.issue, stage=Stage.DECOMPOSED, attempt=state.attempt)
    if outcome is Outcome.WAIT:
        if state.stage is not Stage.FRONTIER:
            raise ValueError(f"wait is not legal from {state.stage.value}")
        return FactoryState(issue=state.issue, stage=Stage.WAITING, attempt=state.attempt)
    if outcome is not Outcome.PASS:
        raise ValueError(f"unsupported outcome {outcome.value}")
    try:
        next_stage = NEXT[state.stage]
    except KeyError as exc:
        raise ValueError(f"pass is not legal from {state.stage.value}") from exc
    return FactoryState(issue=state.issue, stage=next_stage, attempt=1)


def retry(state: FactoryState) -> FactoryState:
    if state.stage in TERMINAL:
        raise ValueError(f"cannot retry terminal stage {state.stage.value}")
    return FactoryState(issue=state.issue, stage=state.stage, attempt=state.attempt + 1)
