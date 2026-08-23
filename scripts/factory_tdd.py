#!/usr/bin/env python3
"""Deterministic test-author/coder authority split with RED/GREEN proof."""
from __future__ import annotations
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parent.parent

def sh(a,check=False,t=900):
 p=subprocess.run(a,cwd=R,text=True,capture_output=True,timeout=t)
 if check and p.returncode: raise RuntimeError((p.stderr or p.stdout).strip())
 return p
def git(*a): return sh(["git",*a],True).stdout.strip()
def load(p): return json.loads(Path(p).read_text())
def dump(p,x): Path(p).write_text(json.dumps(x,sort_keys=True,indent=2)+"\n")
def canon(x): return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def digest(x): return hashlib.sha256(x if isinstance(x,bytes) else x.encode()).hexdigest()
def clean(): return not git("status","--porcelain")
def istest(x):
 p=x.replace("\\","/").lower(); n=p.rsplit("/",1)[-1]
 return any(z in f"/{p}/" for z in ("/tests/","/test/","/__tests__/")) or n.startswith("test_") or ".test." in n or ".spec." in n
def diff(a,b): return [x for x in git("diff","--name-only",f"{a}..{b}").splitlines() if x]
def fh(p): return digest((R/p).read_bytes())
def execute(cmd):
 h=git("rev-parse","HEAD")
 if not clean(): raise ValueError("proof command requires clean worktree")
 p=subprocess.run(cmd,cwd=R,shell=True,text=True,capture_output=True,timeout=900)
 if git("rev-parse","HEAD")!=h or not clean(): raise ValueError("proof command mutated repository")
 return p
def capture(cpath,state):
 c=load(cpath); x={"schema_version":"1.0","contract_sha256":digest(canon(c)),"base_sha":git("rev-parse","HEAD"),
 "command":c["test_seam"]["command"],"expected_red":c["test_seam"]["expected_red"]}
 dump(state,x); print("TDD_BASE_CAPTURED",x["base_sha"])
def red(cpath,state,out):
 c,x=load(cpath),load(state)
 if digest(canon(c))!=x["contract_sha256"]: raise ValueError("contract changed")
 if not clean(): raise ValueError("test-author left dirty worktree")
 ts=git("rev-parse","HEAD"); fs=diff(x["base_sha"],ts)
 if not fs or (bad:=[p for p in fs if not istest(p)]): raise ValueError(f"test-only authority violated: {bad if fs else 'no change'}")
 r=execute(x["command"]); text=(r.stdout or "")+"\n"+(r.stderr or "")
 if r.returncode==0 or x["expected_red"] not in text: raise ValueError("RED not proven for expected reason")
 hs={p:fh(p) for p in fs if (R/p).is_file()}; x.update(test_sha=ts,test_files=fs,test_hashes=hs); dump(state,x)
 proof={"phase":"red","contract_sha256":x["contract_sha256"],"base_sha":x["base_sha"],"test_sha":ts,
 "test_files":fs,"test_hashes":hs,"command":x["command"],"expected_red":x["expected_red"],"returncode":r.returncode}
 proof["proof_sha256"]=digest(canon(proof)); dump(out,proof); print("RED_PROVED",proof["proof_sha256"])
def green(cpath,state,out):
 c,x=load(cpath),load(state)
 if digest(canon(c))!=x["contract_sha256"] or "test_sha" not in x: raise ValueError("RED chain invalid")
 if sh(["git","merge-base","--is-ancestor",x["test_sha"],"HEAD"]).returncode or not clean(): raise ValueError("test commit ancestry/worktree invalid")
 h=git("rev-parse","HEAD"); fs=diff(x["test_sha"],h)
 if not fs or (bad:=[p for p in fs if istest(p)]): raise ValueError(f"coder authority violated: {bad if fs else 'no production change'}")
 if any(not (R/p).is_file() or fh(p)!=v for p,v in x["test_hashes"].items()): raise ValueError("frozen test hash changed")
 r=execute(x["command"])
 if r.returncode: raise ValueError("GREEN not proven by exact RED command")
 proof={"phase":"green","contract_sha256":x["contract_sha256"],"base_sha":x["base_sha"],"test_sha":x["test_sha"],
 "head_sha":h,"test_files":x["test_files"],"test_hashes":x["test_hashes"],"production_files":fs,"command":x["command"],"returncode":0}
 proof["proof_sha256"]=digest(canon(proof)); dump(out,proof); print("GREEN_PROVED",proof["proof_sha256"])
def main():
 a=argparse.ArgumentParser(); s=a.add_subparsers(dest="cmd",required=True)
 for n in ("capture","red","green"):
  q=s.add_parser(n); q.add_argument("contract"); q.add_argument("state")
  if n!="capture": q.add_argument("proof")
 z=a.parse_args()
 try:
  {"capture":lambda:capture(z.contract,z.state),"red":lambda:red(z.contract,z.state,z.proof),"green":lambda:green(z.contract,z.state,z.proof)}[z.cmd](); return 0
 except Exception as e: print("TDD_PROOF_ERROR:",e,file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
