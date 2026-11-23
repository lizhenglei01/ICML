from copy import deepcopy
import json
import random
import pytest

from .engine import execute, validate, GraphError
from .synthesis import generate, synthesize_chart, default_metadata
from .verify import audit_graph, independent_answers, compare_answers
from .render import render_bundle, audit_public, narrative_spec, write_narrative, audit_rule_semantics


@pytest.mark.parametrize("seed",range(20))
def test_twenty_seeded_trees_constraints_and_independent_answers(seed):
    graph = generate(seed, 2+seed%4)
    state = execute(graph)
    assert audit_graph(graph,state)["passed"]
    assert compare_answers(state,independent_answers(graph))["passed"]
    assert graph == generate(seed,2+seed%4)


def test_coverage_metadata_generalization_and_no_fixed_answer():
    graphs=[generate(i,2+i%4) for i in range(20)]
    ops={n['op'] for g in graphs for n in g['nodes']}
    assert {'GE','LE','MAX','MIN','AND','OR','COUNT','AVG'} <= ops
    assert len({tuple(independent_answers(g)[g['selection']]) for g in graphs})>10
    meta=default_metadata();meta['entities']=[f'Clinic {i}' for i in range(8)]
    meta['topic']='诊所 / Clinics'
    for metric in meta['metrics']: metric.update(min=10,max=250)
    g=generate(7,5,meta)
    assert audit_graph(g,execute(g))['passed']


def test_saved_metadata_and_seed_replay_exactly_without_mutating_caller():
    meta=default_metadata();before=deepcopy(meta)
    graph=generate(7,5,meta)
    assert meta==before
    assert generate(graph['seed'],graph['requested_depth'],graph['metadata'])==graph


def test_multi_constraint_chart_and_unsatisfiable_case():
    metric={'min':0,'max':100}
    req=[{'op':'GE','threshold':30,'members':{'A','B'}}, {'op':'LE','threshold':70,'members':{'A','C'}}]
    values=synthesize_chart(['A','B','C'],req,metric,random.Random(2))
    assert 30<=values['A']<=70 and values['B']>70 and values['C']<30
    req.append({'op':'GE','threshold':90,'members':{'A'}})
    with pytest.raises(ValueError,match='UNSAT'): synthesize_chart(['A','B','C'],req,metric,random.Random(2))


def test_tied_extrema_and_boundary_thresholds_are_not_arbitrary():
    g=generate(2,4)
    extrema=[n for n in g['nodes'] if n['op'] in {'MAX','MIN'}]
    assert extrema
    values=execute(g)
    for n in extrema: assert len(values[n['id']])>=1
    predicate=next(n for n in g['nodes'] if n['op'] in {'GE','LE'})
    metric=predicate['inputs'][0]
    g['facts'][metric][g['entities'][0]]=predicate['threshold']
    assert g['entities'][0] in execute(g)[predicate['id']]


def test_cycles_types_units_and_missing_inputs_rejected():
    graph=generate(0,2)
    root=next(n for n in graph['nodes'] if n['id']==graph['selection'])
    root['inputs'][0]=root['id']
    with pytest.raises(GraphError,match='CYCLE'):validate(graph)
    graph=generate(0,2)
    root=next(n for n in graph['nodes'] if n['id']==graph['selection'])
    root['inputs'][0]=next(iter(graph['facts']))
    with pytest.raises(GraphError,match='TYPE'):validate(graph)
    graph=generate(0,2)
    n=next(n for n in graph['nodes'] if n['op'] in {'GE','LE'})
    n['threshold_unit']={'currency':1}
    with pytest.raises(GraphError,match='UNIT'):validate(graph)


def test_rendered_evidence_and_corruptions(tmp_path):
    graph=generate(1,3)
    mapping=render_bundle(tmp_path,graph)
    assert audit_public(tmp_path,graph,mapping)['passed']
    assert not ({'facts','constraints','entities','answers'} & set(narrative_spec(graph)))
    first=next(e for e in mapping['evidence'] if e['kind']=='chart')
    path=tmp_path/first['location']
    original=path.read_text()
    # Break real geometry while retaining all values and labels.
    import re
    changed=re.sub(r'(id="patch_3"[^>]*>\s*<path d=")[^"]+',r'\g<1>M 0 0 L 1 0 L 1 1 L 0 1 z',original,count=1)
    assert changed!=original
    path.write_text(changed)
    assert not audit_public(tmp_path,graph,mapping)['passed']
    path.write_text(original)
    rules=tmp_path/'rules.txt';rules.write_text(rules.read_text().replace(mapping['narrative']['rules'][0]['text'],''))
    assert not audit_public(tmp_path,graph,mapping)['passed']


def test_no_answer_file_in_public(tmp_path):
    graph=generate(0,2);mapping=render_bundle(tmp_path,graph,render_charts=False)
    (tmp_path/'answers.json').write_text('{}')
    assert not audit_public(tmp_path,graph,mapping,charts=False)['passed']


def test_independent_rule_parser_rejects_wrong_boolean_wording_and_threshold():
    graph=generate(1,3);spec=narrative_spec(graph);narrative=write_narrative(spec)
    assert all(c['passed'] for c in audit_rule_semantics(spec,narrative['rules']))
    changed=deepcopy(narrative['rules'])
    boolean=next(r for r in changed if 'Satisfy both' in r['text'] or 'Satisfy at least one' in r['text'])
    boolean['text']=boolean['text'].replace('Satisfy both','Satisfy either').replace('inclusive OR','exclusive OR')
    assert not all(c['passed'] for c in audit_rule_semantics(spec,changed))
