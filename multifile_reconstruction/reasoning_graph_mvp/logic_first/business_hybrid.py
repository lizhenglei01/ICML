"""Compile verified metric formulas into the existing typed numerical/set engine."""
from fractions import Fraction
import math
import random

from .business_definitions import canonical,units
from .business_data import worlds
from .business_verify import project_verified
from .engine import execute,validate,depths


def number(value):
    return int(value) if value.denominator==1 else float(value)


def compile_metrics(proposal,world_rows):
    nodes=[];facts={};bindings={};metrics={m['id']:m for m in proposal['metrics']}
    entities=proposal['entities'];active=set()
    def compile_quantity(key):
        if key in bindings:return bindings[key]
        if key in active:raise ValueError('CYCLE')
        active.add(key);metric=metrics[key]
        if metric['kind']=='base':
            nodes.append({'id':key,'op':'FACT','inputs':[],'unit':units(metric['unit']),'metric':key})
            role=canonical(metric['concept'])
            facts[key]={e:number(w['roles'][role]) for e,w in zip(entities,world_rows)}
            result=key
        else:
            serial=[0]
            def compile_expr(expr,is_root=False):
                if 'ref' in expr:return compile_quantity(expr['ref'])
                if 'const' in expr:
                    if 'constant_one' not in facts:
                        nodes.append({'id':'constant_one','op':'FACT','inputs':[],'unit':{}})
                        facts['constant_one']={e:1 for e in entities}
                    return 'constant_one'
                incoming=[compile_expr(c) for c in expr['args']]
                serial[0]+=1;identifier=key if is_root else key+'__op_'+str(serial[0])
                nodes.append({'id':identifier,'op':expr['op'],'inputs':incoming,**({'unit':units(metric['unit'])} if is_root else {})})
                return identifier
            result=compile_expr(metric['formula'],True)
        bindings[key]=result;active.remove(key);return result
    for metric in proposal['metrics']:compile_quantity(metric['id'])
    return {'entities':entities,'nodes':nodes,'facts':facts,'outputs':list(bindings.values())},bindings


def thresholds(unit):
    if unit=={}:return [0,.25,.5,.75]
    if unit=={'CNY':1}:return [5000,50000,100000,200000]
    if unit.get('CNY')==1:return [50,100,300,600]
    return [10,50,100]


def build_hybrid(proposal,verification,seed=0):
    profile=project_verified(proposal,verification)
    derived=[m for m in profile['metrics'] if m['kind']=='derived']
    pairs=[(a,b) for a in derived for b in derived if a['id']!=b['id'] and units(a['unit'])!=units(b['unit'])]
    if not pairs:raise ValueError('Need at least two verified derived metrics with different units')
    rng=random.Random(seed)
    for attempt in range(100):
        first,second=rng.choice(pairs);logic=rng.choice(['AND','OR'])
        policies=[{'metric':m['id'],'op':rng.choice(['GE','LE']),'threshold':rng.choice(thresholds(units(m['unit'])))} for m in [first,second]]
        # The policy is fixed before its observations are sampled.
        rows=list(worlds(seed+attempt*104729,len(profile['entities'])))
        graph,bindings=compile_metrics(profile,rows)
        for i,policy in enumerate(policies):
            metric=next(m for m in profile['metrics'] if m['id']==policy['metric'])
            graph['nodes'].append({'id':f'condition_{i}','op':policy['op'],'inputs':[bindings[policy['metric']]],
                                   'threshold':policy['threshold'],'threshold_unit':units(metric['unit'])})
        graph['nodes'] += [{'id':'selection','op':logic,'inputs':['condition_0','condition_1']},
                           {'id':'selected_count','op':'COUNT','inputs':['selection']},
                           {'id':'selected_average','op':'AVG','inputs':['selection',bindings[first['id']]]}]
        graph['outputs']+=['selection','selected_count','selected_average']
        try:state=execute(graph)
        except ValueError:continue
        a,b=state['condition_0'],state['condition_1'];selected=state['selection'];universe=set(profile['entities'])
        if not a or not b or a==universe or b==universe or not selected or selected==universe or selected in [a,b]:continue
        independent=[]
        for policy in policies:
            role=canonical(next(m['concept'] for m in profile['metrics'] if m['id']==policy['metric']))
            threshold=Fraction(str(policy['threshold']))
            independent.append({e for e,w in zip(profile['entities'],rows)
                                if (w['roles'][role]>=threshold if policy['op']=='GE' else w['roles'][role]<=threshold)})
        expected=independent[0]&independent[1] if logic=='AND' else independent[0]|independent[1]
        if selected!=expected:raise AssertionError('Independent policy result mismatch')
        role=canonical(first['concept']);average=sum(w['roles'][role] for e,w in zip(profile['entities'],rows) if e in expected)/len(expected)
        if not math.isclose(state['selected_average'],float(average),abs_tol=1e-9,rel_tol=1e-10):raise AssertionError('Independent average mismatch')
        # Also validate every derived vector, not just the selected final answer.
        checks=[]
        for metric in derived:
            role=canonical(metric['concept'])
            for entity,row in zip(profile['entities'],rows):
                actual=state[bindings[metric['id']]][entity];reference=row['roles'][role]
                checks.append({'metric':metric['id'],'entity':entity,'actual':actual,'expected_exact':str(reference),
                               'passed':math.isclose(actual,float(reference),rel_tol=1e-10,abs_tol=1e-9)})
        if not all(c['passed'] for c in checks):raise AssertionError('Independent derived result mismatch')
        return {'seed':seed,'profile':profile,'graph':graph,'bindings':bindings,'policies':policies,'logic':logic,
                'truth':{'entities':sorted(expected),'count':len(expected),'average_exact':str(average),
                         'average':float(average),'average_metric':first['id']},
                'state':state,'independent_numeric_checks':checks,'construction_attempts':attempt+1,
                'graph_depth':depths(graph)['selection'],
                'depth_scope':'Compiled graph depth, not a globally shortest inference-depth certificate. See target-driven alternative-path audit.',
                'policy_scope':'Explicit synthetic selection thresholds, not real industry certification criteria.'}
    raise ValueError('No discriminative mixed-unit policy/data instance found')


def negative_submissions(task):
    from copy import deepcopy
    truth=task['truth'];cases=[]
    for field in ['entities','count','average']:
        wrong=deepcopy(truth);wrong[field]=[] if field=='entities' else wrong[field]+1
        cases.append({'case':'wrong_'+field,'accepted':wrong==truth,'expected_accept':False})
    bad=deepcopy(task['graph']);node=next(n for n in bad['nodes'] if n['id']=='selection')
    node['op']='OR' if node['op']=='AND' else 'AND'
    altered=execute(bad,targets=['selection'])['selection']
    cases.append({'case':'wrong_boolean','accepted':altered==set(truth['entities']),'expected_accept':False})
    return {'passed':all(c['accepted']==c['expected_accept'] for c in cases),'cases':cases}
