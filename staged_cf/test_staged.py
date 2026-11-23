from copy import deepcopy
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from .build import build,DEFAULT_PROFILE,DEFAULT_CASE,audit_public,independent_goals
from .session import Session
from .evaluate import evaluate,equal,validate_submission
from .checks import controls


@pytest.fixture(scope='module')
def package(tmp_path_factory):
    p=tmp_path_factory.mktemp('staged-package');build(p);return p


@pytest.fixture(scope='module')
def responses(package):
    return [json.loads((package/f'private/reference_stage{i}.json').read_text()) for i in (1,2)]


def test_known_truth_and_change_mix(package):
    o=json.loads((package/'private/oracle.json').read_text())
    assert o['before']=={'G1':4,'G2':5,'G3':[2],'G4':13,'G5':6,'G6':4,'G7':0,'G8':1}
    assert o['after']=={'G1':9,'G2':0,'G3':[1],'G4':13,'G5':6,'G6':6,'G7':0,'G8':2}
    assert sum(o['changed'].values())==5 and sum(not v for v in o['changed'].values())==3


def test_stage1_source_only_and_no_future_change(package):
    assert audit_public(package)['pass']
    wb=load_workbook(package/'stage1/source.xlsx',data_only=False)
    assert set(wb.sheetnames)=={'event','record','stadium','swimmer'}
    text=(package/'stage1/instruction.md').read_text()
    assert 'Stadium_ID' not in text and 'World Master' not in text and 'changed' not in text
    assert not list((package/'stage1').glob('*stage2*'))
    g=json.loads((package/'stage1/goals.json').read_text())
    assert all(set(x)=={'id','question','type'} for x in g)


def test_audit_rejects_added_answer_file(package,tmp_path):
    import shutil
    copy=tmp_path/'copy';shutil.copytree(package,copy)
    (copy/'stage1/truth.json').write_text('{}')
    assert not audit_public(copy)['pass']


def test_missing_stage1_cannot_reveal(package,tmp_path):
    s=Session(package,tmp_path/'session');s.start()
    with pytest.raises(ValueError,match='committed'):s.reveal()
    assert not (s.root/'stage2').exists()


def test_no_overwrite_locked_stage1(package,responses,tmp_path):
    s=Session(package,tmp_path/'session');s.start();s.commit(1,responses[0])
    with pytest.raises(ValueError,match='closed'):s.commit(1,responses[0])
    s.reveal()
    with pytest.raises(ValueError,match='closed'):s.commit(1,responses[0])


def test_tampered_stage1_detected_before_reveal(package,responses,tmp_path):
    s=Session(package,tmp_path/'session');s.start();s.commit(1,responses[0])
    (s.root/'submission_stage1.json').write_text('{}')
    with pytest.raises(ValueError,match='modified'):s.reveal()


def test_tampered_source_detected(package,responses,tmp_path):
    s=Session(package,tmp_path/'session');s.start()
    (s.root/'stage1/instruction.md').write_text('changed')
    with pytest.raises(ValueError,match='source package changed'):s.commit(1,responses[0])


def test_correct_two_stage_sequence(package,responses,tmp_path):
    s=Session(package,tmp_path/'session');s.start();s.commit(1,responses[0]);text=s.reveal()
    assert 'World Master' in text
    s.commit(2,responses[1]);assert s.verify_locked()
    events=[json.loads(line)['event'] for line in (s.root/'events.jsonl').read_text().splitlines()]
    assert events==['stage1_released','stage1_committed','stage2_released','stage2_committed']
    assert evaluate(package,*responses,tmp_path/'deliver')['pass']


def test_no_stage2_commit_before_release(package,responses,tmp_path):
    s=Session(package,tmp_path/'session');s.start()
    with pytest.raises(ValueError):s.commit(2,responses[1])
    s.commit(1,responses[0])
    with pytest.raises(ValueError):s.commit(2,responses[1])


def test_first_stage_errors_do_not_block_reveal(package,responses,tmp_path):
    wrong=deepcopy(responses[0]);wrong['answers']['G4']['value']=999
    s=Session(package,tmp_path/'session');s.start();s.commit(1,wrong)
    assert 'World Master' in s.reveal()


