#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from factory_protocol import canonical, validate_contract

ROOT = Path(__file__).resolve().parents[1]
PART_OF = re.compile(r"(?im)^Part of\s+#([1-9][0-9]*)\s*$")
BLOCKED = re.compile(r"(?im)^Blocked by:\s+#([1-9][0-9]*)\s*$")


def die(message: str) -> None:
    print(f"ARTIFACT_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        die(f"{path} must contain an object")
    return value


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write(path: str, value: dict) -> str:
    Path(path).write_bytes(canonical(value))
    return digest(value)


def gh_issue(number: int) -> dict:
    proc = subprocess.run(
        ["gh", "issue", "view", str(number), "--json", "number,title,body,state,labels,url"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        die(f"cannot read issue #{number}: {(proc.stderr or proc.stdout)[-800:]}")
    return json.loads(proc.stdout)


def compile_ticket(args: argparse.Namespace) -> None:
    contract = load(args.contract)
    contract_hash = validate_contract(contract, args.issue)
    issue = gh_issue(args.issue)
    body = issue.get("body") or ""
    labels = sorted(x.get("name", "") for x in issue.get("labels", []))
    blockers = []
    for number in sorted({int(x) for x in BLOCKED.findall(body)}):
        state = str(gh_issue(number).get("state", "OPEN")).upper()
        blockers.append({"issue": number, "state": state})
    parent_match = PART_OF.search(body)
    acceptance = [b["id"] for b in contract["behaviors"]]
    seams = {b["id"]: b["seam"] for b in contract["behaviors"]}
    ticket = {
        "version": "1.0", "issue": args.issue, "title": issue.get("title") or "",
        "parent": int(parent_match.group(1)) if parent_match else None,
        "contract_sha256": contract_hash, "acceptance": acceptance, "test_seams": seams,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    ready = (
        str(issue.get("state", "OPEN")).upper() == "OPEN"
        and "factory:accepted" in labels
        and all(x["state"] == "CLOSED" for x in blockers)
    )
    frontier = {
        "version": "1.0", "issue": args.issue, "accepted": "factory:accepted" in labels,
        "blockers": blockers, "ready": ready,
        "ticket_sha256": digest(ticket),
    }
    if not ready:
        die(f"issue #{args.issue} is not on the ready frontier")
    th = write(args.ticket_output, ticket)
    fh = write(args.frontier_output, frontier)
    print(f"TICKET_OK issue={args.issue} sha256={th} frontier_sha256={fh}")


def nonempty_strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        die(f"design {name} must be a non-empty string list")
    return value


def compile_design(args: argparse.Namespace) -> None:
    raw = load(args.input)
    contract = load(args.contract)
    context = load(args.context)
    contract_hash = validate_contract(contract)
    context_hash = digest(context)
    required = {"version", "modules", "seams", "public_interfaces", "invariants", "data_flows", "ac_mapping"}
    if required - raw.keys() or raw.get("version") != "1.0":
        die("design must be version 1.0 with modules/seams/public_interfaces/invariants/data_flows/ac_mapping")
    design = {
        "version": "1.0", "contract_sha256": contract_hash, "context_sha256": context_hash,
        "modules": nonempty_strings(raw["modules"], "modules"),
        "seams": nonempty_strings(raw["seams"], "seams"),
        "public_interfaces": raw["public_interfaces"] if isinstance(raw["public_interfaces"], list) else die("design public_interfaces must be a list"),
        "invariants": nonempty_strings(raw["invariants"], "invariants"),
        "data_flows": nonempty_strings(raw["data_flows"], "data_flows"),
        "ac_mapping": raw["ac_mapping"],
    }
    ids = {b["id"] for b in contract["behaviors"]}
    mapping = design["ac_mapping"]
    if not isinstance(mapping, dict) or set(mapping) != ids:
        die("design ac_mapping must cover every contract AC exactly")
    for ac, seams in mapping.items():
        if not isinstance(seams, list) or not seams or any(not isinstance(x, str) or not x.strip() for x in seams):
            die(f"design {ac} must map to one or more seams")
        if any(x not in design["seams"] for x in seams):
            die(f"design {ac} references an undeclared seam")
    dh = write(args.output, design)
    print(f"DESIGN_OK sha256={dh} criteria={len(ids)} seams={len(design['seams'])}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ticket")
    p.add_argument("--issue", type=int, required=True); p.add_argument("--contract", required=True)
    p.add_argument("--ticket-output", required=True); p.add_argument("--frontier-output", required=True)
    p.set_defaults(fn=compile_ticket)
    p = sub.add_parser("design")
    p.add_argument("--input", required=True); p.add_argument("--contract", required=True)
    p.add_argument("--context", required=True); p.add_argument("--output", required=True)
    p.set_defaults(fn=compile_design)
    args = parser.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
