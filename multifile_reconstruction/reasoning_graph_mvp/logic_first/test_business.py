"""Offline fixtures only; actual model runs are logged separately."""
from copy import deepcopy
from fractions import Fraction
import pytest

from .business_verify import verify,candidate_expression
from .business_hybrid import build_hybrid,negative_submissions
from .business_goal import paths,known_revpar,goal_quantity
from .business_audit import shortest_one_operator,definition_controls,revpar_boundary_and_necessity
from .business_definitions import units
from .business_view import render_hybrid


def formula(op,*args):
    return {'op':op,'args':[{'ref':a} if isinstance(a,str) else a for a in args]}


def fixture():
    metrics=known_revpar()
    for identifier,concept,zh,en,unit,expr in [
        ('occ','occupancy_rate','入住率','Occupancy rate',{},formula('DIV','sold_room_nights','available_room_nights')),
        ('adr','average_daily_rate','平均房价','Average daily rate',{'CNY':1,'room_night':-1},formula('DIV','room_revenue','sold_room_nights')),
        ('revpar','revpar','每可售间夜收入','RevPAR',{'CNY':1,'room_night':-1},formula('MUL','adr','occ'))]:
        metrics.append({'id':identifier,'concept':concept,'kind':'derived','zh':zh,'en':en,'meaning':'Offline test fixture',
                        'unit':unit,'formula':expr})
    return {'period':'2025-Q4','topic_zh':'离线酒店测试','topic_en':'Offline hotel fixture',
            'entities':[f'Fixture Hotel {i}' for i in range(6)],'metrics':metrics}


def target_fixture():
    base=known_revpar();proposal=fixture()
    quantities=base+[dict((k,v) for k,v in m.items() if k!='formula') for m in proposal['metrics'] if m['kind']=='derived']
    productions=[{'id':'direct','output':'revpar','formula':formula('DIV','room_revenue','available_room_nights')},
                 {'id':'compound','output':'revpar','formula':formula('MUL','adr','occ')},
                 {'id':'adr_definition','output':'adr','formula':formula('DIV','room_revenue','sold_room_nights')},
                 {'id':'occ_definition','output':'occ','formula':formula('DIV','sold_room_nights','available_room_nights')}]
    return quantities,productions


def test_formula_units_semantics_and_independent_perturbations():
    report=verify(fixture())
    assert report['passed'] and len(report['accepted_derived'])==3
    assert all(m['status']=='VERIFIED' and len(m['perturbations'])==36 for m in report['metrics'])
    assert units({'room_night':1})=={'room':1,'day':1}
    assert units({'CNY':1,'room_night':-1})=={'CNY':1,'room':-1,'day':-1}


@pytest.mark.parametrize('change,code',[
    ('reverse_ratio','BUSINESS_FORMULA_MISMATCH'),('wrong_unit','BUSINESS_UNIT_MISMATCH'),
    ('cycle','CYCLE'),('zero_divisor','ZERO_DIVISOR'),('label','BUSINESS_LABEL_MISMATCH')])
def test_invalid_formulas_are_rejected_for_the_right_reason(change,code):
    p=fixture();m=p['metrics'][3]
    if change=='reverse_ratio':m['formula']=formula('DIV','available_room_nights','sold_room_nights')
    elif change=='wrong_unit':m['unit']={'CNY':1}
    elif change=='cycle':m['formula']=formula('DIV','occ',{'const':1})
    elif change=='zero_divisor':m['formula']=formula('DIV','sold_room_nights',formula('SUB','available_room_nights','available_room_nights'))
    elif change=='label':m.update(zh='库存周转率',en='Inventory turnover')
    report=verify(p);entry=next(r for r in report['metrics'] if r['id']=='occ')
    assert not entry['accepted'] and entry['status']=='REJECTED' and entry['error']==code


def test_unknown_semantics_are_drafts_not_endorsed_by_a_correct_number():
    p=fixture();p['metrics'][3]['concept']='invented_guest_happiness_rate'
    report=verify(p);by={r['id']:r for r in report['metrics']}
    assert by['occ']['status']=='DRAFT' and not by['occ']['accepted']
    assert not by['revpar']['accepted']  # dependency cannot bypass the gate


def test_target_alternative_paths_have_different_cost_and_same_minimal_support():
    quantities,productions=target_fixture();routes=paths(quantities,productions)
    assert len(routes)==2 and all(r['accepted'] for r in routes)
    assert {(r['operator_count'],r['dependency_depth']) for r in routes}=={(1,1),(3,2)}
    assert all(r['simplified_sufficient_inputs']==['available_room_nights','room_revenue'] for r in routes)
    longer=next(r for r in routes if r['operator_count']==3)
    assert longer['algebraically_cancelled_inputs']==['sold_room_nights']
    minimum=shortest_one_operator(quantities,goal_quantity())
    assert minimum['certified_minimum_depth']==1 and len(minimum['accepted_formulas'])==1


