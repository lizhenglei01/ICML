"""Metadata -> varied scoped trees -> joint chart-constraint solving."""
from copy import deepcopy
import hashlib
import json
import random
import time

from .chart_rules import CATALOG, SUPPORTED, coordinate, condition, execute, answers
from .topic_author import validate_metadata


def chart_coordinates(metadata,chart):
    return [coordinate(e,m,g,t) for e in metadata['entities'] for m in chart['metrics']
            for g in chart['groups'] for t in chart['timestamps']]


def sample_charts(metadata,rng,count):
    charts=[]
    metricids=[m['id'] for m in metadata['metrics']];groupids=[g['id'] for g in metadata['groups']]
    types=['line','multi_metric_bar','group_bar']
    for i in range(count):
        kind=types[i%3] if i<3 else rng.choice(types)
        charts.append({'id':f'chart_{i+1}','kind':kind,
                       'metrics':rng.sample(metricids,3 if kind=='multi_metric_bar' else 1),
                       'groups':rng.sample(groupids,3 if kind=='group_bar' else 1),
                       'timestamps':metadata['timestamps'] if kind=='line' else [metadata['timestamps'][-1]]})
    return charts


def skeleton(rng,depth,budget=8):
    if depth==1:return {'op':'ATOM','inputs':[]}
    # A real successive filter: the atom receives the preceding candidate set.
    if rng.random()<.4 or budget<=depth:
        return {'op':'ATOM','inputs':[skeleton(rng,depth-1,budget-1)]}
    other=rng.randint(1,min(depth-1,budget-depth))
    children=[skeleton(rng,depth-1,budget-other),skeleton(rng,other,other)]
    if rng.random()<.5:children.reverse()
    return {'op':rng.choice(['AND','OR']),'inputs':children}


def instantiate(tree,charts,rng,seed):
    nodes=[]
    def visit(part,parent=None):
        inputs=[visit(c,part['op']) for c in part['inputs']]
        node={'id':f'stage_{len(nodes)+1}','op':part['op'],'inputs':inputs}
        if node['op']=='ATOM':
            eligible=[name for name in sorted(SUPPORTED)
                      if (not CATALOG[name][2] or parent in {None,'OR'})]
            template=rng.choice(eligible)
            compatible=[c for c in charts if c['kind']==CATALOG[template][0]]
            if not compatible:
                template=rng.choice([t for t in eligible if any(c['kind']==CATALOG[t][0] for c in charts)])
                compatible=[c for c in charts if c['kind']==CATALOG[template][0]]
            chart=rng.choice(compatible)
            metric=rng.choice(chart['metrics']);group=rng.choice(chart['groups'])
            ts=chart['timestamps']
            p={'metric':metric,'group':group,'timestamp':rng.choice(ts),
               'comparison':rng.choice(['GE','LE']),'direction':rng.choice(['up','down']),
               'threshold':rng.choice([10,20,30,50,70,100]),'window':ts,
               'start':ts[0],'end':ts[-1]}
            if len(chart['metrics'])>1:p['other_metric']=rng.choice([m for m in chart['metrics'] if m!=metric])
            if len(chart['groups'])>1:p['other_group']=rng.choice([g for g in chart['groups'] if g!=group])
            node.update(template=template,chart_id=chart['id'],params=p,
                        source=f'DocHop appendix {CATALOG[template][1]} / {template}')
        nodes.append(node);return node['id']
    selection=visit(tree)
    return nodes,selection


def intended_sets(nodes,entities,rng,attempts=800):
    universe=set(entities)
    for _ in range(attempts):
        desired={};valid=True
        for node in nodes:
            if node['op']=='ATOM':
                pool=desired[node['inputs'][0]] if node['inputs'] else universe
                if len(pool)<2:valid=False;break
                if CATALOG[node['template']][2]:result={rng.choice(sorted(pool))}
                else:result={e for e in sorted(pool) if rng.random()<.55}
                if not result or result==pool:valid=False;break
            else:
                a,b=[desired[i] for i in node['inputs']]
                result=a&b if node['op']=='AND' else a|b
                if not result or result==universe or result==a or result==b:valid=False;break
            desired[node['id']]=result
        if valid:return desired
    raise ValueError('NO_DISCRIMINATIVE_MEMBERSHIPS')


