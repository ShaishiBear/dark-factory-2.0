#!/usr/bin/env python3
"""One spelling rule for every worker-written list.

Model workers keep writing lists whose entries are objects (`{"path": ..., "why": ...}`,
`{"id": ..., "verdict": ...}`) where a deterministic compiler wanted plain strings. The eighth
canary attempt (run 33916377607) died at the context gate that way, the seventh (run
33914596611) at the governor gate. The content was right both times; only the spelling was.

Rule: every validator accepts each entry of a worker-written list as EITHER a plain string OR
an object carrying that list's canonical key, and normalises to the string before it validates
or hashes. Compiled outputs and hashes therefore stay exactly what they were. An object without
the canonical key, a non-string value, or a duplicate after normalisation is refused: this
accepts an equivalent spelling, it never drops a check. A worker that wants to explain an entry
puts the explanation in a top-level `notes` string, which every validator ignores.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

# One test-path predicate for the three authorities that classify a path as a test: the
# acceptance-test commit envelope (git_authority), the RED gate (factory_proof) and the post-code
# architecture guard (factory_architecture_guard). Before D-030 the guard recognised only the
# directory and infix markers, so `app/backend/routes/test_export.py` passed commit and RED and was
# then refused two gates later as an unplanned production file, with a stray commit already made.
TEST_MARKERS: tuple[str, ...] = ("/tests/", "/__tests__/", ".test.", ".spec.")


def test_shaped(path: str) -> bool:
    """True when every test-classifying authority treats `path` as a test file."""
    low = "/" + path.replace("\\", "/").lower()
    return (
        "test" in PurePosixPath(path).name.lower()
        or any(marker in low for marker in TEST_MARKERS)
        or low.endswith("/conftest.py")
    )

# The keys a list entry may carry its value under, per list. The first is canonical.
CANONICAL_KEYS: dict[str, tuple[str, ...]] = {
    # context.raw.json
    "files": ("path", "name"),
    "tests": ("path", "name"),
    "adrs": ("path", "name"),
    "symbols": ("name",),
    "callers": ("name",),
    "invariants": ("text", "why"),
    "history": ("text", "why"),
    # design.raw.json
    "modules": ("name",),
    "seams": ("name",),
    "public_interfaces": ("name",),
    "data_flows": ("name", "text"),
    "planned_files": ("path", "name"),
    "allowed_new_files": ("path", "name"),
    # governor / conformance
    "principles": ("id",),
    "migrations": ("id",),
    "debts": ("id",),
    "rationale": ("text",),
    "findings": ("text",),
    "required_changes": ("text",),
}

# A free-text field workers may add anywhere; validators ignore it.
NOTES_FIELD = "notes"

# A governor that walks the whole policy and marks each entry `"applicable": false` has
# said the same thing as omitting it. Only an explicit boolean false is an omission; the
# absence of the field, or any other value, leaves the entry in and lets the validator judge.
APPLICABLE_FIELD = "applicable"


def entry_to_string(entry: object, keys: tuple[str, ...], where: str, die: Callable[[str], None]) -> str | None:
    """Return the canonical string for one list entry, None for an explicit non-entry, or die."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        if entry.get(APPLICABLE_FIELD) is False:
            return None
        for key in keys:
            if key in entry:
                value = entry[key]
                if not isinstance(value, str) or not value.strip():
                    die(f"{where} entry has a non-string or empty {key!r}")
                return value
        die(f"{where} entry object lacks its canonical key {keys[0]!r}")
    die(f"{where} entry must be a string or an object with {keys[0]!r}")
    raise AssertionError("die must not return")


def normalise_list(value: object, name: str, where: str, die: Callable[[str], None]) -> object:
    """Normalise a worker-written list to plain strings; leave non-lists for the caller to judge."""
    if not isinstance(value, list):
        return value
    keys = CANONICAL_KEYS.get(name, ("name",))
    strings = [entry_to_string(x, keys, where, die) for x in value]
    out = [x for x in strings if x is not None]
    if len(out) != len(set(out)):
        die(f"{where} contains duplicate entries after normalisation")
    return out


def normalise_lists(raw: dict, names: tuple[str, ...], where: str, die: Callable[[str], None]) -> dict:
    """Return a copy of `raw` with every named list normalised and `notes` removed."""
    out = {k: v for k, v in raw.items() if k != NOTES_FIELD}
    for name in names:
        if name in out:
            out[name] = normalise_list(out[name], name, f"{where} {name}", die)
    return out
