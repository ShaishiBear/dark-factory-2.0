#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AC = re.compile(r"^AC-[1-9][0-9]*$")
DEPENDENCY_ECOSYSTEMS = ("python", "javascript")
DEPENDENCY_FIELDS = ("name", "purpose", "why_existing_insufficient", "maintenance_evidence")
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._/@\[\]-]*$")


def die(msg: str) -> None:
    print(f"PROTOCOL_FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        die(f"cannot read JSON {path}: {e}")
    if not isinstance(value, dict):
        die(f"{path} must contain an object")
    return value


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def lease(action: str, issue: int, stage: str, pr: str | None = None) -> None:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        return
    artifacts = os.environ.get("ARTIFACTS_DIR", "").strip()
    if not artifacts:
        return
    lease_file = Path(artifacts) / "factory-lease.json"
    if not lease_file.is_file() and action != "start":
        return
    argv = [
        sys.executable, str(ROOT / "scripts" / "factory_lease.py"), action,
        "--issue", str(issue), "--stage", stage, "--lease-file", str(lease_file),
    ]
    if pr is not None and action != "start":
        argv.extend(["--pr", str(pr)])
    subprocess.check_call(argv, cwd=ROOT)


def validate_dependencies(deps: object) -> list[dict]:
    """A contract may declare the packages the change needs. Each declaration carries the three
    justifications the security guard requires under `## Dependency justification`; the kernel
    renders them into the PR body verbatim, so a thin declaration fails here, not at merge."""
    if not isinstance(deps, list): die("contract dependencies must be a list")
    seen: set[tuple[str, str]] = set()
    for d in deps:
        if not isinstance(d, dict): die("contract dependency must be an object")
        if d.get("ecosystem") not in DEPENDENCY_ECOSYSTEMS: die(f"dependency ecosystem must be one of {DEPENDENCY_ECOSYSTEMS}")
        for k in DEPENDENCY_FIELDS:
            if not isinstance(d.get(k), str) or len(d[k].strip()) < (1 if k == "name" else 10): die(f"dependency {d.get('name')!r} needs a substantive {k}")
        if not PACKAGE_NAME.match(d["name"].strip()): die(f"invalid dependency name {d['name']!r}")
        key = (d["ecosystem"], d["name"].strip().lower())
        if key in seen: die(f"duplicate dependency {d['name']!r}")
        seen.add(key)
    return deps


def validate_contract(c: dict, issue: int | None = None) -> str:
    required = {"version", "issue", "summary", "behaviors", "invariants", "out_of_scope", "risks", "ambiguities"}
    missing = sorted(required - c.keys())
    if missing: die(f"contract missing {missing}")
    if c["version"] != "2.0": die("contract version must be 2.0")
    if not isinstance(c["issue"], dict) or not isinstance(c["issue"].get("number"), int) or not c["issue"].get("title"):
        die("contract issue requires integer number and title")
    if issue is not None and c["issue"]["number"] != issue: die("contract issue number does not match dispatched issue")
    if not isinstance(c["summary"], str) or len(c["summary"].strip()) < 10: die("contract summary is too weak")
    if c["ambiguities"] != []: die("material ambiguities remain; factory must stop")
    behaviors = c["behaviors"]
    if not isinstance(behaviors, list) or not behaviors: die("contract needs at least one observable behavior")
    ids: set[str] = set()
    for b in behaviors:
        if not isinstance(b, dict): die("behavior must be an object")
        if set(("id", "given", "when", "then", "seam")) - b.keys(): die("behavior missing Given/When/Then/seam")
        if not AC.match(str(b["id"])) or b["id"] in ids: die(f"invalid/duplicate behavior id {b.get('id')}")
        ids.add(b["id"])
        if any(not isinstance(b[k], str) or not b[k].strip() for k in ("given", "when", "then", "seam")):
            die(f"behavior {b['id']} has an empty field")
    for key in ("invariants", "out_of_scope", "risks"):
        if not isinstance(c[key], list) or any(not isinstance(x, str) or not x.strip() for x in c[key]): die(f"{key} must be strings")
    validate_dependencies(c.get("dependencies", []))
    return hashlib.sha256(canonical(c)).hexdigest()


def validate_context(m: dict, contract_hash: str) -> dict:
    required = {"version", "contract_sha256", "files", "symbols", "callers", "tests", "invariants", "adrs", "history"}
    if required - m.keys(): die(f"context missing {sorted(required - m.keys())}")
    if m["version"] != "1.0" or m["contract_sha256"] != contract_hash: die("context is not bound to validated contract")
    if not isinstance(m["files"], list) or not m["files"]: die("context must identify relevant files")
    hashes = {}
    for rel in m["files"]:
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts: die(f"unsafe context path {rel!r}")
        p = ROOT / rel
        if not p.is_file(): die(f"context file does not exist: {rel}")
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    for key in ("symbols", "callers", "tests", "invariants", "adrs", "history"):
        if not isinstance(m[key], list): die(f"context {key} must be a list")
    out = dict(m); out["file_sha256"] = hashes
    return out


def run_contract(args: argparse.Namespace) -> None:
    c = load(args.input); h = validate_contract(c, args.issue)
    Path(args.output).write_bytes(canonical(c)); Path(args.hash_output).write_text(h + "\n", encoding="utf-8")
    lease("start", c["issue"]["number"], "contract")
    print(f"CONTRACT_OK sha256={h} criteria={len(c['behaviors'])}")


def run_context(args: argparse.Namespace) -> None:
    c = load(args.contract); h = validate_contract(c)
    artifacts = Path(args.output).parent
    enriched = artifacts / "context.enriched.json"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "factory_impact.py"), "context",
        "--input", args.input, "--output", str(enriched),
    ], cwd=ROOT)
    m = validate_context(load(str(enriched)), h)
    Path(args.output).write_bytes(canonical(m))
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "factory_artifacts.py"), "ticket",
        "--issue", str(c["issue"]["number"]), "--contract", args.contract,
        "--ticket-output", str(artifacts / "ticket.json"),
        "--frontier-output", str(artifacts / "frontier.json"),
    ], cwd=ROOT)
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "factory_artifacts.py"), "design",
        "--input", str(artifacts / "design.raw.json"), "--contract", args.contract,
        "--context", args.output, "--output", str(artifacts / "design.json"),
    ], cwd=ROOT)
    lease("touch", c["issue"]["number"], "design-context")
    print(f"CONTEXT_OK files={len(m['files'])} sha256={hashlib.sha256(canonical(m)).hexdigest()}")


