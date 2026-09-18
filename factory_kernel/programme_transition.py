"""Governed programme replacement: the transition phase machine and the release decision (C07, WP03).

Pure. Nothing here talks to GitHub, the intent store or the lease store. The service that
performs a transition (SPECIFICATION 7, eight steps) holds its durable state in the transition
journal (`transition_journal.py`, canonical private decision events) and its effect ownership in
the SQLite lease store; this module says which phase follows which, which events are legal under
stop, when a remote request has become uncertain and how it is resolved, and whether the fence
may be released. The fence acquisition lane itself (source observation, consent, admission,
publication effects) is the Front Door task's seven draft modules and is not reimplemented here.

Phases, in the only legal forward order (C07):

    reviewed -> fence_requested -> fenced -> draining -> drained -> reconciled
    -> predecessor_retired -> successor_requested -> successor_observed
    -> release_requested -> released

The three `*_requested` phases are remote requests. Each may become `<phase>:uncertain` when
the response is lost; the recovery observer resolves it by re-observing the SAME request id and
digest to the observed phase, to a definite no-effect refusal, or leaves it uncertain. No
timeout implies absence. A mismatch between the journal and the effect store after a crash is
`reconciliation_required`, never "whichever is further ahead".

Recorded interpretation of C07's "definite no-effect terminal refusal": terminal for the
REQUEST, which is closed and can never be reused (the journal refuses its id again), not for
the transition, which returns to the phase before the request so that the same transition may
make a fresh request under a new id. The transition is never re-created to escape uncertainty.
The phase list follows C07's eleven states; SPECIFICATION 7 names a shorter operational list
(fenced-observed, successor-published, active) that this module treats as superseded by C07.

Stop permits the reconciliation of effects that already happened (observations, marking
uncertainty, reconcile, drain) and refuses every new remote request (fence, successor, release)
and the retirement of predecessor work.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .canonical import sha256_value

PHASES: tuple[str, ...] = (
    "reviewed", "fence_requested", "fenced", "draining", "drained", "reconciled", "predecessor_retired",
    "successor_requested", "successor_observed", "release_requested", "released",
)
RECONCILIATION_REQUIRED = "reconciliation_required"
UNCERTAIN_SUFFIX = ":uncertain"

# Remote request phase -> (phase it came from, phase its observed effect establishes).
REMOTE_REQUESTS: dict[str, tuple[str, str]] = {
    "fence_requested": ("reviewed", "fenced"),
    "successor_requested": ("predecessor_retired", "successor_observed"),
    "release_requested": ("successor_observed", "released"),
}

# Event -> (phase before, phase after). Forward steps only; nothing skips a phase.
EVENTS: dict[str, tuple[str, str]] = {
    "request_fence": ("reviewed", "fence_requested"),
    "observe_fence": ("fence_requested", "fenced"),
    "begin_drain": ("fenced", "draining"),
    "observe_drained": ("draining", "drained"),
    "reconcile": ("drained", "reconciled"),
    "retire_predecessor": ("reconciled", "predecessor_retired"),
    "request_successor": ("predecessor_retired", "successor_requested"),
    "observe_successor": ("successor_requested", "successor_observed"),
    "request_release": ("successor_observed", "release_requested"),
    "observe_release": ("release_requested", "released"),
}
# Events refused under stop: every NEW remote effect (the three remote requests) and the
# retirement of predecessor work. What remains is the reconciliation of effects that already
# happened (observations, marking uncertainty, reconcile, begin/observe drain), which stop
# permits. This is the phase machine's own gate; the broker re-observes stop before every
# effect (SPEC 4.3) and the fence lane refuses acquisition under stop on its own (fence_request).
STOP_REFUSED_EVENTS = frozenset({"request_fence", "request_successor", "request_release", "retire_predecessor"})
# Recovery events on an uncertain remote request.
RECOVERY_EVENTS = frozenset({"observe_effect", "observe_no_effect"})
OBSERVE_EVENT_OF = {"fence_requested": "observe_fence", "successor_requested": "observe_successor",
                    "release_requested": "observe_release"}

RELEASE_FIELDS = ("observed", "consent", "successor", "fenced", "queued", "active", "pending_effects",
                  "accounting_reconciled")
# Release refusal precedence (conformance/README transition_release): first the observer itself,
# then the fence, then consent, then the successor, then predecessor work, effects, accounting.
RELEASE_ORDER: tuple[tuple[str, str], ...] = (
    ("observed", "observation_missing"),
    ("fenced", "fence_absent"),
    ("consent", "consent_invalid"),
    ("successor", "successor_unobserved"),
    ("drained", "predecessor_not_drained"),
    ("effects", "effect_uncertain"),
    ("accounting_reconciled", "accounting_unreconciled"),
)

HEX = {32: re.compile(r"[0-9a-f]{32}"), 40: re.compile(r"[0-9a-f]{40}"), 64: re.compile(r"[0-9a-f]{64}")}


class TransitionRefused(ValueError):
    """An illegal phase step, a malformed identity or an observation that cannot be applied."""


def is_uncertain(phase: str) -> bool:
    return isinstance(phase, str) and phase.endswith(UNCERTAIN_SUFFIX)


def request_of(phase: str) -> str:
    """The remote request phase an uncertain marker belongs to."""
    if not is_uncertain(phase):
        raise TransitionRefused(f"{phase!r} is not an uncertain phase")
    request = phase[: -len(UNCERTAIN_SUFFIX)]
    if request not in REMOTE_REQUESTS:
        raise TransitionRefused(f"{request!r} is not a remote request phase")
    return request


def validate_phase(phase: Any) -> str:
    if phase == RECONCILIATION_REQUIRED or phase in PHASES:
        return phase
    if is_uncertain(phase):
        request_of(phase)
        return phase
    raise TransitionRefused(f"unknown transition phase {phase!r}")


def advance(phase: str, event: str, *, stop: bool = False) -> str:
    """The phase after `event`, or TransitionRefused. Exactly the C07 forward sequence; a remote
    request may be marked uncertain and recovered; `require_reconciliation` is reachable from
    every phase and is terminal for this transition (a new review supersedes it)."""
    current = validate_phase(phase)
    if not isinstance(event, str):
        raise TransitionRefused("event must be a string")
    if event == "require_reconciliation":
        return RECONCILIATION_REQUIRED
    if current == RECONCILIATION_REQUIRED:
        raise TransitionRefused("a transition needing reconciliation accepts no further event; a new review supersedes it")
    if event == "mark_uncertain":
        if current in REMOTE_REQUESTS:
            return current + UNCERTAIN_SUFFIX
        if is_uncertain(current):
            return current  # still uncertain; nothing new is learned
        raise TransitionRefused(f"only a remote request can be uncertain, not {current!r}")
    if is_uncertain(current):
        request = request_of(current)
        before, observed = REMOTE_REQUESTS[request]
        if event == "observe_effect" or event == OBSERVE_EVENT_OF[request]:
            return observed
        if event == "observe_no_effect":
            return before
        raise TransitionRefused(f"an uncertain {request!r} is resolved only by observing its effect or its definite absence")
    if event in RECOVERY_EVENTS:
        raise TransitionRefused(f"{event!r} applies only to an uncertain remote request")
    step = EVENTS.get(event)
    if step is None:
        raise TransitionRefused(f"unknown transition event {event!r}")
    if stop and event in STOP_REFUSED_EVENTS:
        raise TransitionRefused(f"stop is asserted: {event!r} starts a new effect or retires work and is refused")
    if step[0] != current:
        raise TransitionRefused(f"{event!r} needs phase {step[0]!r}, transition is at {current!r}")
    return step[1]


def replay(phases: list[tuple[str, bool]] | tuple[tuple[str, bool], ...], *, start: str = "reviewed") -> str:
    """Derive the current phase from a sequence of (event, stop) pairs, refusing at the first
    illegal step. The journal verifier uses this so a receipt can never record a skipped phase."""
    phase = validate_phase(start)
    for event, stop in phases:
        phase = advance(phase, event, stop=bool(stop))
    return phase


# ---- identity --------------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionIdentity:
    repository_id: int
    generation: int
    old_programme_sha256: str
    successor_sha256: str
    plan_sha256: str
    consent_sha256: str
    source_sha: str

    @property
    def transition_id(self) -> str:
        return sha256_value(self.to_dict(with_id=False))

    def to_dict(self, *, with_id: bool = True) -> dict:
        value = {"schema": "dark-factory/transition-identity", "schema_version": "1.0",
                 "repository_id": self.repository_id, "generation": self.generation,
                 "old_programme_sha256": self.old_programme_sha256, "successor_sha256": self.successor_sha256,
                 "plan_sha256": self.plan_sha256, "consent_sha256": self.consent_sha256, "source_sha": self.source_sha}
        if with_id:
            value["transition_id"] = self.transition_id
        return value


def transition_identity(value: Mapping[str, Any]) -> TransitionIdentity:
    """Bind one transition to the original repository numeric identity, old and successor
    programme hashes, generation, frozen plan hash, exact consent and source (SPEC 7). Every
    field is required and exact; nothing is defaulted."""
    if not isinstance(value, Mapping):
        raise TransitionRefused("transition identity must be a mapping")
    fields = {"repository_id", "generation", "old_programme_sha256", "successor_sha256", "plan_sha256",
              "consent_sha256", "source_sha"}
    if set(value) - fields or fields - set(value):
        raise TransitionRefused(f"transition identity needs exactly {sorted(fields)}")
    for key in ("repository_id", "generation"):
        if type(value[key]) is not int or value[key] < (1 if key == "repository_id" else 0):
            raise TransitionRefused(f"{key} must be a nonnegative integer (repository_id positive)")
    for key, size in (("old_programme_sha256", 64), ("successor_sha256", 64), ("plan_sha256", 64),
                      ("consent_sha256", 64), ("source_sha", 40)):
        if not isinstance(value[key], str) or not HEX[size].fullmatch(value[key]):
            raise TransitionRefused(f"{key} must be {size} lowercase hex characters")
    if value["old_programme_sha256"] == value["successor_sha256"]:
        raise TransitionRefused("a transition needs a successor that differs from the old programme")
    return TransitionIdentity(**{k: value[k] for k in fields})


# ---- release decision ------------------------------------------------------------------------

def _known_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _known_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def request_release(observation: Mapping[str, Any]) -> dict:
    """Eligibility to request fence removal (SPEC 7 step 8, C07 last paragraph). Output only;
    no remote call is made or counted here (`remote_calls` is 0 by construction).

    Inputs are independently observed facts: `observed` (the observer itself produced a
    current result), `fenced` (the fence is present on protected main), `consent` (current
    release consent), `successor` (the successor and its lineage were observed at main),
    `queued`/`active` (old-generation runs still queued or active), `pending_effects`
    (started effects not yet observed or reconciled), `accounting_reconciled`. Anything
    unknown, missing or of the wrong type keeps the fence: it is reported under the gate it
    belongs to, never treated as satisfied. Every failing gate is listed, in precedence order.
    """
    if not isinstance(observation, Mapping):
        raise TransitionRefused("release observation must be a mapping")
    unknown = set(observation) - set(RELEASE_FIELDS)
    if unknown:
        raise TransitionRefused(f"unexpected release observation fields: {sorted(unknown)}")
    observed = _known_bool(observation.get("observed"))
    fenced = _known_bool(observation.get("fenced"))
    consent = _known_bool(observation.get("consent"))
    successor = _known_bool(observation.get("successor"))
    queued = _known_count(observation.get("queued"))
    active = _known_count(observation.get("active"))
    pending = _known_count(observation.get("pending_effects"))
    accounting = _known_bool(observation.get("accounting_reconciled"))
    gates = {
        "observed": observed is True,
        "fenced": fenced is True,
        "consent": consent is True,
        "successor": successor is True,
        "drained": queued == 0 and active == 0,
        "effects": pending == 0,
        "accounting_reconciled": accounting is True,
    }
    if observed is not True:
        # Without a current observer result nothing else is known; the observer is the reason.
        return {"status": "blocked", "reason_codes": ["observation_missing"], "remote_calls": 0}
    codes = [code for gate, code in RELEASE_ORDER if not gates[gate]]
    return {"status": "eligible" if not codes else "blocked", "reason_codes": codes, "remote_calls": 0}


# ---- journal / effect-store agreement -------------------------------------------------------

def compare_stores(journal_phase: str, store_state: str | None) -> str:
    """After a crash the journal (canonical receipts) and the SQLite effect store may disagree
    about a remote request. `store_state` is the lease store's grant state for the request
    (`started`, `observed_success`, `observed_failure`, `uncertain`) or None when the store holds
    no grant. Returns `consistent`, `uncertain` (both sides agree the outcome is unknown, the
    recovery observer must re-observe) or `reconciliation_required` (a mismatch; neither side is
    trusted as "further ahead")."""
    phase = validate_phase(journal_phase)
    if phase == RECONCILIATION_REQUIRED:
        return RECONCILIATION_REQUIRED
    if phase in REMOTE_REQUESTS:
        # The journal says the request is pending: the store must show it started or uncertain.
        if store_state in {"started", "uncertain"}:
            return "consistent" if store_state == "started" else "uncertain"
        return RECONCILIATION_REQUIRED
    if is_uncertain(phase):
        if store_state in {"started", "uncertain"}:
            return "uncertain"
        return RECONCILIATION_REQUIRED
    # An observed or pre-request phase: no request may still be open in the store.
    if store_state in {None, "observed_success", "observed_failure"}:
        return "consistent"
    return RECONCILIATION_REQUIRED


__all__ = ["EVENTS", "PHASES", "RECONCILIATION_REQUIRED", "RELEASE_FIELDS", "RELEASE_ORDER", "REMOTE_REQUESTS",
           "STOP_REFUSED_EVENTS", "TransitionIdentity", "TransitionRefused", "advance", "compare_stores", "is_uncertain",
           "replay", "request_of", "request_release", "transition_identity", "validate_phase"]
