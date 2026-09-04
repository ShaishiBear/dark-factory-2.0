#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF_BLOCK = re.compile(r"\n?<!-- factory-proof:start -->.*?<!-- factory-proof:end -->\n?", re.S)
DESIGN_BLOCK = re.compile(r"\n?<!-- factory-design:start -->.*?<!-- factory-design:end -->\n?", re.S)

def die(msg): print(f"PROOF_FAIL: {msg}", file=sys.stderr); raise SystemExit(1)
def run(argv, cwd):
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    p=subprocess.run(argv,cwd=ROOT/cwd,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300)
    return p.returncode,(p.stdout or '')+(p.stderr or '')
def sha(p): return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n'
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def load(p):
    try: v=json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception as e: die(f"cannot read {p}: {e}")
    return v
def heartbeat(action, stage, pr=None):
    if not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN'): return
    artifacts=os.environ.get('ARTIFACTS_DIR','').strip()
    if not artifacts: return
    contract=Path(artifacts)/'task-contract.json'; lease_file=Path(artifacts)/'factory-lease.json'
    if not contract.is_file() or not lease_file.is_file(): return
    if not contract.is_file() or not lease_file.is_file(): die('factory lease artifacts missing')
    issue=load(str(contract)).get('issue',{}).get('number')
    if not isinstance(issue,int): die('factory contract lacks issue number for lease')
    argv=[sys.executable,str(ROOT/'scripts'/'factory_lease.py'),action,
          '--issue',str(issue),'--stage',stage,'--lease-file',str(lease_file)]
    if pr is not None: argv.extend(['--pr',str(pr)])
    subprocess.check_call(argv,cwd=ROOT)
def ensure_design(base):
    design_path=base/'design.json'
    if design_path.is_file(): return
    raw=base/'design.raw.json'; contract=base/'task-contract.json'; context=base/'context.json'
    if not raw.is_file() or not contract.is_file() or not context.is_file():
        die('validated contract/context and raw design are required before RED')
    p=subprocess.run([
        sys.executable,str(ROOT/'scripts'/'factory_artifacts.py'),'design',
        '--input',str(raw),'--contract',str(contract),'--context',str(context),
        '--output',str(design_path),
    ],cwd=ROOT,text=True,capture_output=True,timeout=120)
    text=(p.stdout or '')+(p.stderr or '')
    if p.returncode: die('design compiler failed: '+text[-1200:])
    if p.stdout.strip(): print(p.stdout.strip())
def artifacts():
    root=os.environ.get('ARTIFACTS_DIR','').strip()
    if not root: die('ARTIFACTS_DIR is required for acceptance proof')
    base=Path(root); ensure_design(base)
    contract_path=base/'task-contract.json'; design_path=base/'design.json'
    if not contract_path.is_file() or not design_path.is_file(): die('validated contract/design artifacts are required before RED')
    contract=load(str(contract_path)); design=load(str(design_path))
    ids=[b.get('id') for b in contract.get('behaviors',[]) if isinstance(b,dict)]
    mapping=design.get('ac_mapping')
    if not ids or not isinstance(mapping,dict) or set(mapping)!=set(ids): die('design/contract AC mapping is invalid')
    planned=design.get('planned_files'); allowed_new=design.get('allowed_new_files')
    if not isinstance(planned,list) or not planned or not isinstance(allowed_new,list):
        die('compiled design lacks implementation file envelope')
    return contract,design,ids
def checkpoint(value):
    required={'acceptance_id','cwd','argv','files','expected_failure'}
    if not isinstance(value,dict) or required-value.keys(): die('test checkpoint missing fields')
    ac=value['acceptance_id']
    if not isinstance(ac,str) or not re.fullmatch(r'AC-[1-9][0-9]*',ac): die('invalid acceptance_id')
    if not isinstance(value['argv'],list) or not value['argv'] or any(not isinstance(x,str) or not x for x in value['argv']): die(f'{ac} argv must be non-empty strings')
    if not isinstance(value['files'],list) or not value['files'] or any(not isinstance(x,str) or not x for x in value['files']): die(f'{ac} files must be non-empty strings')
    if not isinstance(value['expected_failure'],str) or len(value['expected_failure'].strip())<3: die(f'{ac} expected_failure too weak')
    cwd=ROOT/value['cwd']
    if not cwd.is_dir() or ROOT not in cwd.resolve().parents and cwd.resolve()!=ROOT: die(f'{ac} unsafe cwd')
    for f in value['files']:
        p=Path(f)
        if p.is_absolute() or '..' in p.parts or not (ROOT/p).is_file(): die(f'{ac} unsafe/missing test file {f}')
        low=f.lower()
        if not ('test' in low or '/tests/' in '/'+low or low.endswith('conftest.py')): die(f'{ac} acceptance file is not test-oriented: {f}')
    return dict(value)