def run_attach(args: argparse.Namespace) -> None:
    c = load(args.contract); h = validate_contract(c)
    body = subprocess.check_output(["gh", "pr", "view", str(args.pr), "--json", "body", "-q", ".body"], text=True)
    body = re.sub(r"\n?<!-- factory-contract:start -->.*?<!-- factory-contract:end -->\n?", "\n", body, flags=re.S).rstrip()
    block = "\n\n<!-- factory-contract:start -->\n```factory-contract\n" + canonical(c).decode().rstrip() + "\n```\ncontract-sha256: " + h + "\n<!-- factory-contract:end -->\n"
    tmp = Path(args.contract).with_suffix(".pr-body.md"); tmp.write_text(body + block, encoding="utf-8")
    subprocess.check_call(["gh", "pr", "edit", str(args.pr), "--body-file", str(tmp)])
    artifacts = os.environ.get("ARTIFACTS_DIR", "").strip() or str(Path(args.contract).resolve().parent)
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "factory_provenance.py"), "publish",
        "--pr", str(args.pr), "--artifacts", artifacts,
    ], cwd=ROOT)
    lease("touch", c["issue"]["number"], "pr-handoff", str(args.pr))
    print(f"CONTRACT_ATTACHED pr={args.pr} sha256={h}")


def main() -> None:
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("contract"); a.add_argument("--input", required=True); a.add_argument("--output", required=True); a.add_argument("--hash-output", required=True); a.add_argument("--issue", type=int); a.set_defaults(fn=run_contract)
    a = sub.add_parser("context"); a.add_argument("--input", required=True); a.add_argument("--contract", required=True); a.add_argument("--output", required=True); a.set_defaults(fn=run_context)
    a = sub.add_parser("attach"); a.add_argument("--contract", required=True); a.add_argument("--pr", required=True); a.set_defaults(fn=run_attach)
    args = p.parse_args(); args.fn(args)

if __name__ == "__main__": main()
