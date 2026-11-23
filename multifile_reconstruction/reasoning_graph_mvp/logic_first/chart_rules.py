"""Chart-grounded atoms from DocHop appendix C, independently implemented.

These are synthetic selection policies, not discovered real business standards.
Relations compare same-unit observations. No invented indicator formula is used.
"""
from fractions import Fraction


CATALOG = {
    'timestamp_threshold': ('line', 'C.1', False),
    'timestamp_vs_across_entity_agg': ('line', 'C.1', False),
    'timestamp_vs_within_entity_time_agg': ('line', 'C.1', False),
    'two_point_change_threshold': ('line', 'C.1', False),
    'two_point_percentage_change': ('line', 'C.1', False),
    'window_monotone': ('line', 'C.1', False),
    'window_slope_strict_monotone': ('line', 'C.1', False),
    'timestamp_extreme': ('line', 'C.1', True),
    'window_sum_extreme': ('line', 'C.1', True),
    'metric_threshold': ('multi_metric_bar', 'C.2', False),
    'row_metric_extreme': ('multi_metric_bar', 'C.2', False),
    'row_metric_vs_row_stat': ('multi_metric_bar', 'C.2', False),
    'row_share_threshold': ('multi_metric_bar', 'C.2', False),
    'within_entity_metric_compare': ('multi_metric_bar', 'C.2', False),
    'metric_difference_threshold': ('multi_metric_bar', 'C.2', False),
    'single_metric_cross_entity_extreme': ('multi_metric_bar', 'C.2', True),
    'row_total_cross_entity_extreme': ('multi_metric_bar', 'C.2', True),
    'within_entity_group_compare': ('group_bar', 'C.3', False),
    'within_entity_group_share_threshold': ('group_bar', 'C.3', False),
    'same_group_value_threshold': ('group_bar', 'C.3', False),
    'same_group_share_threshold': ('group_bar', 'C.3', False),
    'same_group_vs_row_stat': ('group_bar', 'C.3', False),
    'group_difference_threshold': ('group_bar', 'C.3', False),
    'row_group_extreme': ('group_bar', 'C.3', False),
    'same_group_value_extreme': ('group_bar', 'C.3', True),
    'same_group_share_extreme': ('group_bar', 'C.3', True),
    'row_groups_sum_extreme': ('group_bar', 'C.3', True),
}
# Start with a varied, linear-integer-solvable subset. The remaining catalog
# entries are documented, NOT silently advertised as supported/executed.
SUPPORTED = {
    'timestamp_threshold', 'timestamp_vs_across_entity_agg',
    'timestamp_vs_within_entity_time_agg', 'two_point_change_threshold',
    'two_point_percentage_change', 'window_monotone',
    'window_slope_strict_monotone', 'timestamp_extreme', 'window_sum_extreme',
    'metric_threshold', 'within_entity_metric_compare', 'metric_difference_threshold',
    'single_metric_cross_entity_extreme', 'within_entity_group_compare',
    'same_group_value_threshold', 'group_difference_threshold',
    'same_group_value_extreme',
}


PARAMETERS = {
    'timestamp_threshold':['metric','group','timestamp','comparison','threshold'],
    'timestamp_vs_across_entity_agg':['metric','group','timestamp','comparison'],
    'timestamp_vs_within_entity_time_agg':['metric','group','timestamp','window','comparison'],
    'two_point_change_threshold':['metric','group','start','end','comparison','threshold'],
    'two_point_percentage_change':['metric','group','start','end','comparison','threshold'],
    'window_monotone':['metric','group','window','direction'],
    'window_slope_strict_monotone':['metric','group','window','direction'],
    'timestamp_extreme':['metric','group','timestamp','direction'],
    'window_sum_extreme':['metric','group','window','direction'],
    'metric_threshold':['metric','group','timestamp','comparison','threshold'],
    'within_entity_metric_compare':['metric','other_metric','group','timestamp','comparison'],
    'metric_difference_threshold':['metric','other_metric','group','timestamp','comparison','threshold'],
    'single_metric_cross_entity_extreme':['metric','group','timestamp','direction'],
    'within_entity_group_compare':['metric','group','other_group','timestamp','comparison'],
    'same_group_value_threshold':['metric','group','timestamp','comparison','threshold'],
    'group_difference_threshold':['metric','group','other_group','timestamp','comparison','threshold'],
    'same_group_value_extreme':['metric','group','timestamp','direction'],
}


