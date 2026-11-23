"""One real revision using original RevPropBench prompts, patch application and scorer.

Only the inference transport is adapted to the existing authorized Codex Relay.
The upstream repository is not modified. Dataset sampling/annotation is not rerun.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT/'三方依赖代码/llm-revision-propagation'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(REPO))

from benchmark.jsonptr import apply_patch
from benchmark.strict_score import score_strict
from methods.subagent_revision import SubHistJsonMethod
from alignment_probe.codex_transport import call_codex,configured_route


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sample_path=REPO/'benchmark/data/annotated/invoice_L10_00.json'
    sample=json.loads(sample_path.read_text())
    source_sha=sha(sample_path)
    output=Path(__file__).with_name('results')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True,exist_ok=False)
    route=configured_route()
    save(output/'manifest.json',{'repository':str(REPO),'commit':subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        'sample':str(sample_path),'sample_sha256':source_sha,'method':'sub_hist_json','route':route,
        'actual_upstream_functions':['SubHistJsonMethod.prepare','benchmark.jsonptr.apply_patch','benchmark.strict_score.score_strict'],
        'scope':'One existing annotated case, real model revision; no dataset regeneration or full paper reproduction.',
        'transport':'Existing authorized Codex Relay; original LiteLLM network client is not used.'})
    current={};replay=[]
    for message in sample['conversation']:
        if message['role']=='assistant':
            patch=json.loads(message['content'])
            current=apply_patch(current,patch)
            replay.append({'step':len(replay)+1,'add_patch':patch,'result':deepcopy(current)})
    if current!=sample['current_json']:raise AssertionError('History replay differs from initial artifact')
    save(output/'history_replay.json',replay)
    save(output/'before.json',current)
    # Strict input allowlist. Gold, annotation notes and cascade-pattern guidance are never passed.
    public={k:sample[k] for k in ['conversation','current_json','edit_instruction','master_data']}
    prepared=asyncio.run(SubHistJsonMethod().prepare(public,model=None,temperature=0.0,retries=0,seed=0))
    request={'messages':prepared.messages}
    save(output/'model_request.json',request)
    print('CASE',sample['id'],'history messages',len(sample['conversation']),flush=True)
    print('EDIT',sample['edit_instruction'],flush=True)
    print('REAL_MODEL_STARTED',route['model'],route['effort'],flush=True)
    started=time.monotonic()
    text,info=call_codex(request,route['model'],route['effort'] or 'high',output/'transport')
    (output/'model_response.txt').write_text(text)
    save(output/'model_call.json',dict(info,wall_seconds=time.monotonic()-started))
    stripped=text.strip()
    if stripped.startswith('```') and stripped.endswith('```'):stripped=stripped[stripped.find('\n')+1:-3].strip()
    patch=json.loads(stripped)
    if not isinstance(patch,list):raise ValueError('Model did not return a JSON Patch array')
    save(output/'model_patch.json',patch)
    after=apply_patch(current,patch)
    save(output/'after.json',after)
    gold=sample['expected_patch']
    score=score_strict(current,gold,patch,sample.get('value_constraints',[]),collect_detail=True)
    save(output/'score.json',score)
    direct=[op for op in gold if op.get('kind')=='direct']
    extra=deepcopy(gold)+[{'op':'replace','path':'/dueDate','value':'2026-04-15'}]
    controls={
        'reference_patch':score_strict(current,gold,gold),
        'only_direct_price_change':score_strict(current,gold,direct),
        'correct_plus_unrelated_date_edit':score_strict(current,gold,extra)}
    save(output/'controls.json',controls)
    save(output/'private_gold.json',{'expected_patch':gold,'expected_json':sample['expected_json']})
    save(output/'summary.json',{'model_correct':score['apply_ok'] and score['value_recall']==1 and score['over_edit']==0,
        'score':{k:v for k,v in score.items() if k!='detail'},'upstream_sample_unchanged':sha(sample_path)==source_sha,
        'latency_seconds':time.monotonic()-started,'output':str(output)})
    print('MODEL_PATCH',json.dumps(patch,ensure_ascii=False),flush=True)
    print('SCORE',json.dumps({k:v for k,v in score.items() if k!='detail'},ensure_ascii=False),flush=True)
    print('CONTROLS',json.dumps(controls,ensure_ascii=False),flush=True)
    print('OUTPUT',output,flush=True)


if __name__=='__main__':main()
