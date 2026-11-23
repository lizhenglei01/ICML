"""Compare carried and standard-start hotel action programs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2
from threading import Lock

from alignment_probe.codex_transport import configured_route
from research_archive import archive_hotel_run, capture_run_environment

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_stateful_bench import BOOK, DEFAULT, PROGRAM, PUBLIC, STATE_READ_ERRORS, grade, read_state

MODEL = 'gpt-6.2-sol'


def input_for(mode, canonical, stage, prior_submission, destination):
    if mode == 'independent' or stage == 1:
        return canonical, 'standard'
    if prior_submission is None:
        return None, 'chain_interrupted'
    try:
        if set(hotel_agent.file_hashes(prior_submission, (BOOK, PROGRAM))) != {BOOK, PROGRAM}:
            return None, 'missing_predecessor'
        read_state(prior_submission / BOOK)
        (prior_submission / PROGRAM).read_text(encoding='utf-8')
    except STATE_READ_ERRORS:
        return None, 'unreadable_predecessor'
    public = destination / 'public'
    public.mkdir(parents=True)
    for name in (BOOK, PROGRAM):
        copy2(prior_submission / name, public / name)
    copy2(canonical / 'public/instruction.md', public / 'instruction.md')
    (destination / 'private').symlink_to(canonical / 'private', target_is_directory=True)
    return destination, 'actual_predecessor'


def freeze(package):
    private = package / 'private'
    office = (BOOK, PROGRAM)
    hotel_agent.check_public(package / 'public', PUBLIC)
    return {'package': str(package), 'office_files': office, 'editable_files': (PROGRAM,),
            'public_sha256': hotel_agent.file_hashes(package / 'public', PUBLIC),
            'reference_sha256': hotel_agent.file_hashes(private / 'reference', office),
            'oracle_sha256': hotel_agent.digest(private / 'oracle.json')}


def score_for(record, directory, package, stage):
    result = directory / 'native_evaluation.json'
    if not result.is_file():
        events = record.get('trace', {}).get('runtime_events', [])
        prefix = f'Model metadata for `{MODEL}` not found. Defaulting to fallback metadata;'
        metadata_only = bool(events) and all(event.get('type') == 'item.completed'
                                            and event.get('item', {}).get('message', '').startswith(prefix)
                                            for event in events)
        if (record.get('returncode') == 0 and record.get('status') == 'RUN_ERROR' and metadata_only
                and record.get('trace', {}).get('usage') and record.get('originals_and_oracle_unchanged')
                and record.get('reference_unchanged')):
            score = grade(directory / 'submission', stage, read_json(package / 'private/oracle.json'))
            save_json(result, score)
            record.update(raw_status='RUN_ERROR', status='COMPLETED', native_pass=score['pass'])
    return read_json(result) if result.is_file() else None


def diagnostics(score, stage, previous_score):
    if not score or score['status'] != 'checked':
        return {}
    previous = {case['name']: case['pass'] for case in (previous_score or {}).get('cases', [])}
    categories = {'new_failure': [], 'regression': [], 'inherited_failure': [], 'unknown_prior': []}
    for case in score['cases']:
        name = case['name']
        if not case['pass']:
            if case['introduced'] == stage:
                categories['new_failure'].append(name)
            elif name in previous:
                categories['regression' if previous[name] else 'inherited_failure'].append(name)
            else:
                categories['unknown_prior'].append(name)
    return {'case_pass_count': sum(case['pass'] for case in score['cases']), 'case_count': len(score['cases']),
            'step_pass_count': sum(step['pass'] for case in score['cases'] for step in case['steps']),
            'step_count': sum(len(case['steps']) for case in score['cases']), **categories}


def run(source, output, timeout):
    if not read_json(source / 'summary.json')['passed']:
        raise ValueError('Run and validate the benchmark builder first')
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login']:
        raise RuntimeError('Use the official OpenAI route with saved ChatGPT login')
    route['model'] = MODEL
    output.mkdir(parents=True)
    save_json(output / 'plan.json', {'model': MODEL, 'effort': 'medium', 'shared_initial_run': 'm1',
        'environment_at_run': capture_run_environment('stateful'),
        'continuous': 'Carry every readable actual submission regardless of score.',
        'independent': 'Use the standard previous program and workbook at every milestone.'})
    lock = Lock()
    records = {}

    def attempt(label, mode, stage, previous_path=None, previous_score=None):
        canonical = source / f'm{stage}'
        package, origin = input_for(mode, canonical, stage, previous_path, output / 'carried' / label)
        directory = output / label
        score = None
        if package:
            try:
                record = hotel_agent.run_layout(
                    label, freeze(package), output, route, 'medium', timeout, False,
                    grader=lambda target, oracle: grade(target, stage, oracle),
                    task_suffix='Implement the action program in approval.py. Test on workbook copies and keep the supplied initial workbook ready for the grader.',
                )
                score = score_for(record, directory, package, stage)
            except (RuntimeError, ValueError, OSError) as exc:
                record = {'status': 'RUN_ERROR', 'error': str(exc)}
        else:
            record = {'status': 'CHAIN_INTERRUPTED'}
        with lock:
            records[label] = {**record, 'starting_point': origin, 'mode': mode,
                              'business_score': score['pass'] if score and score['status'] == 'checked' else None,
                              'score_status': score['status'] if score else None,
                              **diagnostics(score, stage, previous_score)}
            save_json(output / 'results.json', records)
        print('STATEFUL_MODEL_RESULT', label, records[label]['business_score'], record['status'], flush=True)
        submission = directory / 'submission'
        return submission if submission.is_dir() else None, score

    def continuous(previous_path, previous_score):
        for stage in (2, 3):
            previous_path, previous_score = attempt(f'm{stage}_continuous', 'continuous', stage, previous_path, previous_score)

    with ThreadPoolExecutor(max_workers=3) as pool:
        controls = [pool.submit(attempt, f'm{stage}_independent', 'independent', stage, None,
                                read_json(source / f'm{stage-1}/private/reference_score.json')) for stage in (2, 3)]
        chain = attempt('m1', 'continuous', 1)
        continuous(*chain)
        for future in controls:
            future.result()
    archive_hotel_run('stateful', source, output)
    print('STATEFUL_MODEL_COMPLETE', output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or args.source / 'agent_runs' / ('gpt62_stateful_' + stamp)
    run(args.source.resolve(), output.resolve(), args.timeout)


if __name__ == '__main__':
    main()