def solve_charts(metadata,charts,nodes,desired,seed,timeout_ms=2500):
    import z3
    # Shared coordinates are one variable even when repeated in different charts.
    coords=sorted({key for c in charts for key in chart_coordinates(metadata,c)})
    variables={key:z3.Int(f'v{i}') for i,key in enumerate(coords)}
    solver=z3.Solver();solver.set(timeout=timeout_ms,random_seed=seed%2147483647)
    for value in variables.values():solver.add(value>=10,value<=250)
    universe=set(metadata['entities'])
    for node in nodes:
        if node['op']!='ATOM':continue
        pool=desired[node['inputs'][0]] if node['inputs'] else universe
        for entity in sorted(pool):
            pred=condition(node,entity,sorted(pool),variables.__getitem__,symbolic=True)
            solver.add(pred if entity in desired[node['id']] else z3.Not(pred))
    # Vary unforced coordinates as well. Constraint correctness is never traded
    # away for this soft diversity preference; individual anchors are retractable.
    rng=random.Random(seed+65537)
    for key in rng.sample(coords,min(4,len(coords))):
        anchor=rng.randint(15,220)
        solver.push();solver.add(variables[key]==anchor)
        feasible=solver.check()==z3.sat
        solver.pop()
        if feasible:solver.add(variables[key]==anchor)
    status=solver.check()
    if status!=z3.sat:raise ValueError('CHART_SOLVER_'+str(status))
    model=solver.model()
    return {key:model.eval(value,model_completion=True).as_long() for key,value in variables.items()}


def graph_depth(task):
    d={}
    for n in task['nodes']:d[n['id']]=1+max([d[i] for i in n['inputs']] or [0])
    return d[task['selection']]


def shape_signature(task):
    nodes={n['id']:n for n in task['nodes']}
    def shape(key):
        n=nodes[key]
        children=[shape(i) for i in n['inputs']]
        if n['op'] in {'AND','OR'}:children=sorted(children)
        return n['op']+'('+','.join(children)+')'
    return shape(task['selection'])


def generate_topic_task(metadata,seed,depth,chart_count=3,max_attempts=80):
    validate_metadata(metadata)
    if depth not in {2,3,4,5} or chart_count not in {2,3,4,6}:raise ValueError('GENERATION_CONFIG')
    started=time.perf_counter();history=[]
    for attempt in range(max_attempts):
        rng=random.Random(seed+attempt*1000003)
        charts=sample_charts(metadata,rng,chart_count)
        nodes,selection=instantiate(skeleton(rng,depth),charts,rng,seed)
        try:
            desired=intended_sets(nodes,metadata['entities'],rng)
            facts=solve_charts(metadata,charts,nodes,desired,seed+attempt)
        except ValueError as exc:
            history.append({'attempt':attempt,'reason':str(exc)});continue
        chart=charts[-1]
        task={'schema_version':'topic-logic/1','seed':seed,'depth':depth,'metadata':deepcopy(metadata),
              'charts':charts,'nodes':nodes,'selection':selection,'facts':facts,
              'average_query':{'metric':chart['metrics'][0],'group':chart['groups'][0],'timestamp':chart['timestamps'][-1]},
              'intended_sets':{k:sorted(v) for k,v in desired.items()},'construction_attempts':attempt+1,
              'rejected_constructions':history,
              'provenance':{'metadata':'LLM (or explicit supplied metadata file; see run manifest)',
                            'tree':'program-sampled conditional and Boolean structure',
                            'data':'bounded integer SMT constraints, not LLM or post-hoc formula expansion'}}
        state=execute(task)
        if any(state[k]!=desired[k] for k in desired):raise AssertionError('SOLVER_EXECUTOR_MISMATCH')
        if graph_depth(task)!=depth:raise AssertionError('DEPTH_MISMATCH')
        task['shape_signature']=shape_signature(task)
        task['generation_seconds']=time.perf_counter()-started
        return task
    raise ValueError('GENERATION_EXHAUSTED:'+json.dumps(history[-4:]))
