"""Regression checks for the stateful benchmark and its handoff."""
from .hotel_graph import read_json
from .hotel_stateful_agent import input_for
from .hotel_stateful_bench import BOOK, PROGRAM, build, grade, start_workbook
from .hotel_stateful_reference import reference_source


def test_stateful_replay_and_fault_controls(tmp_path):
    output = tmp_path / 'benchmark'
    build(output)
    result = read_json(output / 'm3/private/reference_score.json')
    cases = {case['name']: case['steps'] for case in result['cases']}
    assert result['pass']
    assert cases['refund_with_release_blocked'][-1]['reply'] == {'result': 'no_release'}
    assert cases['refund_then_attempt_to_sign'][3]['reply'] == {'result': 'not_reserved'}
    assert cases['sign_then_refund'][3]['reply'] == {'result': 'refunded'}
    assert len(list((output / 'controls').glob('*/score.json'))) == 3
    stage_one_seed = (output / 'm1/public' / PROGRAM).read_text()
    stage_two_seed = (output / 'm2/public' / PROGRAM).read_text()
    assert 'def refund(' not in stage_one_seed and 'def refund(' not in stage_two_seed
    assert 'def pending_release(' not in stage_two_seed


def test_failed_readable_submission_is_carried(tmp_path):
    prior = tmp_path / 'prior'
    prior.mkdir()
    start_workbook(prior / BOOK)
    script = reference_source(0)
    (prior / PROGRAM).write_text(script)
    assert not grade(prior, 1)['pass']
    canonical = tmp_path / 'standard'
    (canonical / 'public').mkdir(parents=True)
    (canonical / 'public/instruction.md').write_text('Stage two')
    (canonical / 'private').mkdir()
    target, source = input_for('continuous', canonical, 2, prior, tmp_path / 'carried')
    assert source == 'actual_predecessor'
    assert (target / 'public' / PROGRAM).read_text() == script
    assert input_for('independent', canonical, 2, prior, tmp_path / 'unused') == (canonical, 'standard')
    assert input_for('continuous', canonical, 3, None, tmp_path / 'unusable') == (None, 'chain_interrupted')
