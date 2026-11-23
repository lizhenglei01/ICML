"""Run one official Codex trial per arm of the hotel correction sequence."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2, copytree

from alignment_probe.codex_transport import configured_route

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_revenue_sequence import DEFAULT, prepare_stage_two


STAGE_ONE = 'stage_01_blind'
TEACHER = 'stage_02_teacher_forced'
CARRY = 'stage_02_carry_forward'
MODEL = 'gpt-6.2-sol'
EFFORT = 'medium'


def freeze_carry(first_run, second_package, output, original):
    proxy = output / 'carry_source'
    transition = prepare_stage_two(first_run / 'submission', proxy / 'public', second_package)
    original_private = second_package / 'private'
    private = proxy / 'private'
    private.mkdir()
    copy2(original_private / 'oracle.json', private / 'oracle.json')
    copytree(original_private / 'reference', private / 'reference')
    save_json(private / 'native_manifest.json', {
        'public_sha256': hotel_agent.file_hashes(proxy / 'public', hotel_agent.OFFICE_FILES),
        'reference_sha256': original['reference_sha256'],
    })
    frozen = hotel_agent.freeze_packages({CARRY: proxy}, original_oracle=True)[CARRY]
    if (frozen['oracle_sha256'] != original['oracle_sha256'] or frozen['reference_sha256'] != original['reference_sha256']
            or frozen['public_sha256']['instruction.md'] != original['public_sha256']['instruction.md']):
        raise ValueError('The carried arm must use the unchanged stage-two instruction, reference and Oracle')
    transition['frozen_stage_one_submission_sha256'] = hotel_agent.file_hashes(first_run / 'submission', hotel_agent.OFFICE_FILES)
    save_json(output / 'transition.json', transition)
    return frozen


def failed_checks(path):
    if not path.is_file():
        return None
    return {item['object']: item['group'] for item in read_json(path)['checks'] if not item['pass']}


def compare(output):
    checks = {label: failed_checks(output / label / 'native_evaluation.json') for label in (STAGE_ONE, TEACHER, CARRY)}
    comparison = {'failed_checks': checks}
    if checks[STAGE_ONE] is not None and checks[CARRY] is not None:
        first, carry = checks[STAGE_ONE], checks[CARRY]
        comparison['stage_one_failed_checks_still_failing_after_carry'] = sorted(first.keys() & carry.keys())
        comparison['stage_one_failed_checks_recovered_after_carry'] = sorted(first.keys() - carry.keys())
    if checks[TEACHER] is not None and checks[CARRY] is not None:
        teacher, carry = checks[TEACHER], checks[CARRY]
        comparison['failures_only_in_carry'] = sorted(carry.keys() - teacher.keys())
        comparison['failures_only_in_teacher_forced'] = sorted(teacher.keys() - carry.keys())
    return comparison


def run(source, output, timeout):
    validation = read_json(source / 'summary.json')
    if not validation['passed'] or not validation['controls']['gold_stage_one_equals_stage_two_start_after_injection']:
        raise ValueError('The offline authoring and carry-forward control must pass first')
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login'] or route['model'] != MODEL:
        raise RuntimeError(f'This trial requires the configured official Codex login and {MODEL}')

    second_package = source / 'stage_02'
    frozen = hotel_agent.freeze_packages({STAGE_ONE: source / 'stage_01', TEACHER: second_package}, original_oracle=True)
    output.mkdir(parents=True)
    plan = {'model': MODEL, 'effort': EFFORT, 'timeout_per_session_seconds': timeout,
            'validation_sha256': hotel_agent.digest(source / 'summary.json'), 'layouts': frozen,
            'session_limit': 3, 'fresh_thread_each_session': True,
            'carry_protocol': 'Use actual stage-one Office files. Insert the authorized source cell value and replace the instruction before launching a fresh thread.'}
    save_json(output / 'plan.json', plan)
    records = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        def submit(label):
            return executor.submit(hotel_agent.run_layout, label, frozen[label], output, route, EFFORT, timeout, False)

        pending = {submit(label): label for label in (STAGE_ONE, TEACHER)}
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                label = pending.pop(future)
                try:
                    records[label] = future.result()
                except Exception as exc:
                    records[label] = {'status': 'RUN_ERROR', 'native_pass': None, 'error': f'{type(exc).__name__}: {exc}'}
                print('HOTEL_SEQUENCE_RESULT', label, records[label]['status'], records[label]['native_pass'], flush=True)
                if label == STAGE_ONE and records[label]['status'] == 'COMPLETED':
                    try:
                        frozen[CARRY] = freeze_carry(output / STAGE_ONE, second_package, output, frozen[TEACHER])
                    except Exception as exc:
                        records[CARRY] = {'status': 'HISTORY_UNAVAILABLE', 'native_pass': None,
                                          'error': f'{type(exc).__name__}: {exc}'}
                    else:
                        save_json(output / 'plan.json', plan)
                        pending[submit(CARRY)] = CARRY
                elif label == STAGE_ONE:
                    records[CARRY] = {'status': 'HISTORY_UNAVAILABLE', 'native_pass': None,
                                      'error': 'Stage-one trial did not complete. The carry-forward arm was not substituted.'}
                save_json(output / 'results.json', records)

    if (output / CARRY / 'prompt.txt').is_file():
        if hotel_agent.digest(output / TEACHER / 'prompt.txt') != hotel_agent.digest(output / CARRY / 'prompt.txt'):
            raise ValueError('Stage-two prompts differed across arms')
    save_json(output / 'comparison.json', compare(output))
    print('HOTEL_SEQUENCE_OUTPUT', output, flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    source = args.source.resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.out or source / 'agent_runs' / f'official_gpt62_sol_medium_{stamp}').resolve()
    run(source, output, args.timeout)


if __name__ == '__main__':
    main()