def test_correction_is_not_invariant_damage(package,responses,tmp_path):
    wrong=deepcopy(responses[0]);wrong['answers']['G4']['value']=999
    r=evaluate(package,wrong,responses[1],tmp_path/'deliver')
    assert not r['pass'] and r['stage2_pass']
    assert r['summary']['self_corrections']==['G4']
    assert r['summary']['correct_invariant_preservation']['correct']==2
    assert r['summary']['correct_invariant_preservation']['total']==2
    g=next(g for g in r['goals'] if g['goal_id']=='G4')
    assert not g['expected_changed'] and g['impact_correct'] and g['preservation_correct'] is None


def test_self_consistency_is_not_truth(package,responses,tmp_path):
    a,b=deepcopy(responses)
    a['answers']['G7']['value']=8;b['answers']['G7']['value']=8
    r=evaluate(package,a,b,tmp_path/'deliver')
    assert not r['pass'] and not r['stage2_pass']
    assert 'G7' in r['summary']['inherited_wrong_answers']


def test_right_results_require_model_source_edit(package,responses,tmp_path):
    b=deepcopy(responses[1]);b['source_edits']=[]
    r=evaluate(package,responses[0],b,tmp_path/'deliver')
    assert r['summary']['stage2_accuracy']['correct']==8
    assert not r['native']['pass'] and not r['pass']


def test_oracle_fault_controls(package,tmp_path):
    assert all(r['expectation_met'] for r in controls(package,tmp_path/'controls').values())


def test_id_sets_unordered_and_counts_not_booleans():
    assert equal([2,1],[1,2],'id_set')
    assert not equal([1,1],[1],'id_set')
    assert not equal(True,1,'count')


def test_independent_sql_catches_shared_bad_truth(package):
    p=package/'private'
    rows=json.loads((p/'before_data.json').read_text());schemas=json.loads((p/'schemas.json').read_text())
    profile=json.loads((p/'profile.json').read_text());deps=json.loads((p/'dependencies.json').read_text())
    o=json.loads((p/'oracle.json').read_text());o['before']['G1']=777
    with pytest.raises(AssertionError):independent_goals(rows,schemas,profile,deps,o['before'])


def test_parametric_second_intervention_and_tie(tmp_path):
    profile=json.loads(DEFAULT_PROFILE.read_text())
    profile['intervention']={'table':'record','row_key':{'ID':3},'column':'Event_ID','value':2}
    profile['stage2_instruction']='模拟更正：成绩记录编号3改归赛事编号2，其余原始记录不变。重新回答同一组目标。'
    # Change only the goal specification, not generator/evaluator source.
    profile['goals'][-1]={'id':'G8','question':'场馆编号10关联多少条成绩记录？','type':'count',
                        'plan':{'source':'record','target':'stadium','aggregate':'count'},'group':10}
    f=tmp_path/'profile.json';f.write_text(json.dumps(profile,ensure_ascii=False))
    output=tmp_path/'new';build(output,profile_path=f)
    o=json.loads((output/'private/oracle.json').read_text())
    assert o['after']['G3']==[1,2] and o['after']['G8']==3
    assert sum(o['changed'].values())==3
    a=json.loads((output/'private/reference_stage1.json').read_text())
    b=json.loads((output/'private/reference_stage2.json').read_text())
    b['answers']['G3']['value'].reverse()
    assert evaluate(output,a,b,tmp_path/'delivered')['pass']


def test_medical_reuse_and_primary_affiliation_rule(tmp_path):
    from medical_cf.upstream import REPO
    profile=Path(__file__).with_name('medical.json')
    package=tmp_path/'medical';summary=build(package,REPO/'dataset/medical/case1',profile)
    assert summary['changed_goals']==3 and summary['unchanged_goals']==5
    o=json.loads((package/'private/oracle.json').read_text())
    assert o['before']=={'G1':5,'G2':3,'G3':[1],'G4':9,'G5':4,'G6':1,'G7':11,'G8':1}
    assert o['after']=={'G1':4,'G2':4,'G3':[1,2],'G4':9,'G5':4,'G6':1,'G7':11,'G8':1}
    a=json.loads((package/'private/reference_stage1.json').read_text())
    b=json.loads((package/'private/reference_stage2.json').read_text())
    assert evaluate(package,a,b,tmp_path/'delivered')['pass']
    assert all(r['expectation_met'] for r in controls(package,tmp_path/'controls').values())