DESCRIPTIONS = {
    'timestamp_threshold':'At timestamp, metric for group is GE/LE the absolute threshold.',
    'timestamp_vs_across_entity_agg':'At timestamp, metric for group is GE/LE the arithmetic mean over the incoming candidate scope (not all entities if an input scope exists).',
    'timestamp_vs_within_entity_time_agg':'At timestamp, metric for group is GE/LE this same entity\'s arithmetic mean over window.',
    'two_point_change_threshold':'Metric for group at end MINUS its value at start is GE/LE the absolute threshold (not percentage).',
    'two_point_percentage_change':'100*(end-start)/start for the metric and group is GE/LE threshold percent; start is positive.',
    'window_monotone':'Metric for group strictly increases (up) or strictly decreases (down) across every consecutive timestamp in window.',
    'window_slope_strict_monotone':'Consecutive absolute increments (differences) strictly increase (up) or strictly decrease (down) across window. The level itself need not increase.',
    'timestamp_extreme':'Unique strict maximum (up) or unique strict minimum (down) of metric for group at timestamp, within incoming candidates. Ties do not qualify.',
    'window_sum_extreme':'Unique strict maximum (up) or minimum (down) of the sum of metric for group across window, within incoming candidates. Ties do not qualify.',
    'metric_threshold':'At timestamp, metric for group is GE/LE the absolute threshold.',
    'within_entity_metric_compare':'At timestamp and group, this entity\'s metric is GE/LE its other_metric.',
    'metric_difference_threshold':'At timestamp and group, metric MINUS other_metric is GE/LE threshold.',
    'single_metric_cross_entity_extreme':'Unique strict max (up) or min (down) of metric for group at timestamp within incoming candidates; ties do not qualify.',
    'within_entity_group_compare':'At timestamp, this entity\'s metric for group is GE/LE its metric for other_group.',
    'same_group_value_threshold':'At timestamp, metric for group is GE/LE threshold.',
    'group_difference_threshold':'At timestamp, metric for group MINUS the same metric for other_group is GE/LE threshold.',
    'same_group_value_extreme':'Unique strict max (up) or min (down) of metric for group at timestamp within incoming candidates; ties do not qualify.',
}


def semantic_ir(task):
    out=[]
    for n in task['nodes']:
        node={k:n[k] for k in ['id','op','inputs']}
        if n['op']=='ATOM':
            node.update(template=n['template'],params={k:n['params'][k] for k in PARAMETERS[n['template']]})
        else:node['inputs']=sorted(node['inputs'])
        out.append(node)
    return {'nodes':out,'selection':task['selection']}


def coordinate(entity, metric, group, timestamp):
    return '|'.join([entity, metric, group, timestamp])


