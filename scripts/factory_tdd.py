#!/usr/bin/env python3
"""Deterministic authority split and RED/GREEN proof for one Dark Factory tracer bullet."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

def run(argv:list[str], check:bool=False, timeout:int=600)->subprocess.CompletedProcess[str]:
    p=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip() or f"{argv!r} failed")
    return p

def git(*args:str, check:bool=True)->str:
    return run(["git",*args],check=check).stdout.strip()

def load(path:str)->dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def dump(path:str,obj:dict)->None:
    Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def canonical(obj:object)->str:
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False)

def sha_text(s:str)->str:
    return hashlib.sha256(s.encode()).hexdigest()

def clean()->bool:
    return not git("status","--porcelain").strip()

def test_path(path:str)->bool:
    p=path.replace("\\","/").lower()
    name=p.rsplit("/",1)[-1]
    return ("/tests/" in f"/{p}/" or "/test/" in f"/{p}/" or "/__tests__/" in f"/{p}/"
            or name.startswith("test_") or ".test." in name or ".spec." in name)

def diff_files(a:str,b:str)->list[str]:
    return [x for x in git("diff","--name-only",f"{a}..{b}").splitlines() if x.strip()]

def file_hash(path:str)->str:
    return hashlib.sha256((ROOT/path).read_bytes()).hexdigest()

def execute(command:str)->dict:
    before=git("rev-parse","HEAD")
    if not clean(): raise ValueError("proof command requires a clean worktree")
    p=subprocess.run(command,cwd=ROOT,shell=True,text=True,capture_output=True,timeout=900)
    after=git("rev-parse","HEAD")
    if before!=after: raise ValueError("test command changed HEAD")
    if not clean(): raise ValueError("test command dirtied the worktree")
    return {"returncode":p.returncode,"stdout":(p.stdout or "")[-6000:],
            "stderr":(p.stderr or "")[-6000:]}

def capture(contract_path:str,state_path:str)->None:
    c=load(contract_path)
    state={"schema_version":"1.0","contract_sha256":sha_text(canonical(c)),
           "base_sha":git("rev-parse","HEAD"),"command":c["test_seam"]["command"],
           "expected_red":c["test_seam"]["expected_red"]}
    dump(state_path,state)
    print(f"TDD_BASE_CAPTURED {state['base_sha']}")

def prove_red(contract_path:str,state_path:str,proof_path:str)->None:
    c=load(contract_path); state=load(state_path)
    if sha_text(canonical(c))!=state["contract_sha256"]: raise ValueError("contract changed after capture")
    if not clean(): raise ValueError("test-author must finish with a clean committed worktree")
    test_sha=git("rev-parse","HEAD")
    files=diff_files(state["base_sha"],test_sha)
    if not files: raise ValueError("test author created no committed change")
    bad=[p for p in files if not test_path(p)]
    if bad: raise ValueError(f"test-author changed non-test paths: {bad}")
    result=execute(state["command"])
    output=result["stdout"]+"\n"+result["stderr"]
    if result["returncode"]==0: raise ValueError("RED gate failed: command passed before production change")
    expected=state["expected_red"]
    if expected not in output: raise ValueError("RED gate failed for an unexpected reason/signature")
    hashes={p:file_hash(p) for p in files if (ROOT/p).is_file()}
    state.update({"test_sha":test_sha,"test_files":files,"test_hashes":hashes})
    dump(state_path,state)
    proof={"schema_version":"1.0","phase":"red","contract_sha256":state["contract_sha256"],
           "base_sha":state["base_sha"],"test_sha":test_sha,"test_files":files,
           "test_hashes":hashes,"command":state["command"],"expected_red":expected,
           "returncode":result["returncode"],"head_after":git("rev-parse","HEAD")}
    proof["proof_sha256"]=sha_text(canonical(proof)); dump(proof_path,proof)
    print(f"RED_PROVED sha256={proof['proof_sha256']}")

def prove_green(contract_path:str,state_path:str,proof_path:str)->None:
    c=load(contract_path); state=load(state_path)
    if sha_text(canonical(c))!=state["contract_sha256"]: raise ValueError("contract changed after RED")
    if "test_sha" not in state: raise ValueError("RED proof missing")
    if run(["git","merge-base","--is-ancestor",state["test_sha"],"HEAD"]).returncode:
        raise ValueError("test-only commit is not an ancestor of final HEAD")
    if not clean(): raise ValueError("coder must finish with a clean committed worktree")
    head=git("rev-parse","HEAD")
    changed=diff_files(state["test_sha"],head)
    if not changed: raise ValueError("coder created no production commit")
    bad=[p for p in changed if test_path(p)]
    if bad: raise ValueError(f"coder changed tests after RED: {bad}")
    for p,h in state["test_hashes"].items():
        if not (ROOT/p).is_file() or file_hash(p)!=h: raise ValueError(f"frozen test changed: {p}")
    result=execute(state["command"])
    if result["returncode"]!=0: raise ValueError("GREEN gate failed: exact RED command is not green")
    proof={"schema_version":"1.0","phase":"green","contract_sha256":state["contract_sha256"],
           "base_sha":state["base_sha"],"test_sha":state["test_sha"],"head_sha":head,
           "test_files":state["test_files"],"test_hashes":state["test_hashes"],
           "production_files":changed,"command":state["command"],"returncode":0}
    proof["proof_sha256"]=sha_text(canonical(proof)); dump(proof_path,proof)
    print(f"GREEN_PROVED head={head} sha256={proof['proof_sha256']}")

def main()->int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    for name in ("capture","red","green"):
        p=sub.add_parser(name); p.add_argument("contract"); p.add_argument("state")
        if name!="capture": p.add_argument("proof")
    a=ap.parse_args()
    try:
        if a.cmd=="capture": capture(a.contract,a.state)
        elif a.cmd=="red": prove_red(a.contract,a.state,a.proof)
        else: prove_green(a.contract,a.state,a.proof)
        return 0
    except Exception as e:
        print(f"TDD_PROOF_ERROR: {e}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
