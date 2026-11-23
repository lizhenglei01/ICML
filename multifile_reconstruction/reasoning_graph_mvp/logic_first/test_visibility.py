from copy import deepcopy
import pytest

from .engine import execute, depths, validate, GraphError
from .synthesis import generate
from .verify import independent_answers
from .visibility import expand_calculations, shortest_proofs, evidence_ablation, build_pair


@pytest.mark.parametrize('seed',range(20))
def test_identical_graph_truth_and_distinct_verified_depths(seed,tmp_path):
    result=build_pair(tmp_path,generate(seed,2+seed%4),render_charts=False)
    assert result['passed']
    assert all(c['strict_increase'] for c in result['depth_comparison'])
    hashes={v['hidden_graph_sha256'] for v in result['variants'].values()}
    assert hashes=={result['hidden_graph_sha256']}
    for variant in result['variants'].values():
        for target,check in variant['evidence_ablation'].items():
            assert check['sufficient']
            assert all(not c['still_derivable'] for c in check['deletion_checks'])


def test_all_numeric_operators_have_typed_execution_and_zero_denominator_rejected():
    g={'entities':['A','B'],'nodes':[
        {'id':'a','op':'FACT','inputs':[],'unit':{'money':1}},
        {'id':'b','op':'FACT','inputs':[],'unit':{'money':1}},
        {'id':'add','op':'ADD','inputs':['a','b']},
        {'id':'sub','op':'SUB','inputs':['a','b']},
        {'id':'mul','op':'MUL','inputs':['a','b']},
        {'id':'div','op':'DIV','inputs':['a','b']}],
       'facts':{'a':{'A':6,'B':9},'b':{'A':2,'B':3}},'outputs':['add','sub','mul','div']}
    result=execute(g)
    assert result['add']=={'A':8,'B':12} and result['sub']=={'A':4,'B':6}
    assert result['mul']=={'A':12,'B':27} and result['div']=={'A':3,'B':3}
    g['facts']['b']['A']=0
    with pytest.raises(GraphError,match='ZERO_DIVISOR'):execute(g)


def and_chain():
    nodes=[];facts={}
    for i in range(4):
        nodes += [{'id':f'm{i}','op':'FACT','inputs':[],'unit':{'points':1}},
                  {'id':f'p{i}','op':'GE','inputs':[f'm{i}'],'threshold':1,'threshold_unit':{'points':1}}]
        facts[f'm{i}']={'A':2,'B':0}
    nodes += [{'id':'c1','op':'AND','inputs':['p2','p3']},
              {'id':'c2','op':'AND','inputs':['p1','c1']},
              {'id':'root','op':'AND','inputs':['p0','c2']}]
    return {'entities':['A','B'],'nodes':nodes,'facts':facts,'outputs':['root']}


def test_associative_shortcut_corrects_nominal_depth_and_missing_evidence_fails():
    graph=and_chain();assert depths(graph)['root']==4
    proof=shortest_proofs(graph,graph['facts'])['proofs']['root']
    assert proof['minimum_depth']==3 and proof['rule'].endswith('associative')
    assert proof['value']==['A']
    missing=dict(graph['facts']);del missing['m2']
    assert shortest_proofs(graph,missing)['proofs']['root']['minimum_depth'] is None
    # Search must not silently read the omitted value from private graph['facts'].
    leaked=deepcopy(graph);leaked['facts']['m2']={'A':999,'B':999}
    assert shortest_proofs(leaked,missing)['proofs']['root']['minimum_depth'] is None


def test_observed_boolean_shortcuts_and_direct_answer_leak_reported():
    graph=and_chain()
    proof=shortest_proofs(graph,{'p0':set()})['proofs']['root']
    assert proof['minimum_depth']==1 and proof['value']==[]
    report=shortest_proofs(graph,{'root':{'A'}})
    assert report['direct_answer_leaks']==['root'] and report['proofs']['root']['minimum_depth']==0


def test_inverse_arithmetic_route_and_finite_search_termination():
    graph=expand_calculations(generate(0,2));state=execute(graph)
    item=graph['calculation_expansion'][0]
    q,n,d=item['indicator'],item['numerator'],item['denominator']
    proof=shortest_proofs(graph,{q:state[q],d:state[d]},[n])['proofs'][n]
    assert proof['minimum_depth']==1 and proof['value']==state[n]
    # An intermediate can be visible, but no observation may come from a hidden truth loader.
    assert n not in graph['outputs']


def test_stage2_actual_render_readback(tmp_path):
    result=build_pair(tmp_path,generate(2,4))
    assert result['passed']
