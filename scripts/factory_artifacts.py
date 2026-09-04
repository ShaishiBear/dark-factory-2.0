#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from factory_protocol import canonical, validate_contract

ROOT = Path(__file__).resolve().parents[1]
PART_OF = re.compile(r"(?im)^Part of\s+#([1-9][0-9]*)\s*$")
BLOCKED = re.compile(r"(?im)^Blocked by:\s+#([1-9][0-9]*)\s*$")
DESIGN_BLOCK = re.compile(
    r"\n?<!-- factory-design:start -->.*?<!-- factory-design:end -->\n?", re.S
)


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


def load_issue_frontier(path: str, issue_number: int) -> tuple[dict, list[dict]]:
    """The kernel's snapshot of the issue and its `Blocked by` issues.

    This program runs with no credentials, so it never asks GitHub anything. The kernel fetched
    the issue and every blocker with its own authority before any model stage and wrote them
    here; readiness is judged from that snapshot. A missing or mismatched snapshot fails closed.
    """
    snapshot = load(path)
    if snapshot.get("version") != "1.0":
        die(f"{path} is not a v1.0 issue-frontier snapshot")
    issue = snapshot.get("issue")
    if not isinstance(issue, dict) or issue.get("number") != issue_number:
        die(f"{path} does not describe issue #{issue_number}")
    raw_blockers = snapshot.get("blockers")
    if not isinstance(raw_blockers, list):
        die(f"{path} blockers must be a list")
    body = str(issue.get("body") or "")
    named = sorted({int(x) for x in BLOCKED.findall(body)})
    blockers = []
    for entry in raw_blockers:
        if not isinstance(entry, dict) or not isinstance(entry.get("issue"), int):
            die(f"{path} contains a malformed blocker entry")
        blockers.append({"issue": int(entry["issue"]), "state": str(entry.get("state") or "OPEN").upper()})
    if sorted(b["issue"] for b in blockers) != named:
        die(f"{path} blockers do not match the `Blocked by` lines in the issue body")
    return issue, blockers


def compile_ticket(args: argparse.Namespace) -> None:
    contract = load(args.contract)
    contract_hash = validate_contract(contract, args.issue)
    issue, blockers = load_issue_frontier(args.issue_json, args.issue)
    body = issue.get("body") or ""
    labels = sorted(x.get("name", "") for x in issue.get("labels", []))
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


def nonempty_strings(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        die(f"design {name} must be {'a' if allow_empty else 'a non-empty'} string list")
    if any(not isinstance(x, str) or not x.strip() for x in value):
        die(f"design {name} must contain non-empty strings")
    if len(value) != len(set(value)):
        die(f"design {name} must not contain duplicates")
    return list(value)


def repo_files(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    files = nonempty_strings(value, name, allow_empty=allow_empty)
    for raw in files:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or raw != path.as_posix():
            die(f"design {name} contains unsafe path {raw!r}")
    return files


def compile_design(args: argparse.Namespace) -> None:
    raw = load(args.input)
    contract = load(args.contract)
    context = load(args.context)
    contract_hash = validate_contract(contract)
    context_hash = digest(context)
    required = {
        "version", "modules", "seams", "public_interfaces", "invariants", "data_flows",
        "ac_mapping", "planned_files", "allowed_new_files",
    }
    if required - raw.keys() or raw.get("version") != "1.0":
        die(
            "design must be version 1.0 with modules/seams/public_interfaces/invariants/"
            "data_flows/ac_mapping/planned_files/allowed_new_files"
        )
    planned = repo_files(raw["planned_files"], "planned_files")
    allowed_new = repo_files(raw["allowed_new_files"], "allowed_new_files", allow_empty=True)
    if not set(allowed_new).issubset(set(planned)):
        die("design allowed_new_files must be a subset of planned_files")
    context_files = {x for x in context.get("files", []) if isinstance(x, str)}
    for path in planned:
        exists = (ROOT / path).is_file()
        if exists and path not in context_files:
            die(f"design planned existing file is outside validated context: {path}")
        if path in allowed_new and exists:
            die(f"design allowed_new_files entry already exists: {path}")
        if not exists and path not in allowed_new:
            die(f"design planned file does not exist and is not explicitly allowed_new: {path}")
    design = {
        "version": "1.0", "contract_sha256": contract_hash, "context_sha256": context_hash,
        "modules": nonempty_strings(raw["modules"], "modules"),
        "seams": nonempty_strings(raw["seams"], "seams"),
        "public_interfaces": raw["public_interfaces"] if isinstance(raw["public_interfaces"], list) else die("design public_interfaces must be a list"),
        "invariants": nonempty_strings(raw["invariants"], "invariants"),
        "data_flows": nonempty_strings(raw["data_flows"], "data_flows"),
        "ac_mapping": raw["ac_mapping"],
        "planned_files": sorted(planned),
        "allowed_new_files": sorted(allowed_new),
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
    print(
        f"DESIGN_OK sha256={dh} criteria={len(ids)} seams={len(design['seams'])} "
        f"planned_files={len(planned)} allowed_new={len(allowed_new)}"
    )


def attach_design(args: argparse.Namespace) -> None:
    design = load(args.design)
    if design.get("version") != "1.0" or not isinstance(design.get("planned_files"), list):
        die("only a compiled design can be attached")
    raw = subprocess.run(
        ["gh", "pr", "view", str(args.pr), "--json", "body,headRefOid"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if raw.returncode:
        die("cannot read PR while attaching design")
    info = json.loads(raw.stdout)
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if local != info.get("headRefOid"):
        die("compiled design can only be attached from the exact PR head")
    block = (
        "\n<!-- factory-design:start -->\n```factory-design\n"
        + canonical(design).decode().strip()
        + "\n```\ndesign-sha256: "
        + digest(design)
        + "\n<!-- factory-design:end -->\n"
    )
    body = DESIGN_BLOCK.sub("\n", info.get("body") or "").rstrip() + block
    edit = subprocess.run(
        ["gh", "pr", "edit", str(args.pr), "--body", body],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if edit.returncode:
        die("could not attach design: " + (edit.stderr or edit.stdout)[-1000:])
    print(f"DESIGN_ATTACHED pr={args.pr} sha256={digest(design)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ticket")
    p.add_argument("--issue", type=int, required=True); p.add_argument("--contract", required=True)
    p.add_argument("--issue-json", required=True, help="kernel-written issue-frontier.json snapshot")
    p.add_argument("--ticket-output", required=True); p.add_argument("--frontier-output", required=True)
    p.set_defaults(fn=compile_ticket)
    p = sub.add_parser("design")
    p.add_argument("--input", required=True); p.add_argument("--contract", required=True)
    p.add_argument("--context", required=True); p.add_argument("--output", required=True)
    p.set_defaults(fn=compile_design)
    p = sub.add_parser("attach-design")
    p.add_argument("--design", required=True); p.add_argument("--pr", required=True)
    p.set_defaults(fn=attach_design)
    args = parser.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
