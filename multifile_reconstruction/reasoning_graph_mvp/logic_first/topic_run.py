"""Topic-first authoring experiment; no Office, editor Agent or depth padding."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import hashlib
import json
import time

from .topic_author import (generate_metadata,validate_metadata,writer_spec,narrative_request,
                           extraction_request,call_author,validate_document,compare_extracted)
from .topic_synthesis import generate_topic_task,graph_depth,shape_signature
from .chart_rules import execute,answers,semantic_ir,CATALOG,SUPPORTED


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def cached_call(request,directory,reuse):
    directory=Path(directory);request_file=directory/'request.json';response=directory/'response.txt'
    if reuse and request_file.exists() and response.exists() and json.loads(request_file.read_text())==request:
        from .topic_author import parse_json
        return parse_json(response.read_text()),json.loads((directory/'call.json').read_text())
    return call_author(request,directory)


def author_batch(items,directory,reuse=True):
    specs=[writer_spec(task,task_id) for task_id,task,path in items]
    previous=Path(directory)/'result.json'
    if reuse and previous.exists() and json.loads(previous.read_text()).get('passed'):
        # Resume the accepted attempt, rather than repeating a known timeout in
        # attempt 0. Re-check raw auditor IR with the current strict comparator.
        from .topic_author import parse_json
        valid=True
        for (task_id,task,path),spec in zip(items,specs):
            saved=json.loads((path/'private'/'writer_input.json').read_text())
            language=json.loads((path/'private'/'language_audit.json').read_text())
            rawpath=Path(language['auditor_call']['transport_audit']).parent.parent/'response.txt'
            recovered={d['task_id']:d for d in parse_json(rawpath.read_text())['tasks']}[task_id]
            match=compare_extracted(spec['rules'],recovered['ir'])
            writer_request_path=Path(language['writer_call']['transport_audit']).parent.parent/'request.json'
            saved_request=json.loads(writer_request_path.read_text())
            valid &= saved==spec and match['passed'] and recovered['bilingual_consistent'] and not recovered['issues']
            valid &= saved_request['messages'][:2]==narrative_request(specs)['messages']
            validate_document(spec,json.loads((path/'private'/'document.json').read_text()))
        if valid:
            result=json.loads(previous.read_text());result['reused_accepted_batch']=True
            result['task_ids']=[s['task_id'] for s in specs]
            return result
    history=[];feedback=None
    for attempt in range(2):
        folder=Path(directory)/f'attempt_{attempt}'
        request=narrative_request(specs)
        if feedback:
            request['messages'].append({'role':'user','content':json.dumps({'repair_required':feedback,
                     'instruction':'Rewrite faithfully to the original constraints. Preserve all required section IDs. No chart data or answers are provided.'},ensure_ascii=False)})
        try:
            raw,writer_info=cached_call(request,folder/'writer',reuse)
            by_id={doc['task_id']:doc for doc in raw['tasks']}
            if len(by_id)!=len(specs) or set(by_id)!={s['task_id'] for s in specs}:raise ValueError('AUTHOR_BATCH_IDS')
            documents=[validate_document(spec,by_id[spec['task_id']]) for spec in specs]
            extracted,auditor_info=cached_call(extraction_request(specs,documents),folder/'auditor',reuse)
            recovered={r['task_id']:r for r in extracted['tasks']}
            audits=[]
            for spec in specs:
                result=recovered[spec['task_id']]
                audit=compare_extracted(spec['rules'],result['ir'])
                audit.update(task_id=spec['task_id'],bilingual_consistent=result.get('bilingual_consistent') is True,
                             prose_issues=result.get('issues',[]))
                audit['passed'] &= audit['bilingual_consistent'] and not audit['prose_issues']
                audits.append(audit)
            record={'attempt':attempt,'passed':all(a['passed'] for a in audits),'semantic_roundtrip':audits,
                    'writer_call':writer_info,'auditor_call':auditor_info}
            history.append(record);save(folder/'audit.json',record)
            if record['passed']:
                for (_,task,path),doc,audit in zip(items,documents,audits):
                    save(path/'private'/'document.json',doc)
                    save(path/'private'/'writer_input.json',writer_spec(task,doc['task_id']))
                    save(path/'private'/'language_audit.json',{'accepted_attempt':attempt,'audit':audit,
                         'writer_call':writer_info,'auditor_call':auditor_info,
                         'limitation':'Model-assisted semantic roundtrip, not mathematical certification of arbitrary prose or human quality review.'})
                save(Path(directory)/'result.json',{'passed':True,'history':history,'task_ids':[s['task_id'] for s in specs]})
                return {'passed':True,'task_ids':[s['task_id'] for s in specs],'history':history}
            feedback=[{'task_id':a['task_id'],'expected':a.get('expected'),'recovered':a.get('extracted'),
                       'issues':a['prose_issues'],'bilingual_consistent':a['bilingual_consistent']} for a in audits if not a['passed']]
        except Exception as exc:
            history.append({'attempt':attempt,'passed':False,'error':type(exc).__name__+': '+str(exc)})
            save(folder/'failure.json',history[-1]);feedback={'error':str(exc)}
    result={'passed':False,'task_ids':[s['task_id'] for s in specs],'history':history}
    save(Path(directory)/'result.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'topic_artifacts')
    p.add_argument('--topics',nargs='+',default=['酒店后勤服务运营','仓储配送运营'])
    p.add_argument('--per-topic',type=int,default=10)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--reuse',action='store_true',help='Reuse only matching saved model requests; no fake model fallback')
    p.add_argument('--author-only',action='store_true',help='Save topic tasks and model prose; render after a successful authoring run')
    args=p.parse_args()
    if args.per_topic<1 or args.workers<1:p.error('Counts must be positive')
    started=time.perf_counter();root=args.output;root.mkdir(parents=True,exist_ok=True)
    items=[];metadata_info=[]
    for index,topic in enumerate(args.topics):
        slug=['hotel','warehouse'][index] if args.topics==['酒店后勤服务运营','仓储配送运营'] else f'topic_{index:02d}'
        directory=root/slug;meta_file=directory/'metadata_author'/'metadata.json'
        if args.reuse and meta_file.exists():
            request=json.loads((meta_file.parent/'request.json').read_text())
            requested=json.loads(request['messages'][1]['content'])['topic']
            if requested!=topic:raise ValueError('Cached metadata topic differs')
            metadata=validate_metadata(json.loads(meta_file.read_text()))
            info=json.loads((meta_file.parent/'call.json').read_text())
        else:metadata,info=generate_metadata(topic,directory/'metadata_author')
        metadata_info.append({'topic':topic,'model_call':info,'metrics':[m['zh'] for m in metadata['metrics']]})
        for i in range(args.per_topic):
            path=directory/f'task_{i:02d}';task_file=path/'private'/'task.json'
            seed=i+index*100
            # Stored construction is usable only with the same metadata and config.
            if args.reuse and task_file.exists():
                task=json.loads(task_file.read_text())
                if task['metadata']!=metadata or task['seed']!=seed or task['depth']!=2+i%4:raise ValueError('Cached task differs')
            else:task=generate_topic_task(metadata,seed,2+i%4,chart_count=3+i%2)
            state=execute(task);truth=answers(task,state)
            if any(sorted(state[k])!=v for k,v in task['intended_sets'].items()):raise AssertionError('Membership mismatch')
            save(task_file,task);save(path/'private'/'truth.json',truth)
            save(path/'private'/'node_values.json',{k:sorted(v) for k,v in state.items()})
            items.append((f'{slug}_{i:02d}',task,path))
    print('METADATA_AND_TASKS_READY',len(items),flush=True)
    chunks=[items[i:i+2] for i in range(0,len(items),2)];batches=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(author_batch,batch,root/'authoring'/f'batch_{i:02d}',args.reuse):i for i,batch in enumerate(chunks)}
        for future in as_completed(pending):
            result=future.result();batches.append(result)
            print('BATCH_COMPLETE',pending[future],result['passed'],result['task_ids'],flush=True)
            save(root/'authoring_progress.json',{'completed_batches':batches,'total_batches':len(chunks)})
    failed=[r for r in batches if not r['passed']]
    report={'metadata':metadata_info,'task_count':len(items),'distinct_shape_count':len({shape_signature(t) for _,t,_ in items}),
            'depths':sorted({graph_depth(t) for _,t,_ in items}),
            'supported_templates':sorted(SUPPORTED),'observed_templates':sorted({n['template'] for _,t,_ in items for n in t['nodes'] if n['op']=='ATOM'}),
            'catalog_templates':len(CATALOG),'batches':batches,'passed':not failed,
            'provenance':{'metadata':'real LLM inference from topic','tree':'program sampling',
                          'chart_data':'SMT integer constraint synthesis','prose':'real LLM inference, no data values or answers supplied',
                          'language_verification':'independent-context LLM extraction + program IR comparison; not a semantic proof',
                          'upstream_generator_functions_called':[],
                          'not_done':['all 27 templates','all 41 question types','original DocHop Gemini per-chart synthesis',
                                      'original ReportLab single-page renderer','human review of all reports','Office/Agent edits','LongChart expansion']}}
    if not args.author_only and not failed:
        from .topic_render import render_topic_bundle
        checks=[]
        for task_id,task,path in items:
            doc=json.loads((path/'private'/'document.json').read_text())
            check=render_topic_bundle(path,task,doc)
            checks.append({'task_id':task_id,**check})
        report['render_checks']=checks;report['passed'] &= all(c['passed'] for c in checks)
        from .graph_view import gallery
        report['dependency_graph_gallery']=str(gallery(root))
    report['elapsed_seconds']=time.perf_counter()-started
    save(root/'summary.json',report)
    print('TOPIC_PIPELINE_COMPLETE',json.dumps({'passed':report['passed'],'tasks':len(items),'shapes':report['distinct_shape_count'],'seconds':report['elapsed_seconds']},ensure_ascii=False),flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
