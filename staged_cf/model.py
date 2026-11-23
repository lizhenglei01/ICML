"""Two genuine inference turns on one existing Codex Relay thread, with no host tools."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from openpyxl import load_workbook

from alignment_probe.codex_transport import configured_route,relay_class,RELAY_SOURCE
from medical_cf.run import save
from .session import Session
from .evaluate import evaluate
from .build import digest

ROLE='counterfactual_analyst'
SYSTEM='你是数据分析助手。仅依据本轮提供的业务要求和原始资料作答。自行分析数据关系并计算，不能访问主机、文件、网络或其他工具。严格按要求输出一个JSON对象，不输出代码围栏或额外文字。'


def raw_prompt(stage1):
    stage1=Path(stage1)
    text=(stage1/'instruction.md').read_text()+'\n附件 source.xlsx 的逐表原始内容如下，只有表头和原始记录：\n'
    wb=load_workbook(stage1/'source.xlsx',data_only=False)
    for ws in wb:
        buffer=io.StringIO();writer=csv.writer(buffer,lineterminator='\n')
        for row in ws.iter_rows(values_only=True):writer.writerow(row)
        text+='\n工作表 '+ws.title+'\n'+buffer.getvalue()
    return text


def parse(text):
    # Only a whole JSON object or a single whole fenced JSON is accepted. No extraction from hidden reasoning.
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):
        text=text[text.find('\n')+1:-3].strip()
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('Model returned a non-object')
    return value


def run(package,output):
    package,output=Path(package).resolve(),Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    session=Session(package,output/'session');stage1=session.start()
    base,relay_error=relay_class();route=configured_route()
    if not route['model']:raise ValueError('No configured model')
    model,effort=route['model'],route['effort'] or 'high'
    env=os.environ.copy()
    if route['use_saved_chatgpt_login']:env.pop('CODEX_API_KEY',None)
    calls,starts,items,usage=[],[],[],[]
    expected_thread=None
    class AnalystRelay(base):
        def _request(self,method,params,timeout):
            params=dict(params)
            if method=='thread/start':
                if expected_thread is not None:raise relay_error('Refusing to replace the committed stage1 conversation')
                params.update(cwd=str(empty),baseInstructions=SYSTEM,developerInstructions='',ephemeral=True,
                    config={'project_doc_max_bytes':0,'web_search':'disabled','features.multi_agent':False,
                            'features.multi_agent_v2':False,'features.code_mode':False,'features.code_mode_host':False},
                    serviceName='icm2027-two-stage-probe')
            elif method=='turn/start':
                params['model']=model
                if expected_thread is not None and params['threadId']!=expected_thread:
                    raise relay_error('Stage2 conversation differs from locked stage1')
            if method in {'thread/start','turn/start'}:calls.append({'method':method,'params':params})
            result=super()._request(method,params,timeout)
            if method=='thread/start':
                entry={k:result.get(k) for k in ['model','modelProvider','reasoningEffort','instructionSources']}
                entry['thread_id']=result['thread']['id'];starts.append(entry)
                if result.get('model')!=model or result.get('instructionSources'):
                    raise relay_error('Unexpected model or instruction source')
            return result

        def _read_message(self,timeout):
            message=super()._read_message(timeout)
            params=message.get('params') or {};method=message.get('method','')
            if method in {'item/started','item/completed'}:
                kind=(params.get('item') or {}).get('type')
                items.append({'method':method,'type':kind,'thread_id':params.get('threadId'),'turn_id':params.get('turnId')})
                if kind not in {'userMessage','reasoning','agentMessage'}:
                    raise relay_error('Host tool/non-inference activity is forbidden: '+str(kind))
            if method=='thread/tokenUsage/updated':usage.append(params.get('tokenUsage'))
            return message

    with tempfile.TemporaryDirectory(prefix='staged-cf-model-') as td:
        empty=Path(td)
        backend=AnalystRelay(workspace=output,run_id='staged_'+uuid.uuid4().hex,model=model,reasoning_effort=effort,
            disable_host_tools=True,disable_apps=True,process_env=env,role_timeout_seconds=180,max_role_turns=2,max_process_turns=2)
        try:
            prompt1=raw_prompt(stage1)
            (output/'prompt_stage1.txt').write_text(prompt1)
            print('MODEL_STAGE1_STARTED',model,effort,flush=True)
            started=time.monotonic()
            result1=asyncio.run(backend.request(role=ROLE,prompt=prompt1))
            if result1.get('status')!='completed':raise relay_error('Stage1 incomplete')
            (output/'response_stage1.txt').write_text(result1['text'])
            save(output/'call_stage1.json',{k:v for k,v in result1.items() if k!='text'})
            first=parse(result1['text']);session.commit(1,first)
            expected_thread=backend._threads[ROLE]
            print('MODEL_STAGE1_LOCKED',round(time.monotonic()-started,2),'seconds',flush=True)
            prompt2=session.reveal();(output/'prompt_stage2.txt').write_text(prompt2)
            print('MODEL_STAGE2_STARTED same thread',expected_thread,flush=True)
            started=time.monotonic()
            result2=asyncio.run(backend.request(role=ROLE,prompt=prompt2))
            if result2.get('status')!='completed' or not result2.get('thread_reused'):
                raise relay_error('Stage2 incomplete or not on the same conversation')
            (output/'response_stage2.txt').write_text(result2['text'])
            save(output/'call_stage2.json',{k:v for k,v in result2.items() if k!='text'})
            second=parse(result2['text']);session.commit(2,second);session.verify_locked()
            print('MODEL_STAGE2_LOCKED',round(time.monotonic()-started,2),'seconds',flush=True)
            report=evaluate(package,first,second,output/'delivered')
            save(output/'evaluation.json',report)
            print('MODEL_EVALUATION',json.dumps(report['summary'],ensure_ascii=False),flush=True)
        finally:
            backend._close_sync()
            save(output/'transport_audit.json',{'backend_source':str(RELAY_SOURCE),'backend_sha256':digest(RELAY_SOURCE),
                'route':route,'starts':starts,'wire_requests':calls,'item_events':items,'usage':usage,
                'host_tools_disabled':True,'apps_disabled':True,'empty_cwd':str(empty),'expected_stage2_thread':expected_thread,
                'scope':'Raw public workbook cells are serialized losslessly as CSV text for inference. No GUI/Office-reading ability is claimed.'})
    return report


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.package,a.out)


if __name__=='__main__':main()
