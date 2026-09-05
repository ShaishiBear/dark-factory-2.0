"""Evidence blocks attached to a pull-request body, and the text that may enter them.

Two authorities share this module so they cannot drift: the scripts that ATTACH a block
(`factory_protocol.py attach`, `factory_proof.py attach`, `factory_artifacts.py attach-design`)
and the kernel that later EXTRACTS it (`KernelRuntime._extract_attached`). An attach is only
complete when the body read back from GitHub yields the same canonical bytes that were sent.

Runner output that travels inside a proof (`red_output_tail`) is sanitised here before it is
stored: terminal control sequences are not evidence, and the first production validation
(worker run 33931048575) refused a correct proof because a vitest colour escape came back from
the PR body as a backslash followed by caret-notation `^[`, which is not a JSON escape (D-038).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

KINDS = ("contract", "design", "proof")

# CSI sequences (ESC [ params intermediates final), OSC sequences (ESC ] ... BEL/ST), two-byte
# ESC sequences, and any lone ESC that is left over.
_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI: ESC [ ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... BEL or ESC \
    r"|\x1b[ -/]+[0-~]"                  # nF escapes: ESC, one or more intermediates, a final (ESC ( B)
    r"|\x1b[6-9=>@-Z\\-_]"              # Fp/Fe/Fs escapes: ESC 7, ESC 8, ESC =, ESC >, ESC D, ESC \
    r"|\x1b"                            # anything else that starts with ESC
)
# Every C0 control character except newline and tab, plus DEL.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REPLACEMENT = "�"


def sanitise_output(text: str) -> str:
    """Return runner output with terminal control sequences removed.

    ANSI/CSI/OSC escapes are stripped; any remaining C0 control character other than newline
    and tab (and DEL) becomes U+FFFD. Idempotent. Symptom matching and storage must both use
    the result, so the evidence a validator reads is exactly what the check was made against.
    """
    without_escapes = _ANSI.sub("", text)
    return _CONTROL.sub(REPLACEMENT, without_escapes)


def has_control_bytes(text: str) -> bool:
    return bool(_ANSI.search(text) or _CONTROL.search(text))


def block_pattern(kind: str) -> re.Pattern[str]:
    if kind not in KINDS:
        raise ValueError(f"unknown attached evidence kind {kind!r}")
    return re.compile(
        rf"<!-- factory-{kind}:start -->\s*```factory-{kind}\s*(\{{.*?\}})\s*```",
        re.S,
    )


def extract_block(body: str, kind: str) -> Mapping[str, Any]:
    """Parse the `kind` block out of a PR body exactly as the validator does.

    Raises ValueError with a stable message when the block is missing, is not JSON, or is
    not an object; callers map that to their own refusal type.
    """
    match = block_pattern(kind).search(body)
    if not match:
        raise ValueError(f"PR is missing attached factory-{kind} evidence")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"attached factory-{kind} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"attached factory-{kind} must be an object")
    return value


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def round_trip_ok(body: str, kind: str, expected: Mapping[str, Any]) -> bool:
    """True when the block read back from GitHub canonicalises to the same bytes as `expected`."""
    try:
        got = extract_block(body, kind)
    except ValueError:
        return False
    return canonical_bytes(got) == canonical_bytes(expected)