def spec(path):
    s=load(path)
    if not isinstance(s,dict) or s.get('version')!='2.0' or not isinstance(s.get('checkpoints'),list) or not s['checkpoints']:
        die('test spec must be version 2.0 with checkpoints')
    contract,design,ids=artifacts()
    cps=[checkpoint(x) for x in s['checkpoints']]
    actual=[x['acceptance_id'] for x in cps]
    if len(actual)!=len(set(actual)) or set(actual)!=set(ids): die('test checkpoints must cover every contract AC exactly once')
    for cp in cps:
        seams=design['ac_mapping'].get(cp['acceptance_id'])
        if not isinstance(seams,list) or not seams: die(f"{cp['acceptance_id']} has no compiled design seam")
        cp['seams']=seams
    return {'version':'2.0','contract_sha256':digest(contract),'design_sha256':digest(design),'checkpoints':cps}
def clean():
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(): die('worktree must be clean')
def changed(parent='HEAD^',head='HEAD'):
    out=subprocess.check_output(['git','diff','--name-only',parent,head],cwd=ROOT,text=True)
    return sorted(x for x in out.splitlines() if x)
def write(path,obj): Path(path).write_text(canonical(obj),encoding='utf-8')

def impact_check(output):
    root=os.environ.get('ARTIFACTS_DIR','').strip()
    if not root: return None
    context=Path(root)/'context.json'
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

def architecture_guard_check(output):
    root=os.environ.get('ARTIFACTS_DIR','').strip()
    if not root: die('ARTIFACTS_DIR is required for architecture guard')
    design=Path(root)/'design.json'
    if not design.is_file(): die('compiled design missing before architecture guard')
    target=Path(output).with_suffix('.architecture.json')
    argv=[
        sys.executable,str(ROOT/'scripts'/'factory_architecture_guard.py'),
        '--policy','.factory/architecture.json','--design',str(design),
        '--head-ref','HEAD','--output',str(target),
    ]
    base=os.environ.get('FACTORY_BASE_REF','').strip()
    if base: argv.extend(['--base-ref',base])
    p=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,timeout=300)
    text=(p.stdout or '')+(p.stderr or '')
    if p.returncode: die('architecture guard failed: '+text[-2000:])
    if p.stdout.strip(): print(p.stdout.strip())
    value=load(str(target))
    return {
        'sha256':digest(value),
        'artifact':str(target),
        'new_forbidden_edges':len(value.get('forbidden_edges',{}).get('new',[])),
        'new_cycles':len(value.get('cycles',{}).get('new',[])),
        'no_growth_regressions':len(value.get('no_growth_regressions',[])),
        'unplanned_product_files':len(value.get('unplanned_product_files',[])),
    }

def bind_architecture(result, head):
    root=os.environ.get('ARTIFACTS_DIR','').strip()
    if not root: die('ARTIFACTS_DIR is required for final architecture proof')
    path=Path(root)/'architecture-conformance.json'
    if not path.is_file(): die('final GREEN requires compiled architecture conformance')
    arch=load(str(path))
    if not isinstance(arch,dict) or arch.get('version')!='1.0' or arch.get('verdict')!='conform':
        die('architecture conformance artifact is invalid')
    if arch.get('head_sha')!=head:
        die('architecture conformance is not bound to current GREEN head')
    if arch.get('contract_sha256')!=result.get('contract_sha256') or arch.get('design_sha256')!=result.get('design_sha256'):
        die('architecture conformance is not bound to proof contract/design')
    if not isinstance(arch.get('governor_sha256'),str) or len(arch['governor_sha256'])!=64:
        die('architecture conformance lacks governor hash')
    if not isinstance(arch.get('diff_sha256'),str) or len(arch['diff_sha256'])!=64:
        die('architecture conformance lacks diff hash')
    if not isinstance(arch.get('changed_files'),list) or not arch['changed_files']:
        die('architecture conformance lacks changed files')
    result['architecture_builder']=arch
    result['architecture_builder_sha256']=digest(arch)
    return result

def plan_from(proof_or_spec,test_commit):
    cps=[]
    for cp in proof_or_spec['checkpoints']:
        cps.append({k:cp[k] for k in ('acceptance_id','seams','cwd','argv','files','expected_failure')})
    return {'version':'1.0','contract_sha256':proof_or_spec['contract_sha256'],
            'design_sha256':proof_or_spec['design_sha256'],'test_commit':test_commit,'checkpoints':cps}
