"""Typed, durable validation refusals.

Until D-023 every validation refusal reached the failure handler as a bare `RuntimeError` or
`NeedsHuman`, the PR comment recorded only the exception's class name, and the two logs that
carried the real reason stayed on an ephemeral runner. Nothing downstream could tell a
security-guard veto from a moved base. This module is the vocabulary that makes a refusal a fact
the kernel can act on later: a stable `reason_code`, the authority that produced it, and a
redacted record that outlives the runner.

Nothing here repairs anything. A model repair loop is deliberately not built (D-023): the only
consumer of a reason code today is the model-free re-head for `stale_base`, and the honest
reason to record the other codes is to have data before deciding whether any of them deserves a
loop at all.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Mapping

REASON_CODES: tuple[str, ...] = (
    "identity",
    "security_guard",
    "attached_evidence",
    "code_holdout",
    "provenance",
    "architecture_holdout",
    "certifier:contract",
    "certifier:design",
    "certifier:governor",
    "evidence_spine",
    "merge_preauth",
    "stale_base",
    "unknown",
)

# The authority that speaks for each reason code, for humans reading the PR.
AUTHORITY: Mapping[str, str] = {
    "identity": "validator preconditions",
    "security_guard": "deterministic security guard (scripts/factory_security.py)",
    "attached_evidence": "attached contract/proof parser",
    "code_holdout": "blinded code holdout",
    "provenance": "builder provenance verifier",
    "architecture_holdout": "independent architecture holdout",
    "certifier:contract": "independent contract certifier",
    "certifier:design": "independent design certifier",
    "certifier:governor": "independent governor certifier",
    "evidence_spine": "evidence spine (scripts/factory_evidence_spine.py)",
    "merge_preauth": "merge pre-authorization (harness/merge_verify.py pre)",
    "stale_base": "base moved under the PR (model-free re-head)",
    "unknown": "unclassified",
}

# The three producers of a stale-base refusal, pinned by their message text. They live in
# programs the kernel invokes as subprocesses, so text is the only channel; the test suite pins
# each string against its producer so a reworded message cannot silently drop the class.
STALE_BASE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("factory_kernel/provenance.py", "builder provenance was built from a different base"),
    ("harness/merge_verify.py", "main moved after evidence"),
    ("scripts/factory_evidence.py", "PR trust root is not current with origin/main"),
)

# Non-stale reasons that the kernel decides from the stage it was in, not from a tool's text.
STAGE_CODES: frozenset[str] = frozenset({
    "identity", "security_guard", "attached_evidence", "code_holdout", "provenance",
    "architecture_holdout", "evidence_spine", "merge_preauth",
})

REFUSAL_MARKER = "<!-- dark-factory-refusal:"
REHEAD_MARKER = "<!-- dark-factory-rehead:"
RESUME_MARKER = "<!-- dark-factory-resume:"
_MARKER_END = "-->"
_CERTIFIER = re.compile(r"independent (contract|design|architecture-governor) certifier")

# Secret shapes the guard refuses in diffs; a refusal tail is scrubbed with the same shapes
# before it is written anywhere durable. Duplicated rather than imported: this module must not
# depend on a script's import path, and the test suite pins the two lists equal.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s/@]+:[^@\s/]+@", re.I
    ),
)
GENERIC_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|access[_-]?token|password)\b\s*[:=]\s*[\"']([^\"']{16,})[\"']"
)
REDACTED = "[REDACTED]"


class ToolRefused(RuntimeError):
    """A deterministic subprocess refused. Carries what refused, not just that something did.

    A subclass of RuntimeError so every existing handler and test that expects the old bare
    error keeps working; the message is byte-identical to the old one.
    """

    def __init__(self, argv: list[str], *, rc: int, output: str) -> None:
        self.argv = list(argv)
        self.tool = tool_name(argv)
        self.phase = tool_phase(argv)
        self.rc = int(rc)
        self.tail = output[-4000:]
        super().__init__(f"{' '.join(argv)} failed rc={rc}: {self.tail}")


def tool_name(argv: Iterable[str]) -> str:
    items = list(argv)
    if not items:
        return ""
    if items[0] == "python" and len(items) > 1:
        return PurePosixPath(items[1].replace("\\", "/")).name
    return PurePosixPath(items[0].replace("\\", "/")).name


def tool_phase(argv: Iterable[str]) -> str:
    items = list(argv)
    if len(items) > 2 and items[0] == "python" and not items[2].startswith("-"):
        return items[2]
    if len(items) > 1 and items[0] != "python" and not items[1].startswith("-"):
        return items[1]
    return ""


def is_stale_base(text: str) -> bool:
    return any(pattern in text for _producer, pattern in STALE_BASE_PATTERNS)


def classify(stage: str, exc: BaseException) -> str:
    """The stable reason code for a refusal raised while the validator was in `stage`.

    Text is consulted only where a stage has more than one refuser inside it: a stale base is
    reported by three different programs, and the evidence spine is the one stage that also
    speaks for the architecture holdout.
    """
    text = str(exc)
    if is_stale_base(text):
        return "stale_base"
    if isinstance(exc, ToolRefused):
        if exc.tool == "factory_security.py":
            return "security_guard"
        if exc.tool == "factory_provenance.py":
            return "provenance"
        if exc.tool in {"factory_evidence.py", "factory_evidence_spine.py"}:
            return "architecture_holdout" if "architecture holdout" in text.lower() else "evidence_spine"
        if exc.tool == "merge_verify.py" and exc.phase == "pre":
            return "merge_preauth"
    if stage == "certifier":
        match = _CERTIFIER.search(text)
        if match:
            return "certifier:" + {"architecture-governor": "governor"}.get(match.group(1), match.group(1))
        return "unknown"
    if stage in STAGE_CODES:
        return stage
    return "unknown"


def scrub(text: str) -> str:
    """Redact every secret shape the guard knows before a refusal tail is written anywhere."""
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    out = GENERIC_SECRET.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    return out


@dataclass(frozen=True)
class Refusal:
    reason_code: str
    authority: str
    tool: str
    phase: str
    rc: int | None
    detail: str
    exception: str


def describe(stage: str, exc: BaseException) -> Refusal:
    code = classify(stage, exc)
    if code not in REASON_CODES:
        raise ValueError(f"unknown reason code {code!r}")
    tool = exc.tool if isinstance(exc, ToolRefused) else ""
    phase = exc.phase if isinstance(exc, ToolRefused) else ""
    rc = exc.rc if isinstance(exc, ToolRefused) else None
    tail = exc.tail if isinstance(exc, ToolRefused) else str(exc)
    return Refusal(
        reason_code=code,
        authority=AUTHORITY[code],
        tool=tool,
        phase=phase,
        rc=rc,
        detail=scrub(tail)[-2000:],
        exception=type(exc).__name__,
    )


def refusal_record(
    refusal: Refusal, *, pr: int, head: str, base: str, stage: str, timestamp: str
) -> dict:
    return {
        "version": "1.0",
        "pr": pr,
        "head": head,
        "base": base,
        "stage": stage,
        "reason_code": refusal.reason_code,
        "authority": refusal.authority,
        "tool": refusal.tool,
        "phase": refusal.phase,
        "rc": refusal.rc,
        "exception": refusal.exception,
        "detail": refusal.detail,
        "timestamp": timestamp,
    }


def render_refusal_marker(record: Mapping[str, object]) -> str:
    """The machine-readable half of the PR comment. Only identity fields travel in the marker;
    the scrubbed detail is in the uploaded refusal record, not in a comment that lasts forever."""
    payload = {
        "version": "1.0",
        "pr": record["pr"],
        "head": record["head"],
        "base": record["base"],
        "stage": record["stage"],
        "reason_code": record["reason_code"],
        "authority": record["authority"],
        "timestamp": record["timestamp"],
    }
    return f"{REFUSAL_MARKER} {json.dumps(payload, sort_keys=True)} {_MARKER_END}"


def render_rehead_marker(payload: Mapping[str, object]) -> str:
    return f"{REHEAD_MARKER} {json.dumps(dict(payload), sort_keys=True)} {_MARKER_END}"


def render_resume_marker(payload: Mapping[str, object]) -> str:
    return f"{RESUME_MARKER} {json.dumps(dict(payload), sort_keys=True)} {_MARKER_END}"


def _markers(bodies: Iterable[str], prefix: str) -> list[dict]:
    found: list[dict] = []
    for body in bodies:
        if not isinstance(body, str):
            continue
        start = 0
        while True:
            at = body.find(prefix, start)
            if at < 0:
                break
            end = body.find(_MARKER_END, at)
            if end < 0:
                break
            raw = body[at + len(prefix):end].strip()
            start = end + len(_MARKER_END)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found.append(value)
    return found


def latest_refusal(bodies: Iterable[str]) -> dict | None:
    """The most recent refusal marker in comment order, or None."""
    found = _markers(bodies, REFUSAL_MARKER)
    return found[-1] if found else None


def rehead_count(bodies: Iterable[str]) -> int:
    return len(_markers(bodies, REHEAD_MARKER))


def resume_count(bodies: Iterable[str]) -> int:
    """How many times a pushed-but-unpublished PR has been resumed from uploaded artifacts."""
    return len(_markers(bodies, RESUME_MARKER))


def rehead_eligible(bodies: Iterable[str]) -> bool:
    """A PR may be re-headed exactly once, and only when its latest refusal is a stale base.

    Every other reason code is either terminal or a candidate for a loop nobody has data to
    size yet; neither is this function's business.
    """
    bodies = list(bodies)
    refusal = latest_refusal(bodies)
    if refusal is None or refusal.get("reason_code") != "stale_base":
        return False
    return rehead_count(bodies) == 0