def condition(atom, entity, scope, get, *, symbolic=False):
    """An atom filters the preceding candidate scope, including scoped extrema."""
    import z3
    p=atom['params'];kind=atom['template']
    if kind not in SUPPORTED: raise ValueError('UNSUPPORTED_ATOM:'+kind)
    m,g,t=p['metric'],p['group'],p['timestamp']
    value=lambda e,mm=m,gg=g,tt=t:get(coordinate(e,mm,gg,tt))
    all_of=(lambda xs:z3.And(*xs)) if symbolic else all
    relation=lambda a,b:(a>=b if p.get('comparison','GE')=='GE' else a<=b)
    if kind in {'timestamp_threshold','metric_threshold','same_group_value_threshold'}:
        return relation(value(entity),p['threshold'])
    if kind=='timestamp_vs_across_entity_agg':
        return relation(len(scope)*value(entity),sum(value(e) for e in scope))
    if kind=='timestamp_vs_within_entity_time_agg':
        return relation(len(p['window'])*value(entity),sum(value(entity,tt=tt) for tt in p['window']))
    if kind in {'two_point_change_threshold','two_point_percentage_change'}:
        before=value(entity,tt=p['start']);after=value(entity,tt=p['end'])
        if kind=='two_point_percentage_change' and not symbolic and before<=0:raise ValueError('NONPOSITIVE_PERCENT_BASE')
        return relation(after-before,p['threshold']) if kind=='two_point_change_threshold' else relation(100*(after-before),p['threshold']*before)
    if kind in {'window_monotone','window_slope_strict_monotone'}:
        observations=[value(entity,tt=tt) for tt in p['window']]
        if kind=='window_slope_strict_monotone': observations=[b-a for a,b in zip(observations,observations[1:])]
        pairs=zip(observations,observations[1:])
        return all_of([b>a if p['direction']=='up' else b<a for a,b in pairs])
    if kind in {'within_entity_metric_compare','metric_difference_threshold'}:
        other=value(entity,mm=p['other_metric'])
        return relation(value(entity),other) if kind=='within_entity_metric_compare' else relation(value(entity)-other,p['threshold'])
    if kind in {'within_entity_group_compare','group_difference_threshold'}:
        other=value(entity,gg=p['other_group'])
        return relation(value(entity),other) if kind=='within_entity_group_compare' else relation(value(entity)-other,p['threshold'])
    if kind in {'timestamp_extreme','window_sum_extreme','single_metric_cross_entity_extreme','same_group_value_extreme'}:
        if kind=='window_sum_extreme':
            score=lambda e:sum(value(e,tt=tt) for tt in p['window'])
        else: score=value
        # Unique extrema: strict against every peer, unlike earlier MVP ties.
        return all_of([score(entity)>score(peer) if p['direction']=='up' else score(entity)<score(peer)
                       for peer in scope if peer!=entity])
    raise ValueError('UNIMPLEMENTED_ATOM')


def execute(task, facts=None):
    facts=task['facts'] if facts is None else facts
    nodes={n['id']:n for n in task['nodes']};state={};active=set()
    def visit(key):
        if key in active:raise ValueError('CYCLE')
        if key in state:return state[key]
        if key not in nodes:raise ValueError('MISSING_INPUT')
        node=nodes[key];active.add(key)
        if node['op']=='ATOM':
            if len(node['inputs'])>1:raise ValueError('ATOM_ARITY')
            scope=visit(node['inputs'][0]) if node['inputs'] else set(task['metadata']['entities'])
            if not scope: result=set()
            else:result={e for e in scope if condition(node,e,sorted(scope),facts.__getitem__)}
        else:
            if node['op'] not in {'AND','OR'} or len(node['inputs'])!=2:raise ValueError('LOGIC_ARITY')
            a,b=[visit(i) for i in node['inputs']]
            result=a&b if node['op']=='AND' else a|b
        state[key]=result;active.remove(key);return result
    visit(task['selection'])
    if set(state)!=set(nodes):raise ValueError('UNREACHABLE_NODE')
    return state


def answers(task, state=None):
    state=execute(task) if state is None else state
    selected=sorted(state[task['selection']]);q=task['average_query']
    if not selected:raise ValueError('EMPTY_AVERAGE')
    total=sum(task['facts'][coordinate(e,q['metric'],q['group'],q['timestamp'])] for e in selected)
    return {'entities':selected,'count':len(selected),'average_exact':str(Fraction(total,len(selected))),
            'average':total/len(selected)}
