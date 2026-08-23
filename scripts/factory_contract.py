#!/usr/bin/env python3
"""Deterministic contract, context, and dependency-frontier gates for Dark Factory."""
from __future__ import annotations
import argparse, ast, hashlib, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCK_RE = re.compile(r"(?im)^\s*Blocked by:\s*(.+)$")
REF_RE = re.compile(r"#(\d+)")

def run(*argv: str, check: bool = True) -> str:
    p = subprocess.run(list(argv), cwd=ROOT, text=True, capture_output=True)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip() or f"{argv!r} failed")
    return p.stdout.strip()

def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def validate_contract(c: dict) -> None:
    required={"schema_version","issue","issue_type","problem","acceptance_criteria","test_seam",
              "ambiguities","invariants","out_of_scope","likely_paths","symbols","risk"}
    missing=required-c.keys()
    if missing: raise ValueError(f"missing fields: {sorted(missing)}")
    if c["schema_version"]!="1.0": raise ValueError("schema_version must be 1.0")
    if not isinstance(c["issue"],int) or c["issue"]<1: raise ValueError("issue must be positive int")
    if c["issue_type"] not in {"bug","feature","enhancement","refactor","chore","documentation"}:
        raise ValueError("invalid issue_type")
    if len(str(c["problem"]).strip())<12: raise ValueError("problem too short")
    if c["ambiguities"]!=[]: raise ValueError("ambiguities must be empty before build")
    ac=c["acceptance_criteria"]
    if not isinstance(ac,list) or not ac: raise ValueError("acceptance_criteria required")
    ids=[x.get("id") for x in ac]
    if len(ids)!=len(set(ids)) or any(not re.fullmatch(r"AC-[1-9]\d*",str(x)) for x in ids):
        raise ValueError("acceptance ids must be unique AC-N")
    if any(len(str(x.get("behavior","")).strip())<8 for x in ac):
        raise ValueError("acceptance behavior too short")
    seam=c["test_seam"]
    for k in ("kind","path_hint","command","expected_red"):
        if not str(seam.get(k,"")).strip(): raise ValueError(f"test_seam.{k} required")
    if seam["kind"] not in {"unit","integration","e2e","static","docs"}: raise ValueError("invalid seam kind")
    paths=c["likely_paths"]
    if not isinstance(paths,list) or not paths or len(paths)>20: raise ValueError("1..20 likely_paths required")
    if c["risk"] not in {"low","medium","high"}: raise ValueError("invalid risk")

def defs_and_imports(path: Path) -> tuple[list[str],list[str]]:
    if path.suffix==".py":
        try: tree=ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError,UnicodeDecodeError): return [],[]
        defs=[n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
        imports=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Import): imports += [a.name for a in n.names]
            elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
        return sorted(set(defs)),sorted(set(imports))
    if path.suffix in {".ts",".tsx",".js",".jsx"}:
        try: body=path.read_text(encoding="utf-8")
        except UnicodeDecodeError: return [],[]
        defs=re.findall(r"\b(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)",body)
        imports=re.findall(r"\bfrom\s+['\"]([^'\"]+)['\"]",body)
        return sorted(set(defs)),sorted(set(imports))
    return [],[]

