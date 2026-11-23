"""Independent arithmetic/row-wise reference; never calls the SMT atom predicate."""
from fractions import Fraction
import json

from .chart_rules import coordinate,execute,answers,CATALOG,SUPPORTED
from .topic_synthesis import chart_coordinates,graph_depth


def reference_states(task):
    state={};facts=task['facts'];entities=set(task['metadata']['entities'])
    for n in task['nodes']:
        if n['op'] in {'AND','OR'}:
            a,b=[state[key] for key in n['inputs']]
            state[n['id']]=a.intersection(b) if n['op']=='AND' else a.union(b)
            continue
        p=n['params'];kind=n['template']
        scope=state[n['inputs'][0]] if n['inputs'] else entities
        selected=set()
        def read(entity,metric=None,group=None,timestamp=None):
            return facts[coordinate(entity,metric or p['metric'],group or p['group'],timestamp or p['timestamp'])]
        def compare(a,b):
            return not a<b if p.get('comparison','GE')=='GE' else not a>b
        for entity in scope:
            current=read(entity)
            if kind in {'timestamp_threshold','metric_threshold','same_group_value_threshold'}:
                accepted=compare(current,p['threshold'])
            elif kind=='timestamp_vs_across_entity_agg':
                accepted=compare(current,Fraction(sum(read(e) for e in scope),len(scope)))
            elif kind=='timestamp_vs_within_entity_time_agg':
                accepted=compare(current,Fraction(sum(read(entity,timestamp=t) for t in p['window']),len(p['window'])))
            elif kind=='two_point_change_threshold':
                accepted=compare(read(entity,timestamp=p['end'])-read(entity,timestamp=p['start']),p['threshold'])
            elif kind=='two_point_percentage_change':
                base=read(entity,timestamp=p['start'])
                if base<=0:raise ValueError('BAD_PERCENT_BASE')
                change=(Fraction(read(entity,timestamp=p['end']),base)-1)*100
                accepted=compare(change,p['threshold'])
            elif kind in {'window_monotone','window_slope_strict_monotone'}:
                series=[read(entity,timestamp=t) for t in p['window']]
                if kind=='window_slope_strict_monotone':series=[series[i]-series[i-1] for i in range(1,len(series))]
                accepted=len(set(series))==len(series) and series==sorted(series,reverse=p['direction']=='down')
            elif kind in {'within_entity_metric_compare','metric_difference_threshold'}:
                other=read(entity,metric=p['other_metric'])
                accepted=compare(current,other) if kind=='within_entity_metric_compare' else compare(current-other,p['threshold'])
            elif kind in {'within_entity_group_compare','group_difference_threshold'}:
                other=read(entity,group=p['other_group'])
                accepted=compare(current,other) if kind=='within_entity_group_compare' else compare(current-other,p['threshold'])
            elif kind in {'timestamp_extreme','window_sum_extreme','single_metric_cross_entity_extreme','same_group_value_extreme'}:
                scores={e:(sum(read(e,timestamp=t) for t in p['window']) if kind=='window_sum_extreme' else read(e)) for e in scope}
                order=sorted(scores.values(),reverse=p['direction']=='up')
                accepted=scores[entity]==order[0] and order.count(order[0])==1
            else:raise ValueError('REFERENCE_UNSUPPORTED')
            if accepted:selected.add(entity)
        state[n['id']]=selected
    return state


def audit_task(task):
    observed=execute(task);reference=reference_states(task)
    checks=[]
    for node in task['nodes']:
        key=node['id']
        checks.append({'name':'independent_selection:'+key,'passed':observed[key]==reference[key],
                       'actual':sorted(observed[key]),'expected':sorted(reference[key])})
        checks.append({'name':'intended_membership:'+key,'passed':sorted(observed[key])==task['intended_sets'][key]})
        if node['op']=='ATOM':
            scope=observed[node['inputs'][0]] if node['inputs'] else set(task['metadata']['entities'])
            checks.append({'name':'effective_filter:'+key,'passed':bool(observed[key]) and observed[key]<scope})
        else:
            checks.append({'name':'effective_boolean:'+key,'passed':all(observed[key]!=observed[i] for i in node['inputs'])})
    coords={key for c in task['charts'] for key in chart_coordinates(task['metadata'],c)}
    checks.append({'name':'all_facts_public_in_charts','passed':coords==set(task['facts'])})
    checks.append({'name':'integer_nonnegative_domains','passed':all(type(v) is int and 10<=v<=250 for v in task['facts'].values())})
    checks.append({'name':'requested_graph_depth','passed':graph_depth(task)==task['depth']})
    q=task['average_query'];chosen=reference[task['selection']]
    checks.append({'name':'nonempty_target_for_average','passed':bool(chosen)})
    if not chosen:return {'passed':False,'checks':checks,'depth_scope':'Generated graph depth only.'}
    independent_total=sum(task['facts'][coordinate(e,q['metric'],q['group'],q['timestamp'])] for e in chosen)
    expected={'entities':sorted(chosen),'count':len(chosen),
              'average_exact':str(Fraction(independent_total,len(chosen))),'average':float(Fraction(independent_total,len(chosen)))}
    checks.append({'name':'independent_answers','passed':answers(task)==expected,'expected':expected,'actual':answers(task)})
    return {'passed':all(c['passed'] for c in checks),'checks':checks,
            'depth_scope':'Generated graph depth; no assertion of globally shortest reasoning or cross-file necessity.'}


def negative_controls(task):
    from copy import deepcopy
    truth=answers(task)
    controls=[]
    for field in ['entities','count','average']:
        wrong=dict(truth);wrong[field]=[] if field=='entities' else truth[field]+1
        controls.append({'case':'wrong_'+field,'accepted':wrong==truth,'expected_accept':False})
    bad=deepcopy(task);del bad['facts'][next(iter(bad['facts']))]
    try:r=audit_task(bad);accepted=r['passed']
    except (KeyError,ValueError):accepted=False
    controls.append({'case':'missing_chart_fact','accepted':accepted,'expected_accept':False})
    node=next((n for n in task['nodes'] if n['op'] in {'AND','OR'}),None)
    if node:
        bad=deepcopy(task)
        altered=next(n for n in bad['nodes'] if n['id']==node['id'])
        altered['op']='OR' if node['op']=='AND' else 'AND'
        controls.append({'case':'flipped_boolean','accepted':audit_task(bad)['passed'],'expected_accept':False})
    return {'passed':all(c['accepted']==c['expected_accept'] for c in controls),'cases':controls}
