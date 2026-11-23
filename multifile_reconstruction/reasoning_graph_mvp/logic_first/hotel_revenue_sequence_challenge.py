"""Test one seeded stage-one omission in a fresh official stage-two session."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from shutil import copytree

from alignment_probe.codex_transport import configured_route
from pptx import Presentation

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_office import FILES, read_observations, set_presentation_value, snapshot, structural_key
from .hotel_revenue_sequence import DEFAULT
from .hotel_revenue_sequence_agent import CARRY, EFFORT, MODEL, STAGE_ONE, TEACHER, freeze_carry


CHALLENGE = 'stage_02_seeded_omission'
HOTEL = 'Willowbank Hotel'


def omission_binding(oracle):
    binding, = [row for row in oracle['bindings'] if row.get('entity') == HOTEL
                and row['semantic_node_id'] == 'qualified' and row['location']['kind'] == 'table']
    check, = [row for row in oracle['evidence_checks'] if row['evidence_id'] == binding['evidence_id']]
    return binding, check


def seed_omission(original, submission, first_oracle, second_oracle):
    """Revert exactly one required stage-one PPT table flag to its stale value."""
    binding, check = omission_binding(first_oracle)
    following_binding, following_check = omission_binding(second_oracle)
    if (binding != following_binding or check['before'] is not False or check['after'] is not True
            or following_check['before'] is not True or following_check['after'] is not True
            or check['group'] != 'required' or following_check['group'] != 'protected'):
        raise ValueError('The seed must be a stage-one change and a stage-two semantic invariant')
    if not hotel_agent.grade(original, first_oracle)['pass']:
        raise ValueError('The source submission does not pass the stage-one Oracle')

    copytree(original, submission)
    path = submission / binding['file']
    deck = Presentation(path)
    loc = binding['location']
    cell = deck.slides[loc['slide']].shapes[loc['shape']].table.cell(loc['row'], loc['column'])
    set_presentation_value(cell, binding, check['before'])
    deck.save(path)
    seed, = read_observations(submission, [binding])
    if seed['value'] is not False:
        raise ValueError('The native omission did not survive readback')
    before, after = (snapshot(directory, hotel_agent.OFFICE_FILES) for directory in (original, submission))
    modified = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    expected_object, = structural_key(original, binding)
    expected_failures = {expected_object, binding['evidence_id']}
    score = hotel_agent.grade(submission, first_oracle)
    failures = {item['object'] for item in score['checks'] if not item['pass']}
    if modified != {expected_object} or failures != expected_failures:
        raise ValueError('The omission changed unrelated native objects or introduced unrelated failures')
    return {'hotel': HOTEL, 'file': binding['file'], 'file_location': loc,
            'native_object': expected_object, 'evidence_id': binding['evidence_id'],
            'stage_one_seeded_value': seed['value'], 'stage_one_correct_value': check['after'],
            'stage_two_correct_value': following_check['after'], 'stage_two_evidence_group': following_check['group'],
            'stage_one_seed_grade': score['groups'], 'stage_one_seed_failed_checks': sorted(failures)}


def verify_baselines(source, existing):
    packages = {STAGE_ONE: source / 'stage_01', TEACHER: source / 'stage_02'}
    frozen = hotel_agent.freeze_packages(packages, original_oracle=True)
    prior_plan = read_json(existing / 'plan.json')
    prior_results = read_json(existing / 'results.json')
    if prior_plan['model'] != MODEL or prior_plan['effort'] != EFFORT:
        raise ValueError('Baseline model and effort differ from the seeded challenge')
    for name in (STAGE_ONE, TEACHER, CARRY):
        record = prior_results[name]
        if record['status'] != 'COMPLETED' or not record['native_pass']:
            raise ValueError(f'The prior {name} session must have passed')
        expected = frozen[STAGE_ONE if name == STAGE_ONE else TEACHER]
        baseline = prior_plan['layouts'][name]
        if baseline['oracle_sha256'] != expected['oracle_sha256'] or baseline['reference_sha256'] != expected['reference_sha256']:
            raise ValueError(f'The {name} Oracle or reference differs from the frozen baseline')
        if hotel_agent.file_hashes(existing / name / 'submission', hotel_agent.OFFICE_FILES) != record['submission_sha256']:
            raise ValueError(f'The prior {name} submission no longer matches its recorded hashes')
    prompt = hotel_agent.digest(existing / TEACHER / 'prompt.txt')
    if prompt != hotel_agent.digest(existing / CARRY / 'prompt.txt'):
        raise ValueError('The clean stage-two prompts differ')
    return frozen, prior_results, prompt


def current_event_score(native_score, oracle, seeded_check):
    indexed = {item['object']: item for item in native_score['checks']}
    current = [row['evidence_id'] for row in oracle['evidence_checks'] if row['group'] == 'required']
    checks = {name: indexed.get(name, {}).get('pass') for name in current}
    inherited = indexed.get(seeded_check, {})
    return {'new_event_checked': checks, 'new_event_passed': sum(value is True for value in checks.values()),
            'new_event_total': len(checks), 'new_event_unobservable': sum(value is None for value in checks.values()),
            'old_omission_recovered': inherited.get('pass'), 'old_omission_observed': inherited.get('observed'),
            'failed_checks': {item['object']: item['group'] for item in native_score['checks'] if not item['pass']}}


def run(source, existing, output, timeout, preflight_only=False):
    frozen, baselines, previous_prompt = verify_baselines(source, existing)
    first_oracle = read_json(source / 'stage_01/private/oracle.json')
    second_package = source / 'stage_02'
    second_oracle = read_json(second_package / 'private/oracle.json')
    output.mkdir(parents=True)
    seeded_run = output / 'seeded_stage_01'
    seed = seed_omission(existing / STAGE_ONE / 'submission', seeded_run / 'submission', first_oracle, second_oracle)
    carry = freeze_carry(seeded_run, second_package, output, frozen[TEACHER])
    public = Path(carry['package']) / 'public'
    actual = snapshot(public, hotel_agent.OFFICE_FILES)
    clean = snapshot(second_package / 'public', hotel_agent.OFFICE_FILES)
    changed = {key for key in actual.keys() | clean.keys() if actual.get(key) != clean.get(key)}
    if changed != {seed['native_object']}:
        raise ValueError('Stage two starts with changes beyond the intended inherited omission')
    plan = {'model': MODEL, 'effort': EFFORT, 'session_limit': 1, 'timeout_seconds': timeout,
            'preflight_only': preflight_only, 'source': str(source), 'baseline': str(existing),
            'frozen_sources': frozen, 'frozen_challenge': carry, 'baseline_stage_two_prompt_sha256': previous_prompt,
            'baseline_results': {key: value['score_groups'] for key, value in baselines.items()},
            'seed': seed, 'stage_two_object_diff_from_clean': sorted(changed)}
    save_json(output / 'plan.json', plan)
    if preflight_only:
        print('HOTEL_SEQUENCE_CHALLENGE_PREFLIGHT', output, flush=True)
        return plan

    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login'] or route['model'] != MODEL:
        raise RuntimeError(f'The official Codex route for {MODEL} is required')
    try:
        trial = hotel_agent.run_layout(CHALLENGE, carry, output, route, EFFORT, timeout, False)
        if hotel_agent.digest(output / CHALLENGE / 'prompt.txt') != previous_prompt:
            raise ValueError('The seeded trial prompt differs from the clean prompts')
    except Exception as exc:
        trial = {'status': 'RUN_ERROR', 'native_pass': None, 'error': f'{type(exc).__name__}: {exc}'}
    final_path = output / CHALLENGE / 'native_evaluation.json'
    results = {'run': trial, 'seed': seed, 'comparison': None}
    if final_path.is_file() and trial['status'] == 'COMPLETED':
        results['comparison'] = current_event_score(read_json(final_path), second_oracle, seed['evidence_id'])
    checks = verify_baselines(source, existing)[0]
    results['prior_inputs_and_scores_unchanged'] = checks == frozen
    save_json(output / 'results.json', results)
    print('HOTEL_SEQUENCE_CHALLENGE_RESULT', trial['status'], trial['native_pass'], output, flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    source = args.source.resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.out or source / 'agent_runs' / f'official_seeded_omission_{stamp}').resolve()
    run(source, args.baseline.resolve(), output, args.timeout, args.preflight_only)


if __name__ == '__main__':
    main()
