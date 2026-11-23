"""Independent dimensional, semantic and perturbation acceptance gate."""
from fractions import Fraction
import re
import sympy as sp

from .business_definitions import DEFINITIONS,SOURCES,canonical,units
from .business_data import worlds
from .engine import units_after


class CandidateError(ValueError):
    pass


def validate_schema(proposal):
    if proposal.get('period')!='2025-Q4':raise CandidateError('REPORTING_PERIOD')
    metrics=proposal.get('metrics',[]);ids=[m['id'] for m in metrics]
    if not metrics or len(ids)!=len(set(ids)):raise CandidateError('DUPLICATE_METRIC')
    if len(metrics)>20:raise CandidateError('GRAPH_SIZE_LIMIT')
    for m in metrics:
        if not re.fullmatch('[a-z][a-z0-9_]*',m['id']):raise CandidateError('METRIC_ID')
        if m['kind'] not in {'base','derived'}:raise CandidateError('METRIC_KIND')
        if any(not isinstance(m.get(k),str) or not m[k].strip() for k in ['concept','zh','en','meaning']):raise CandidateError('METRIC_MEANING')
        if (m['kind']=='derived')!=('formula' in m):raise CandidateError('FORMULA_KIND')
        units(m['unit'])
    entities=proposal.get('entities',[])
    if len(entities)!=6 or len(set(entities))!=6:raise CandidateError('ENTITY_SCHEMA')
    roles=[canonical(m['concept']) for m in metrics if canonical(m['concept'])]
    if len(roles)!=len(set(roles)):raise CandidateError('DUPLICATE_BUSINESS_CONCEPT')
    return {m['id']:m for m in metrics}


def candidate_expression(metric_id,metrics,*,numeric=None,cache=None,active=None):
    cache={} if cache is None else cache;active=set() if active is None else active
    if metric_id in active:raise CandidateError('CYCLE')
    if metric_id in cache:return cache[metric_id]
    if metric_id not in metrics:raise CandidateError('MISSING_INPUT')
    metric=metrics[metric_id];declared=units(metric['unit'])
    active.add(metric_id)
    if metric['kind']=='base':
        role=canonical(metric['concept'])
        if role not in DEFINITIONS:raise CandidateError('UNCONFIRMED_DEFINITION')
        value=DEFINITIONS[role]['expression'] if numeric is None else numeric[metric_id]
        result=value,declared
    else:
        result=expression(metric['formula'],metrics,numeric=numeric,cache=cache,active=active)
        if result[1]!=declared:raise CandidateError('DECLARED_UNIT_MISMATCH')
    active.remove(metric_id);cache[metric_id]=result;return result


def expression(tree,metrics,*,numeric=None,cache=None,active=None):
    if not isinstance(tree,dict):raise CandidateError('EXPRESSION_SCHEMA')
    if set(tree)=={'ref'}:
        return candidate_expression(tree['ref'],metrics,numeric=numeric,cache=cache,active=active)
    if set(tree)=={'const'}:
        if type(tree['const'])is not int or tree['const']!=1:raise CandidateError('UNSUPPORTED_CONSTANT')
        return (sp.Integer(1) if numeric is None else Fraction(1)),{}
    if set(tree)!={'op','args'} or tree['op'] not in {'ADD','SUB','MUL','DIV'}:raise CandidateError('OPERATOR_SCHEMA')
    if not isinstance(tree['args'],list) or len(tree['args'])!=2:raise CandidateError('ARITY')
    left,right=[expression(t,metrics,numeric=numeric,cache=cache,active=active) for t in tree['args']]
    try:outunit=units_after(tree['op'],[left[1],right[1]])
    except ValueError as exc:raise CandidateError('UNIT_MISMATCH') from exc
    a,b=left[0],right[0]
    if tree['op']=='ADD':value=a+b
    elif tree['op']=='SUB':value=a-b
    elif tree['op']=='MUL':value=a*b
    else:
        if b==0:raise CandidateError('ZERO_DIVISOR')
        value=a/b
    return value,outunit