def context_manifest(contract: dict) -> dict:
    validate_contract(contract)
    wanted=set(contract["symbols"]); candidates:set[Path]=set()
    for raw in contract["likely_paths"]:
        p=ROOT/raw
        if p.is_file(): candidates.add(p)
        elif p.is_dir():
            candidates.update(x for x in p.rglob("*") if x.is_file() and x.suffix in {".py",".ts",".tsx",".js",".jsx",".md"})
    if wanted:
        for base in (ROOT/"app",ROOT/"harness"):
            if not base.exists(): continue
            for p in base.rglob("*"):
                if p.is_file() and p.suffix in {".py",".ts",".tsx",".js",".jsx"}:
                    try: txt=p.read_text(encoding="utf-8")
                    except UnicodeDecodeError: continue
                    if any(s in txt for s in wanted): candidates.add(p)
    files=[]
    for p in sorted(candidates)[:80]:
        rel=p.relative_to(ROOT).as_posix(); defs,imports=defs_and_imports(p)
        hist=run("git","log","-3","--format=%h %s","--",rel,check=False).splitlines()
        files.append({"path":rel,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
                      "definitions":defs[:40],"imports":imports[:40],"history":hist[:3]})
    doc_paths=[ROOT/"CONTEXT.md"]; adr=ROOT/"docs"/"adr"
    if adr.exists(): doc_paths += sorted(adr.glob("*.md"))
    docs=[{"path":p.relative_to(ROOT).as_posix(),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in doc_paths if p.is_file()]
    result={"schema_version":"1.0","issue":contract["issue"],
            "contract_sha256":sha_text(canonical(contract)),"base_sha":run("git","rev-parse","HEAD"),
            "files":files,"docs":docs,"symbols_requested":contract["symbols"],
            "invariants":contract["invariants"]}
    result["manifest_sha256"]=sha_text(canonical(result)); return result

def blocker_numbers(body: str) -> list[int]:
    out=[]
    for m in BLOCK_RE.finditer(body or ""): out += [int(x) for x in REF_RE.findall(m.group(1))]
    return sorted(set(out))

def issue_json(n: int) -> dict:
    return json.loads(run("gh","issue","view",str(n),"--json","number,body,state,labels"))

def open_blockers(n: int) -> list[int]:
    issue=issue_json(n); opens=[]
    for b in blocker_numbers(issue.get("body","")):
        state=json.loads(run("gh","issue","view",str(b),"--json","state")).get("state")
        if str(state).upper()!="CLOSED": opens.append(b)
    return opens

def reconcile_frontier() -> int:
    run("gh","label","create","factory:blocked","--color","6f42c1",
        "--description","Waiting for declared issue dependencies","--force",check=False)
    issues=json.loads(run("gh","issue","list","--state","open","--limit","200","--json","number,body,labels"))
    changed=0
    for issue in issues:
        if not blocker_numbers(issue.get("body","")): continue
        blocked=bool(open_blockers(int(issue["number"])))
        labels={x["name"] for x in issue.get("labels",[])}; has="factory:blocked" in labels
        if blocked and not has:
            run("gh","issue","edit",str(issue["number"]),"--add-label","factory:blocked"); changed+=1
        elif not blocked and has:
            run("gh","issue","edit",str(issue["number"]),"--remove-label","factory:blocked"); changed+=1
    print(f"FRONTIER_RECONCILED changed={changed}"); return 0

def main() -> int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    v=sub.add_parser("validate"); v.add_argument("contract")
    c=sub.add_parser("context"); c.add_argument("contract"); c.add_argument("out")
    a=sub.add_parser("assert-ready"); a.add_argument("issue",type=int)
    sub.add_parser("reconcile"); args=ap.parse_args()
    try:
        if args.cmd=="validate":
            obj=load(args.contract); validate_contract(obj); print(f"CONTRACT_OK sha256={sha_text(canonical(obj))}")
        elif args.cmd=="context":
            obj=context_manifest(load(args.contract)); Path(args.out).write_text(json.dumps(obj,indent=2)+"\n",encoding="utf-8")
            print(f"CONTEXT_OK files={len(obj['files'])} sha256={obj['manifest_sha256']}")
        elif args.cmd=="assert-ready":
            opens=open_blockers(args.issue)
            if opens:
                run("gh","issue","edit",str(args.issue),"--add-label","factory:blocked",
                    "--remove-label","factory:accepted","--remove-label","factory:in-progress",check=False)
                raise ValueError("open blockers: "+", ".join(f"#{x}" for x in opens))
            print("FRONTIER_READY")
        elif args.cmd=="reconcile": return reconcile_frontier()
        return 0
    except Exception as e:
        print(f"FACTORY_CONTRACT_ERROR: {e}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
