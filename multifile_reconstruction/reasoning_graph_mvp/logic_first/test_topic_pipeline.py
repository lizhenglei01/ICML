"""Offline fixtures exercise semantics; they are NEVER substitutes for LLM runs."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from .chart_rules import (CATALOG,SUPPORTED,condition,coordinate,execute,answers,semantic_ir)
from .topic_synthesis import generate_topic_task,shape_signature,graph_depth
from .topic_verify import audit_task,reference_states,negative_controls
from .topic_author import metadata_request,validate_metadata,writer_spec,compare_extracted


def metadata_fixture():
    return {'topic_zh':'离线测试主题','topic_en':'Offline test fixture',
            'entity_type_zh':'测试对象','entity_type_en':'test sites',
            'entities':[f'Fictional Test Site {i}' for i in range(10)],
            'unit':{'id':'orders','zh':'工单','en':'work orders'},
            'metrics':[{'id':f'metric_{i}','zh':f'测试工单{i}','en':f'test work orders {i}','meaning':'independent count'} for i in range(8)],
            'groups':[{'id':f'group_{i}','zh':f'测试组{i}','en':f'test group {i}'} for i in range(3)],
            'timestamps':['2025-Q1','2025-Q2','2025-Q3','2025-Q4']}


def atom_fixture(template):
    times=['Q1','Q2','Q3','Q4'];entities=['A','B','C'];facts={}
    series={'A':[10,20,40,80],'B':[80,60,50,30],'C':[15,30,45,60]}
    for e in entities:
        for m in ['m1','m2']:
            for g in ['g1','g2']:
                for t in times:facts[coordinate(e,m,g,t)]=5
        for t,v in zip(times,series[e]):facts[coordinate(e,'m1','g1',t)]=v
    for e,v in zip(entities,[90,20,70]):facts[coordinate(e,'m2','g1','Q4')]=v
    for e,v in zip(entities,[70,40,60]):facts[coordinate(e,'m1','g2','Q4')]=v
    p={'metric':'m1','other_metric':'m2','group':'g1','other_group':'g2','timestamp':'Q4',
       'window':times,'start':'Q1','end':'Q4','comparison':'GE','direction':'up','threshold':40}
    if template in {'two_point_change_threshold','two_point_percentage_change'}:p['threshold']=20
    if template in {'metric_difference_threshold','group_difference_threshold'}:p['threshold']=10
    atom={'id':'result','op':'ATOM','inputs':[],'template':template,'params':p}
    return atom,entities,facts


EXPECTED={
    'timestamp_threshold':{'A','C'},'metric_threshold':{'A','C'},'same_group_value_threshold':{'A','C'},
    'timestamp_vs_across_entity_agg':{'A','C'},'timestamp_vs_within_entity_time_agg':{'A','C'},
    'two_point_change_threshold':{'A','C'},'two_point_percentage_change':{'A','C'},
    'window_monotone':{'A','C'},'window_slope_strict_monotone':{'A'},
    'timestamp_extreme':{'A'},'window_sum_extreme':{'B'},
    'within_entity_metric_compare':{'B'},'metric_difference_threshold':{'B'},
    'single_metric_cross_entity_extreme':{'A'},'within_entity_group_compare':{'A','C'},
    'group_difference_threshold':{'A'},'same_group_value_extreme':{'A'},
}


@pytest.mark.parametrize('template',sorted(SUPPORTED))
def test_all_supported_templates_against_hand_computed_fixture(template):
    atom,entities,facts=atom_fixture(template)
    actual={e for e in entities if condition(atom,e,entities,facts.__getitem__)}
    assert actual==EXPECTED[template]
    task={'nodes':[atom],'metadata':{'entities':entities},'facts':facts,'selection':'result'}
    assert reference_states(task)['result']==EXPECTED[template]


def test_scope_changes_mean_and_extreme_and_percentage_requires_positive_base():
    atom,entities,facts=atom_fixture('timestamp_vs_across_entity_agg')
    assert condition(atom,'C',entities,facts.__getitem__)
    assert not condition(atom,'C',['A','C'],facts.__getitem__)
    atom['template']='timestamp_extreme'
    assert condition(atom,'C',['B','C'],facts.__getitem__)
    assert not condition(atom,'C',entities,facts.__getitem__)
    atom['template']='two_point_percentage_change';facts[coordinate('A','m1','g1','Q1')]=0
    with pytest.raises(ValueError,match='BASE'):condition(atom,'A',entities,facts.__getitem__)


@pytest.mark.parametrize('seed',range(20))
def test_generated_scoped_trees_smt_data_and_independent_oracle(seed):
    task=generate_topic_task(metadata_fixture(),seed,2+seed%4,chart_count=3+seed%2)
    assert audit_task(task)['passed']
    assert negative_controls(task)['passed']
    assert graph_depth(task)==2+seed%4
    assert 0<answers(task)['count']<10


def test_shapes_include_real_chains_branches_and_mixed_graphs():
    tasks=[generate_topic_task(metadata_fixture(),seed,2+seed%4) for seed in range(20)]
    shapes={shape_signature(task) for task in tasks}
    assert len(shapes)>=12
    assert any('ATOM(ATOM(' in s for s in shapes)
    assert any('OR(' in s and 'AND(' in s for s in shapes)
    assert len(CATALOG)==27 and len(SUPPORTED)==17
    assert all(n['op'] in {'ATOM','AND','OR'} for t in tasks for n in t['nodes'])


def test_topic_prompt_requests_generated_metrics_and_writer_is_answer_blind():
    request=metadata_request('河港物资运营')
    user=json.loads(request['messages'][1]['content'])
    assert user['topic']=='河港物资运营'
    assert not {'service_index','guest_score'} & set(user)
    task=generate_topic_task(metadata_fixture(),3,5)
    spec=writer_spec(task,'example')
    assert 'entities' not in spec['metadata']
    assert not {'facts','intended_sets','answers','truth'} & set(spec)
    text=json.dumps(spec,ensure_ascii=False)
    assert all(entity not in text for entity in task['metadata']['entities'])


def test_roundtrip_comparator_accepts_only_equivalent_aliases_and_exact_relations():
    task=generate_topic_task(metadata_fixture(),0,2)
    ir=semantic_ir(task)
    assert compare_extracted(ir,ir)['passed']
    bad=deepcopy(ir);bad['nodes'][0]['params']['invented_criterion']=10
    assert not compare_extracted(ir,bad)['passed']
    bad=deepcopy(ir)
    boolean=next(n for n in bad['nodes'] if n['op'] in {'AND','OR'})
    boolean['op']='AND' if boolean['op']=='OR' else 'OR'
    assert not compare_extracted(ir,bad)['passed']


def test_metadata_rejects_duplicate_names_and_incompatible_structure():
    meta=metadata_fixture();validate_metadata(meta)
    meta['entities'][1]=meta['entities'][0]
    with pytest.raises(ValueError):validate_metadata(meta)


def test_resume_accepted_batch_does_not_reissue_a_model_call(tmp_path,monkeypatch):
    from . import topic_run
    from .topic_author import narrative_request
    task=generate_topic_task(metadata_fixture(),0,2)
    taskdir=tmp_path/'task';private=taskdir/'private';private.mkdir(parents=True)
    spec=writer_spec(task,'fixture')
    # Transport/cache fixture only; this text is not advertised as LLM authoring.
    doc={'task_id':'fixture','title_zh':'缓存测试','title_en':'Cache fixture','intro_zh':'测试','intro_en':'Fixture',
         'sections':[{'node_id':n['id'],'name_zh':f'集合{i}','name_en':f'Pool {i}',
                      'text_zh':'离线缓存测试。','text_en':'Offline cache test.'} for i,n in enumerate(task['nodes'])]}
    writer=tmp_path/'writer';auditor=tmp_path/'auditor'
    writer.mkdir();auditor.mkdir()
    topic_run.save(writer/'request.json',narrative_request([spec]))
    topic_run.save(auditor/'response.txt',{'tasks':[{'task_id':'fixture','ir':spec['rules'],'bilingual_consistent':True,'issues':[]}]})
    topic_run.save(private/'writer_input.json',spec);topic_run.save(private/'document.json',doc)
    topic_run.save(private/'language_audit.json',{'writer_call':{'transport_audit':str(writer/'transport/transport_audit.json')},
                                              'auditor_call':{'transport_audit':str(auditor/'transport/transport_audit.json')}})
    batch=tmp_path/'batch';topic_run.save(batch/'result.json',{'passed':True,'history':[]})
    def forbidden(*args,**kwargs):raise AssertionError('Cache resume called a model')
    monkeypatch.setattr(topic_run,'call_author',forbidden)
    result=topic_run.author_batch([('fixture',task,taskdir)],batch,reuse=True)
    assert result['passed'] and result['reused_accepted_batch'] and result['task_ids']==['fixture']