def red(a):
    clean(); s=spec(a.spec)
    declared=sorted({f for cp in s['checkpoints'] for f in cp['files']}); actual=changed()
    if actual!=declared: die(f'test checkpoint changed {actual}; declared test files are {declared}')
    before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    results=[]
    for cp in s['checkpoints']:
        rc,out=run(cp['argv'],cp['cwd'])
        if rc==0: die(f"{cp['acceptance_id']} RED command unexpectedly passed")
        if cp['expected_failure'].lower() not in out.lower(): die(f"{cp['acceptance_id']} RED failed for the wrong reason")
        results.append(dict(cp,red_exit=rc,red_output_sha256=hashlib.sha256(out.encode()).hexdigest()))
    after=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(); clean()
    if before!=after: die('RED commands moved HEAD')
    files={f:sha(f) for f in declared}
    base={'version':'2.0','test_commit':before,'contract_sha256':s['contract_sha256'],
          'design_sha256':s['design_sha256'],'files':files,'checkpoints':results}
    plan=plan_from(base,before)
    root=Path(os.environ['ARTIFACTS_DIR']); write(root/'test-plan.json',plan)
    proof=dict(base,test_plan_sha256=digest(plan))
    write(a.output,proof); heartbeat('touch','red')
    print(f"RED_PROVED criteria={len(results)} tests={len(files)} commit={before}")
def green(a):
    clean(); p=load(a.proof)
    if p.get('version')!='2.0' or not isinstance(p.get('checkpoints'),list) or not p['checkpoints']: die('GREEN requires v2 RED proof')
    for f,h in p.get('files',{}).items():
        if not (ROOT/f).is_file() or sha(f)!=h: die(f'immutable acceptance test changed: {f}')
    before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    green_results=[]
    for cp in p['checkpoints']:
        rc,out=run(cp['argv'],cp['cwd'])
        if rc!=0: die(f"{cp['acceptance_id']} GREEN command failed: "+out[-1200:])
        green_results.append({'acceptance_id':cp['acceptance_id'],'exit':rc,
                              'output_sha256':hashlib.sha256(out.encode()).hexdigest()})
    after=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(); clean()
    if before!=after: die('GREEN commands moved HEAD')
    impact=impact_check(a.output)
    architecture_guard=architecture_guard_check(a.output)
    result=dict(p,green_commit=before,green_results=green_results,architecture_guard=architecture_guard)
    if impact: result['change_impact']=impact
    stage='final-green' if 'final' in Path(a.output).name else 'green'
    if stage=='final-green': result=bind_architecture(result,before)
    write(a.output,result)
    heartbeat('touch',stage)
    print(f"GREEN_PROVED criteria={len(green_results)} tests={len(p['files'])} commit={before}")
def attach(a):
    clean(); p=load(a.proof)
    results=p.get('green_results')
    if p.get('version')!='2.0' or not p.get('green_commit') or not isinstance(results,list) or not results or any(x.get('exit')!=0 for x in results):
        die('only a final v2 GREEN proof can be attached')
    raw=subprocess.check_output(['gh','pr','view',str(a.pr),'--json','body,headRefOid'],cwd=ROOT,text=True)
    info=json.loads(raw); head=info['headRefOid']
    local=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if p['green_commit']!=head or local!=head: die('final GREEN proof is not bound to current PR head')
    if not isinstance(p.get('architecture_builder'),dict) or p.get('architecture_builder_sha256')!=digest(p['architecture_builder']):
        die('final GREEN proof lacks valid architecture provenance')
    guard=p.get('architecture_guard')
    if not isinstance(guard,dict) or not isinstance(guard.get('sha256'),str) or len(guard['sha256'])!=64:
        die('final GREEN proof lacks deterministic architecture guard provenance')
    root=os.environ.get('ARTIFACTS_DIR','').strip(); design_path=Path(root)/'design.json'
    if not design_path.is_file(): die('compiled design missing while attaching proof')
    design=load(str(design_path))
    if digest(design)!=p.get('design_sha256'): die('attached design does not match final proof design hash')
    design_block=(f"\n<!-- factory-design:start -->\n```factory-design\n{canonical(design).strip()}\n```\n"
                  f"design-sha256: {digest(design)}\n<!-- factory-design:end -->\n")
    proof_block=(f"\n<!-- factory-proof:start -->\n```factory-proof\n{canonical(p).strip()}\n```\n"
                 f"proof-sha256: {digest(p)}\n<!-- factory-proof:end -->\n")
    body=DESIGN_BLOCK.sub('\n',PROOF_BLOCK.sub('\n',info.get('body') or '')).rstrip()+design_block+proof_block
    q=subprocess.run(['gh','pr','edit',str(a.pr),'--body',body],cwd=ROOT,text=True,capture_output=True)
    if q.returncode: die('could not attach proof/design: '+q.stderr[-1000:])
    heartbeat('finish','proof-attached',a.pr)
    print(f"PROOF_ATTACHED pr={a.pr} head={head} sha256={digest(p)} design_sha256={digest(design)}")
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('red'); x.add_argument('--spec',required=True); x.add_argument('--output',required=True); x.set_defaults(fn=red)
    x=sub.add_parser('green'); x.add_argument('--proof',required=True); x.add_argument('--output',required=True); x.set_defaults(fn=green)
    x=sub.add_parser('attach'); x.add_argument('--proof',required=True); x.add_argument('--pr',required=True); x.set_defaults(fn=attach)
    a=p.parse_args(); a.fn(a)
if __name__=='__main__': main()