def test_an_unresolved_or_cyclic_alternative_does_not_block_a_closed_route():
    q,p=target_fixture()
    # Do not require unrelated/unresolved productions to enumerate direct paths.
    p=[x for x in p if x['id']!='adr_definition']
    result=paths(q,p)
    assert len(result)==1 and result[0]['accepted'] and result[0]['production_ids']==['direct']
    p.append({'id':'cyclic_adr','output':'adr','formula':formula('DIV','revpar','occ')})
    result=paths(q,p)
    assert len(result)==1 and result[0]['production_ids']==['direct']


def test_cancelled_denominator_still_changes_executability_at_zero():
    quantities,productions=target_fixture();result={'paths':paths(quantities,productions)}
    audit=revpar_boundary_and_necessity(result)
    assert {r['defined'] for r in audit['zero_sold_boundary']}=={True,False}
    assert all(r['same_other_inputs'] and r['different_targets'] for r in audit['necessity_counterexamples'])


@pytest.mark.parametrize('seed',range(10))
def test_mixed_unit_business_formulas_feed_logical_selection(seed):
    p=fixture();task=build_hybrid(p,verify(p),seed)
    assert task['truth']['entities'] and 0<task['truth']['count']<6
    assert all(c['passed'] for c in task['independent_numeric_checks'])
    assert negative_submissions(task)['passed']
    assert len({tuple(sorted(m['unit'].items())) for m in task['profile']['metrics']})>=3


def test_private_diagram_and_public_base_only_report(tmp_path):
    p=fixture();task=build_hybrid(p,verify(p),0)
    result=render_hybrid(tmp_path,task)
    assert result['passed']
    assert (tmp_path/'private/dependency_graph.png').read_bytes().startswith(b'\x89PNG')
    assert not (tmp_path/'public/dependency_graph.png').exists()
    assert set(path.name for path in (tmp_path/'public').iterdir())=={'report.html'}


def test_controls_distinguish_wrong_formula_from_unregistered_definition():
    controls=definition_controls(fixture())
    assert controls['passed']
    assert next(c for c in controls['cases'] if c['case']=='unknown_definition_is_draft')['actual']=='DRAFT'


def test_goal_mode_accepts_an_input_target_other_than_revpar(tmp_path,monkeypatch):
    from . import business_goal
    known=[{'id':'profit','concept':'gross_profit','kind':'base','zh':'毛利','en':'Gross profit','meaning':'已知同季度毛利','unit':{'CNY':1}},
           {'id':'sales','concept':'net_sales','kind':'base','zh':'净销售额','en':'Net sales','meaning':'已知同季度净额','unit':{'CNY':1}}]
    target={'id':'margin','concept':'gross_profit_margin','kind':'derived','zh':'毛利率','en':'Gross profit margin','meaning':'同季度毛利率','unit':{}}
    def fake_call(request,folder):
        payload=__import__('json').loads(request['messages'][1]['content'])
        assert payload['expand_goals']==['margin']
        return {'new_quantities':[],'productions':[{'id':'ratio','output':'margin','formula':formula('DIV','profit','sales')}],
                'expand_next':[]},{'origin':'offline_test_fixture_not_LLM'}
    monkeypatch.setattr(business_goal,'call_business',fake_call)
    result=business_goal.run_backward(tmp_path,reuse=False,goal_spec={'known':known,'target':target,'desired_paths':1})
    assert result['passed'] and result['target']['id']=='margin' and len(result['paths'])==1


def test_initial_author_requests_do_not_contain_reference_formulas_or_intermediate_names():
    from .business_author import proposal_request
    from .business_goal import backward_request
    import json
    initial=backward_request(known_revpar()+[goal_quantity()],[],['revpar'],{'status':'START'})
    text=json.dumps(initial)
    assert 'average_daily_rate' not in text and 'occupancy_rate' not in text
    request=proposal_request('酒店经营')
    payload=json.loads(request['messages'][1]['content'])
    assert payload['topic']=='酒店经营'
    assert not any(k in payload for k in ['reference_formula','expected_expression','definitions_registry'])


def test_incompatible_declared_period_is_not_silently_used_in_a_ratio():
    p=fixture();p['metrics'][2]['meaning']='2025-Q3 available room nights'
    report=verify(p)
    record=next(r for r in report['metrics'] if r['id']=='sold_room_nights')
    assert not record['accepted'] and record['error']=='BUSINESS_PERIOD_MISMATCH'