def definition_gate(metric):
    role=canonical(metric['concept'])
    if role not in DEFINITIONS:raise CandidateError('UNCONFIRMED_DEFINITION')
    expected=DEFINITIONS[role]
    if units(metric['unit'])!=expected['unit']:raise CandidateError('BUSINESS_UNIT_MISMATCH')
    # Reject obvious concept/label mismatches instead of trusting a model's tag.
    label=(metric['zh']+' '+metric['en']).lower()
    required={
        'occupancy_rate':['occupancy','入住率'],
        'average_daily_rate':['average daily rate','adr','平均房价'],
        'revpar':['revpar','revenue per available','每间可售','每可售'],
        'gross_profit':['gross profit','毛利'],
        'gross_profit_margin':['gross margin','gross profit margin','毛利率'],
    }.get(role)
    if required and not any(token in label for token in required):raise CandidateError('BUSINESS_LABEL_MISMATCH')
    periods=set(re.findall(r'20\d{2}-Q[1-4]',metric['meaning']))
    historical=role in {'prior_net_sales_revenue','net_sales_growth_rate'}
    if not historical and periods-{'2025-Q4'}:raise CandidateError('BUSINESS_PERIOD_MISMATCH')
    if role=='prior_net_sales_revenue' and (periods-{'2024-Q4'} or '上季度' in metric['meaning']):raise CandidateError('BUSINESS_PERIOD_MISMATCH')
    if role=='room_revenue' and any(token in metric['meaning'].lower() for token in ['含餐饮','including food','including f&b']):raise CandidateError('BUSINESS_SCOPE_MISMATCH')
    return role,expected


def verify(proposal,trials=36):
    report={'passed':False,'metrics':[],'errors':[],'trial_count':trials,
            'validation_domain':'Same entity/quarter; positive available/sold room-nights, positive room revenue and net sales, nonnegative returns below gross receipts, positive units and prior net sales. Zero-denominator cases are outside this common domain and tested separately.',
            'scope':'Private registered definitions + exact symbolic equivalence + business-consistent numeric trials. Not discovery/proof of arbitrary business rules.'}
    try:metrics=validate_schema(proposal)
    except (KeyError,TypeError,ValueError) as exc:
        report['errors'].append(str(exc));return report
    for metric in proposal['metrics']:
        entry={'id':metric['id'],'kind':metric['kind'],'concept':metric['concept'],'checks':[]}
        try:
            role,expected=definition_gate(metric)
            value,unit=candidate_expression(metric['id'],metrics)
            if sp.cancel(value-expected['expression'])!=0:raise CandidateError('BUSINESS_FORMULA_MISMATCH')
            entry.update(canonical_concept=role,source_id=expected['source'],source=SOURCES[expected['source']],
                         symbolic_expression=str(value),reference_expression=str(expected['expression']),
                         checks=[{'name':'units','passed':True},{'name':'registered_business_definition','passed':True},
                                 {'name':'symbolic_equivalence','passed':True}])
            trials_result=[]
            for i,world in enumerate(worlds(719,trials)):
                given={k:world['roles'][canonical(m['concept'])] for k,m in metrics.items() if m['kind']=='base' and canonical(m['concept']) in world['roles']}
                actual,_=candidate_expression(metric['id'],metrics,numeric=given)
                expected_number=world['roles'][role]
                ok=actual==expected_number
                trials_result.append({'trial':i,'passed':ok,'actual':str(actual),'expected':str(expected_number)})
                if not ok:raise CandidateError('PERTURBATION_FAILURE')
            entry['perturbations']=trials_result
            entry['status']='VERIFIED';entry['accepted']=True
        except (KeyError,TypeError,ValueError,ZeroDivisionError) as exc:
            code=str(exc)
            entry.update(status='DRAFT' if 'UNCONFIRMED_DEFINITION' in code else 'REJECTED',accepted=False,error=code)
        report['metrics'].append(entry)
    # An otherwise correct formula cannot depend on a rejected/draft component.
    accepted={r['id'] for r in report['metrics'] if r['accepted']}
    def references(t):
        if 'ref' in t:return {t['ref']}
        return set().union(*(references(c) for c in t.get('args',[])))
    changed=True
    while changed:
        changed=False
        for entry in report['metrics']:
            metric=metrics[entry['id']]
            if entry['accepted'] and metric['kind']=='derived' and not references(metric['formula'])<=accepted:
                entry.update(accepted=False,status='REJECTED',error='UNVERIFIED_DEPENDENCY')
                accepted.remove(entry['id']);changed=True
    report['accepted_ids']=sorted(accepted)
    report['accepted_derived']=[m['id'] for m in proposal['metrics'] if m['kind']=='derived' and m['id'] in accepted]
    report['draft_ids']=[r['id'] for r in report['metrics'] if r['status']=='DRAFT']
    report['passed']=bool(report['accepted_derived'])
    return report


def project_verified(proposal,report):
    from copy import deepcopy
    result=deepcopy(proposal)
    result['metrics']=[m for m in result['metrics'] if m['id'] in report['accepted_ids']]
    return result
