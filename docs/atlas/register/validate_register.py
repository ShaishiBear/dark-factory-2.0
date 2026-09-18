#!/usr/bin/env python3
"""Validate the Dark Factory decision register.

Checks structural integrity only. It cannot check whether a decision is right,
which is the point: the register records claims, and evidence promotes them.

Usage:  python validate_register.py [path/to/decisions.json]
Exit 0 = valid, 1 = errors found.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

VALID_STATUSES = {
    "PROPOSED",
    "SETTLED",
    "CONSTITUTIONAL",
    "BUILT",
    "PARTIAL",
    "SUPERSEDED",
    "OPEN",
    "AMENDMENT",
}
VALID_TIERS = {0, 1, 2, 3}
KNOWN_REGISTERS = {"DFA", "DFC", "DFP", "DFF", "DFM", "DFV", "DFG", "DFE"}

# A decision at these statuses may not be contradicted without an ACP.
REQUIRES_ACP = {"CONSTITUTIONAL", "SETTLED"}


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate(doc: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    decisions = doc.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return (["`decisions` missing or empty"], [])

    ids: set[str] = set()
    by_register: dict[str, list[dict]] = defaultdict(list)

    for i, d in enumerate(decisions):
        where = d.get("id", f"index {i}")

        for field in ("id", "title", "tier", "status"):
            if field not in d:
                errors.append(f"{where}: missing required field `{field}`")

        did = d.get("id", "")
        if did in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(did)

        prefix = did.split("-")[0] if "-" in did else ""
        if prefix not in KNOWN_REGISTERS:
            errors.append(f"{where}: unknown register prefix `{prefix}`")
        else:
            by_register[prefix].append(d)

        if d.get("status") not in VALID_STATUSES:
            errors.append(f"{where}: invalid status `{d.get('status')}`")

        if d.get("tier") not in VALID_TIERS:
            errors.append(f"{where}: invalid tier `{d.get('tier')}`")

        if d.get("status") == "SUPERSEDED" and not d.get("superseded_by"):
            errors.append(f"{where}: SUPERSEDED without `superseded_by`")

        if d.get("status") == "AMENDMENT" and not d.get("affects"):
            errors.append(f"{where}: AMENDMENT without `affects`")

        if d.get("status") == "CONSTITUTIONAL" and d.get("tier") != 3:
            warnings.append(
                f"{where}: CONSTITUTIONAL but tier {d.get('tier')} — expected tier 3"
            )

    # Referential integrity, second pass so forward references resolve.
    for d in decisions:
        where = d.get("id", "?")
        for field in ("superseded_by", "supersedes", "affects"):
            value = d.get(field)
            if value is None:
                continue
            targets = value if isinstance(value, list) else [value]
            for target in targets:
                if target not in ids:
                    errors.append(f"{where}: `{field}` points at unknown id `{target}`")

    # Supersession should be symmetric where both sides assert it.
    for d in decisions:
        target = d.get("superseded_by")
        if not target:
            continue
        replacement = next((x for x in decisions if x.get("id") == target), None)
        if replacement and "supersedes" in replacement:
            listed = replacement["supersedes"]
            listed = listed if isinstance(listed, list) else [listed]
            if d["id"] not in listed:
                warnings.append(
                    f"{d['id']}: superseded_by {target}, but {target} does not list it"
                )

    return errors, warnings


def report(doc: dict) -> None:
    decisions = doc["decisions"]
    statuses = Counter(d["status"] for d in decisions)
    tiers = Counter(d["tier"] for d in decisions)
    registers = Counter(d["id"].split("-")[0] for d in decisions)

    print(f"\n{len(decisions)} decisions\n")

    print("By register")
    for name, label in doc.get("registers", {}).items():
        count = registers.get(name, 0)
        print(f"  {name}  {count:>4}   {label}")
    print()

    print("By status")
    for status, count in statuses.most_common():
        print(f"  {status:<16} {count:>4}")
    print()

    print("By tier")
    for tier in sorted(tiers):
        print(f"  tier {tier}          {tiers[tier]:>4}")
    print()

    locked = [d for d in decisions if d["status"] in REQUIRES_ACP]
    print(f"Decisions requiring an ACP to contradict: {len(locked)}")

    unevidenced = [d for d in decisions if d["status"] == "PROPOSED"]
    print(f"Decisions at PROPOSED awaiting evidence:  {len(unevidenced)}")

    amendments = [d for d in decisions if d["status"] == "AMENDMENT"]
    if amendments:
        print(f"\nOpen amendments ({len(amendments)}):")
        for a in amendments:
            affects = ", ".join(a.get("affects", []))
            print(f"  {a['id']}  {a['title']}")
            print(f"            affects: {affects}")


def prose_agrees(doc: dict, prose: Path) -> list[str]:
    """DECISION_REGISTER.md must not disagree with the register it describes.

    `Register valid.` was printed on a register whose markdown said 417 decisions and 27 open
    amendments while decisions.json held 418 and 28, because this program validated the JSON
    and never looked at the prose beside it. A green marker that examined less than its reader
    assumes is DFE-027's species -- found here in the tooling built to police the register that
    holds the entry about it.

    Counts only. Whether the prose is *right* is not checkable; whether it contradicts the data
    it summarises is, and that is the whole of the defect observed.
    """
    if not prose.exists():
        return []
    text = prose.read_text(encoding="utf-8")
    decisions = doc["decisions"]
    statuses = Counter(d["status"] for d in decisions)
    tiers = Counter(d["tier"] for d in decisions)
    expected = [
        (f"{len(decisions)} decisions", "total decisions"),
        (f"AMENDMENT         {statuses['AMENDMENT']:<5}", "open amendment count"),
        (f"tier 0   {tiers[0]}      tier 1  {tiers[1]}      "
         f"tier 2  {tiers[2]}      tier 3   {tiers[3]}", "tier counts"),
    ]
    problems = [f"{prose.name} does not carry the current {what} ({fragment.strip()!r})"
                for fragment, what in expected if fragment.rstrip() not in text]
    missing = sorted(d["id"] for d in decisions
                     if d["status"] == "AMENDMENT" and d["id"] not in text)
    if missing:
        problems.append(f"{prose.name} does not mention open amendment(s): {', '.join(missing)}")
    return problems


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("decisions.json")
    if not path.exists():
        print(f"No register at {path}")
        return 1

    doc = load(path)
    errors, warnings = validate(doc)
    errors.extend(prose_agrees(doc, path.with_name("DECISION_REGISTER.md")))

    for w in warnings:
        print(f"WARN   {w}")
    for e in errors:
        print(f"ERROR  {e}")

    if errors:
        print(f"\n{len(errors)} error(s). Register invalid.")
        return 1

    report(doc)
    print("\nRegister valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
