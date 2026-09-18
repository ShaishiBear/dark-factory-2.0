#!/usr/bin/env python3
"""Produce a per-issue decision extract from the register.

Implements DFE-010: an issue should carry the specific decisions it must
honour, not a reference to six architecture documents. Applies the
ExperiencePacket pattern (DFC-047) to the corpus itself.

Usage:
    python extract.py DFA-007 DFV-004 DFC-033      # by id
    python extract.py --grep capability             # by keyword
    python extract.py --register DFM --tier 2       # by filter
    python extract.py --grep lease --markdown       # paste into an issue

Ids resolve transitively: naming a decision also pulls in anything it
supersedes, is superseded by, or is amended by, so an extract can never
quietly omit the amendment that changes it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REGISTER = Path(__file__).with_name("decisions.json")


def load() -> list[dict]:
    with REGISTER.open(encoding="utf-8") as handle:
        return json.load(handle)["decisions"]


def related(decisions: list[dict], seed_ids: set[str]) -> set[str]:
    """Close the seed set over supersession and amendment links."""
    index = {d["id"]: d for d in decisions}
    resolved = set(seed_ids)
    frontier = set(seed_ids)

    while frontier:
        nxt: set[str] = set()
        for did in frontier:
            d = index.get(did)
            if not d:
                continue
            for field in ("superseded_by", "supersedes", "affects"):
                value = d.get(field)
                if not value:
                    continue
                targets = value if isinstance(value, list) else [value]
                nxt.update(t for t in targets if t not in resolved)
        # Amendments that affect anything in the set must come along too.
        for d in decisions:
            if d["id"] in resolved:
                continue
            affects = d.get("affects") or []
            if any(a in resolved for a in affects):
                nxt.add(d["id"])
        resolved |= nxt
        frontier = nxt

    return resolved


def render(items: list[dict], markdown: bool) -> str:
    lines: list[str] = []
    if markdown:
        lines.append("### Decisions this issue must honour\n")
        for d in items:
            flag = f"**{d['status']}**"
            lines.append(f"- `{d['id']}` {flag} · tier {d['tier']} — {d['title']}")
            if d.get("note"):
                lines.append(f"  - {d['note']}")
            if d.get("rationale"):
                lines.append(f"  - {d['rationale']}")
        lines.append(
            "\n*A change contradicting a CONSTITUTIONAL or SETTLED decision requires an ACP.*"
        )
    else:
        for d in items:
            lines.append(f"{d['id']}  [{d['status']}] tier {d['tier']}  {d['title']}")
            if d.get("note"):
                lines.append(f"          note: {d['note']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", help="decision ids, e.g. DFA-007")
    parser.add_argument("--grep", help="case-insensitive match on title and note")
    parser.add_argument("--register", help="filter by register prefix, e.g. DFM")
    parser.add_argument("--tier", type=int, help="filter by tier")
    parser.add_argument("--status", help="filter by status")
    parser.add_argument("--markdown", action="store_true", help="issue-ready output")
    args = parser.parse_args()

    decisions = load()
    selected = decisions

    if args.ids:
        wanted = related(decisions, set(args.ids))
        selected = [d for d in selected if d["id"] in wanted]

    if args.grep:
        needle = args.grep.lower()
        hits = {
            d["id"]
            for d in selected
            if needle in d["title"].lower() or needle in (d.get("note") or "").lower()
        }
        wanted = related(decisions, hits)
        selected = [d for d in decisions if d["id"] in wanted]

    if args.register:
        selected = [d for d in selected if d["id"].startswith(args.register.upper())]
    if args.tier is not None:
        selected = [d for d in selected if d["tier"] == args.tier]
    if args.status:
        selected = [d for d in selected if d["status"] == args.status.upper()]

    if not selected:
        print("No decisions matched.")
        return 1

    print(render(selected, args.markdown))
    if not args.markdown:
        print(f"\n{len(selected)} decision(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
