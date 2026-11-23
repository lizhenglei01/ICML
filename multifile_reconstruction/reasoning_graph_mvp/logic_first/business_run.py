"""Reproduce topic-driven and goal-driven verified business dependencies."""
import argparse
from pathlib import Path
import json
import time

from .run import save
from .business_author import propose
from .business_verify import verify
from .business_hybrid import build_hybrid
from .business_view import render_hybrid,render_goal
from .business_goal import run_backward,paths,goal_quantity
from .business_audit import definition_controls,shortest_one_operator,revpar_boundary_and_necessity
from .business_definitions import SOURCES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'business_artifacts')
    parser.add_argument('--reuse',action='store_true')
    parser.add_argument('--seeds',type=int,default=10)
    parser.add_argument('--topics',nargs='+',default=['酒店客房经营分析（同一季度口径）','零售商品经营分析（季度）'])
    parser.add_argument('--mode',choices=['all','topic','goal'],default='all')
    args=parser.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=True);started=time.perf_counter()
    if args.seeds<1:parser.error('seeds must be positive')
    reports=[]
    defaults=['酒店客房经营分析（同一季度口径）','零售商品经营分析（季度）']
    topics=[(('hotel','retail')[i] if args.topics==defaults else f'topic_{i:02d}',topic) for i,topic in enumerate(args.topics)]
    for domain,topic in (topics if args.mode!='goal' else []):
        folder=root/domain;author=folder/'author/attempt_01';source=author/'proposal.json'
        if args.reuse and source.exists():proposal=json.loads(source.read_text())
        else:proposal,_=propose(topic,author)
        # Check business definitions BEFORE any generated observation exists.
        verification=verify(proposal)
        save(folder/'verification.json',verification)
        controls=definition_controls(proposal);save(folder/'verifier_controls.json',controls)
        cases=[]
        if not verification['passed']:raise RuntimeError('No verified derived metrics for '+domain)
        for seed in range(args.seeds):
            task=build_hybrid(proposal,verification,seed);path=folder/f'task_{seed:02d}'
            result=render_hybrid(path,task)
            cases.append({'seed':seed,**result})
        reports.append({'topic':topic,'proposal_source':str(source),'accepted_derived':verification['accepted_derived'],
                        'draft_ids':verification['draft_ids'],'tasks':cases,'verifier_controls':controls,
                        'passed':controls['passed'] and all(c['passed'] for c in cases)})
        print('TOPIC_COMPLETE',domain,len(cases),verification['accepted_derived'],flush=True)
    goal_folder=root/'goal_revpar';cached=goal_folder/'result.json'
    if args.mode=='topic':
        save(root/'summary.json',{'passed':all(r['passed'] for r in reports),'topic_results':reports,
                                 'goal_result':{'status':'not_requested'},'elapsed_seconds':time.perf_counter()-started})
        return
    if args.reuse and cached.exists() and json.loads(cached.read_text()).get('passed'):
        goal=json.loads(cached.read_text())
        # Preserve actual historical model turns; refresh only independent checks.
        goal['paths']=paths(goal['quantities'],goal['productions'])
        goal['passed']=sum(p['accepted'] for p in goal['paths'])>=2
    else:goal=run_backward(goal_folder,args.reuse)
    goal['shortest_path_certificate']=shortest_one_operator(goal['quantities'],goal_quantity())
    goal['boundary_and_necessity']=revpar_boundary_and_necessity(goal)
    save(goal_folder/'result.json',goal);render_goal(goal_folder,goal)
    summary={'passed':all(r['passed'] for r in reports) and goal['passed'],
             'topic_results':reports,'goal_result':{'passed':goal['passed'],
               'accepted_paths':[{k:p[k] for k in ['production_ids','operator_count','dependency_depth','syntactic_inputs','simplified_sufficient_inputs','algebraically_cancelled_inputs']} for p in goal['paths'] if p['accepted']],
               'unconfirmed_paths':[p['production_ids'] for p in goal['paths'] if not p['accepted']],
               'minimum_certificate':goal['shortest_path_certificate']},
             'definition_sources':SOURCES,'elapsed_seconds':time.perf_counter()-started,
             'provenance':{'topic_metrics_and_formulas':'actual LLM inference','goal_alternatives':'actual iterative LLM backward expansion',
               'business_rules':'private manually registered definitions, with source/scoping records',
               'numeric_data':'business-consistent basic observations sampled AFTER formula acceptance',
               'execution':'existing typed numerical/set engine reused',
               'model':'see actual call logs; successful retries use medium effort, global configuration unchanged',
               'not_claimed':['arbitrary business-rule discovery','general proof of shortest deep inference','Office/Agent evaluation','full DocHop/LongChart reproduction']}}
    save(root/'summary.json',summary)
    print(json.dumps({'passed':summary['passed'],'theme_tasks':args.seeds*len(reports),'goal_paths':len(summary['goal_result']['accepted_paths']),
                      'seconds':summary['elapsed_seconds']},ensure_ascii=False),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':main()
