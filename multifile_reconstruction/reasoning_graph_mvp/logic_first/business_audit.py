"""Explicit controls and shortest-path witnesses; none are LLM generations."""
from copy import deepcopy
from fractions import Fraction
import itertools
import sympy as sp

from .business_definitions import DEFINITIONS,canonical,units
from .business_verify import verify,candidate_expression
from .engine import units_after


def shortest_one_operator(quantities,target):
    given=[q for q in quantities if q['kind']=='base'];accepted=[];tested=0
    zero=[q['id'] for q in given if canonical(q['concept']) in DEFINITIONS and
          sp.cancel(DEFINITIONS[canonical(q['concept'])]['expression']-DEFINITIONS[canonical(target['concept'])]['expression'])==0]
    for a,b,op in itertools.product(given,given,['ADD','SUB','MUL','DIV']):
        try:unit=units_after(op,[units(a['unit']),units(b['unit'])])
        except ValueError:continue
        if unit!=units(target['unit']):continue
        tested+=1;m=deepcopy(target);m['formula']={'op':op,'args':[{'ref':a['id']},{'ref':b['id']}]}
        result=verify({'period':'2025-Q4','entities':[f'Check {i}' for i in range(6)],'metrics':given+[m]},trials=12)
        root=next(r for r in result['metrics'] if r['id']==target['id'])
        if root['accepted']:accepted.append(m['formula'])
    minimum=0 if zero else (1 if accepted else None)
    return {'origin':'deterministic_one_operator_search','candidate_count':tested,'accepted_formulas':accepted,
            'given_target_aliases':zero,'certified_minimum_depth':minimum,'certified_minimum_operator_count':minimum,
            'scope':'A zero/one-operator witness certifies this minimum because no smaller nonnegative count exists. No general completeness claim for deeper targets.'}


def definition_controls(proposal):
    controls=[]
    derived=[m for m in proposal['metrics'] if m['kind']=='derived']
    target=derived[0]['id']
    tests=[]
    bad=deepcopy(proposal);m=next(m for m in bad['metrics'] if m['id']==target)
    m['formula']={'op':'ADD','args':[deepcopy(m['formula']),{'const':1}]}
    tests.append(('wrong_business_result',bad,'REJECTED'))
    bad=deepcopy(proposal);m=next(m for m in bad['metrics'] if m['id']==target)
    m['unit']={'CNY':1} if m['unit']!={'CNY':1} else {}
    tests.append(('wrong_business_unit',bad,'REJECTED'))
    bad=deepcopy(proposal);m=next(m for m in bad['metrics'] if m['id']==target)
    m['formula']={'op':'DIV','args':[{'ref':target},{'const':1}]}
    tests.append(('cyclic_definition',bad,'REJECTED'))
    bad=deepcopy(proposal);m=next(m for m in bad['metrics'] if m['id']==target)
    m['concept']='unregistered_quality_index'
    tests.append(('unknown_definition_is_draft',bad,'DRAFT'))
    for name,candidate,expected in tests:
        report=verify(candidate,trials=12);entry=next((r for r in report['metrics'] if r['id']==target),None)
        actual=entry['status'] if entry else 'SCHEMA_REJECTED'
        controls.append({'case':name,'expected':expected,'actual':actual,'passed':actual==expected,
                         'reason':entry.get('error') if entry else report['errors']})
    return {'passed':all(c['passed'] for c in controls),'cases':controls}


def revpar_boundary_and_necessity(result):
    candidates=[p for p in result['paths'] if p['accepted']]
    common={'room_revenue':Fraction(0),'available_room_nights':Fraction(9200),'sold_room_nights':Fraction(0)}
    boundary=[]
    for path in candidates:
        metrics={m['id']:m for m in path['proposal']['metrics']}
        try:
            actual,_=candidate_expression('revpar',metrics,numeric=common)
            boundary.append({'production_ids':path['production_ids'],'zero_sold_result':str(actual),'defined':True})
        except ValueError as exc:boundary.append({'production_ids':path['production_ids'],'defined':False,'reason':str(exc)})
    base={'room_revenue':460000,'available_room_nights':9200,'sold_room_nights':4600}
    witnesses=[]
    for removed in ['room_revenue','available_room_nights']:
        changed=dict(base);changed[removed]*=2
        first=Fraction(base['room_revenue'],base['available_room_nights'])
        second=Fraction(changed['room_revenue'],changed['available_room_nights'])
        witnesses.append({'missing_input':removed,'world_a':base,'world_b':changed,
                          'same_other_inputs':all(base[k]==changed[k] for k in base if k!=removed),
                          'target_a':str(first),'target_b':str(second),'different_targets':first!=second})
    return {'zero_sold_boundary':boundary,'necessity_counterexamples':witnesses,
            'scope':'Boundary test lies outside the positive-sold common contract. A cancelled denominator can still make unsimplified execution undefined.'}
