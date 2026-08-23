#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF_BLOCK = re.compile(r"\n?<!-- factory-proof:start -->.*?<!-- factory-proof:end -->\n?", re.S)

def die(msg): print(f"PROOF_FAIL: {msg}", file=sys.stderr); raise SystemExit(1)
def run(argv, cwd):
    p=subprocess.run(argv,cwd=ROOT/cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300)
    return p.returncode,(p.stdout or '')+(p.stderr or '')
def sha(p): return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n'
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def load(p):
    try: v=json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception as e: die(f"cannot read {p}: {e}")
    return v

def heartbeat(action, stage, pr=None):
    artifacts=os.environ.get('ARTIFACTS_DIR','').strip()
    if not artifacts: return
    contract=Path(artifacts)/'task-contract.json'; lease_file=Path(artifacts)/'factory-lease.json'
    if not contract.is_file() or not lease_file.is_file(): die('factory lease artifacts missing')
    issue=load(str(contract)).get('issue',{}).get('number')
    if not isinstance(issue,int): die('factory contract lacks issue number for lease')
    argv=[sys.executable,str(ROOT/'scripts'/'factory_lease.py'),action,
          '--issue',str(issue),'--stage',stage,'--lease-file',str(lease_file)]
    if pr is not None: argv.extend(['--pr',str(pr)])
    subprocess.check_call(argv,cwd=ROOT)

def spec(path):
    s=load(path)
    if not isinstance(s,dict) or set(('cwd','argv','files','expected_failure'))-s.keys(): die('test spec missing fields')
    if not isinstance(s['argv'],list) or not s['argv'] or any(not isinstance(x,str) or not x for x in s['argv']): die('argv must be non-empty strings')
    if not isinstance(s['files'],list) or not s['files']: die('files must be non-empty')
    if not isinstance(s['expected_failure'],str) or len(s['expected_failure'])<3: die('expected_failure too weak')
    cwd=ROOT/s['cwd']
    if not cwd.is_dir() or ROOT not in cwd.resolve().parents and cwd.resolve()!=ROOT: die('unsafe cwd')
    for f in s['files']:
        p=Path(f)
        if p.is_absolute() or '..' in p.parts or not (ROOT/p).is_file(): die(f'unsafe/missing test file {f}')
        low=f.lower()
        if not ('test' in low or '/tests/' in '/'+low or low.endswith('conftest.py')): die(f'acceptance file is not test-oriented: {f}')
    return s

def clean():
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(): die('worktree must be clean')
def changed(parent='HEAD^',head='HEAD'):
    out=subprocess.check_output(['git','diff','--name-only',parent,head],cwd=ROOT,text=True)
    return sorted(x for x in out.splitlines() if x)
def write(path,obj): Path(path).write_text(canonical(obj),encoding='utf-8')

def impact_check(output):
    artifacts=os.environ.get('ARTIFACTS_DIR','').strip()
    if not artifacts: return None
    context=Path(artifacts)/'context.json'
    if not context.is_file(): die('factory context missing before GREEN impact check')
    target=Path(output).with_suffix('.impact.json')
    argv=[sys.executable,str(ROOT/'scripts'/'factory_impact.py'),'diff','--context',str(context),'--output',str(target)]
    base=os.environ.get('FACTORY_BASE_REF','').strip()
    if base: argv.extend(['--base-ref',base])
    p=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True)
    text=(p.stdout or '')+(p.stderr or '')
    if p.returncode: die('impact gate failed: '+text[-1200:])
    if p.stdout.strip(): print(p.stdout.strip())
    value=load(str(target))
    return {'sha256':digest(value),'risk':value.get('risk'),'artifact':str(target)}

def red(a):
    clean(); s=spec(a.spec); actual=changed(); declared=sorted(s['files'])
    if actual!=declared: die(f'test checkpoint changed {actual}; declared test files are {declared}')
    before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    rc,out=run(s['argv'],s['cwd'])
    after=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(); clean()
    if before!=after: die('test command moved HEAD')
    if rc==0: die('RED command unexpectedly passed')
    if s['expected_failure'].lower() not in out.lower(): die('RED failed, but not for the declared behavioral reason')
    proof={'version':'1.0','test_commit':before,'cwd':s['cwd'],'argv':s['argv'],'files':{f:sha(f) for f in declared},'red_exit':rc,'red_output_sha256':hashlib.sha256(out.encode()).hexdigest(),'expected_failure':s['expected_failure']}
    write(a.output,proof); heartbeat('touch','red'); print(f"RED_PROVED tests={len(declared)} commit={before}")

def green(a):
    clean(); p=load(a.proof)
    for f,h in p.get('files',{}).items():
        if not (ROOT/f).is_file() or sha(f)!=h: die(f'immutable acceptance test changed: {f}')
    before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    rc,out=run(p['argv'],p['cwd'])
    after=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(); clean()
    if before!=after: die('GREEN command moved HEAD')
    if rc!=0: die('GREEN command failed: '+out[-1200:])
    impact=impact_check(a.output)
    result=dict(p,green_commit=before,green_exit=rc,green_output_sha256=hashlib.sha256(out.encode()).hexdigest())
    if impact: result['change_impact']=impact
    write(a.output,result)
    stage='final-green' if 'final' in Path(a.output).name else 'green'
    heartbeat('touch',stage); print(f"GREEN_PROVED tests={len(p['files'])} commit={before}")

def attach(a):
    clean(); p=load(a.proof)
    if p.get('green_exit')!=0 or not p.get('green_commit'): die('only a final GREEN proof can be attached')
    raw=subprocess.check_output(['gh','pr','view',str(a.pr),'--json','body,headRefOid'],cwd=ROOT,text=True)
    info=json.loads(raw); head=info['headRefOid']
    local=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if p['green_commit']!=head or local!=head: die('final GREEN proof is not bound to current PR head')
    block=(f"\n<!-- factory-proof:start -->\n```factory-proof\n{canonical(p).strip()}\n```\n"
           f"proof-sha256: {digest(p)}\n<!-- factory-proof:end -->\n")
    body=PROOF_BLOCK.sub('\n',info.get('body') or '').rstrip()+block
    q=subprocess.run(['gh','pr','edit',str(a.pr),'--body',body],cwd=ROOT,text=True,capture_output=True)
    if q.returncode: die('could not attach proof: '+q.stderr[-1000:])
    heartbeat('finish','proof-attached',a.pr)
    print(f"PROOF_ATTACHED pr={a.pr} head={head} sha256={digest(p)}")

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('red'); x.add_argument('--spec',required=True); x.add_argument('--output',required=True); x.set_defaults(fn=red)
    x=sub.add_parser('green'); x.add_argument('--proof',required=True); x.add_argument('--output',required=True); x.set_defaults(fn=green)
    x=sub.add_parser('attach'); x.add_argument('--proof',required=True); x.add_argument('--pr',required=True); x.set_defaults(fn=attach)
    a=p.parse_args(); a.fn(a)
if __name__=='__main__': main()
