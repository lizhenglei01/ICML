"""Run independent controls and one official three-stage native Office chain."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory

from alignment_probe.codex_transport import configured_route

from . import hotel_agent
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_revenue_sequence_agent import EFFORT, MODEL


DEFAULT = ARTIFACTS / 'hotel_revenue_milestones'
FIRST = 'stage_01_blind'
PHASES = {
    FIRST: 'stage_01',
    'stage_02_teacher_forced': 'stage_02',
    'stage_03_teacher_forced': 'stage_03',
    'stage_02_carry_forward': 'stage_02',
    'stage_03_carry_forward': 'stage_03',
}
TRANSITIONS = {
    FIRST: 'stage_02_carry_forward',
    'stage_02_carry_forward': 'stage_03_carry_forward',
}
TEACHERS = {'stage_02': 'stage_02_teacher_forced', 'stage_03': 'stage_03_teacher_forced'}
START = (FIRST, *TEACHERS.values())


def freeze_carry(submission, next_stage, output, original, *, prepare_stage=None):
    if prepare_stage is None:
        from .hotel_revenue_milestones import prepare_stage

    transition = prepare_stage(submission, output / 'public', next_stage)
    files = original.get('office_files', hotel_agent.OFFICE_FILES)
    private = output / 'private'
    copytree(next_stage / 'private', private)
    save_json(private / 'native_manifest.json', {
        **read_json(private / 'native_manifest.json'),
        'public_sha256': hotel_agent.file_hashes(output / 'public', files),
    })
    carried = hotel_agent.freeze_packages({next_stage.name: output}, original_oracle=True)[next_stage.name]
    if (carried['oracle_sha256'] != original['oracle_sha256'] or carried['reference_sha256'] != original['reference_sha256']
            or carried['public_sha256']['instruction.md'] != original['public_sha256']['instruction.md']):
        raise ValueError('The carry-forward attempt must use the canonical instruction, Oracle and reference')
    save_json(output / 'transition.json', {
        'upstream_submission_sha256': hotel_agent.file_hashes(submission, files),
        'transition': transition,
    })
    return carried


def _by_check(report):
    return {(item['object'], item['group']): item['pass'] for item in report['checks']}


def attribute_changes(initial, final, oracle, milestone):
    """Compare the same protection checks before and after the current attempt."""
    starting_checks = _by_check(initial)
    ending_checks = _by_check(final)
    semantic_roles = {item['evidence_id']: item['group'] for item in oracle['evidence_checks']}
    starting_parse_failed = any(group == 'structure' and not passed for (_, group), passed in starting_checks.items())
    report = {'missed_current': [], 'new_regressions': [], 'inherited_unrepaired': [],
              'inherited_recovered': [], 'other_failures': [],
              'capabilities': {key: milestone[key] for key in ('new_capabilities', 'retained_capabilities')}}
    for (identifier, group), success in ending_checks.items():
        before = starting_checks.get((identifier, group))
        role = group
        if group == 'semantic':
            role = semantic_roles.get(identifier, 'semantic')
            if identifier.startswith('functional_excel:'):
                origin = milestone.get('formula_check_stage', {}).get(identifier)
                if origin is None:
                    origin = milestone['formula_stage'].get(identifier.rsplit(':', 1)[-1])
                if origin is not None:
                    role = 'required' if origin == milestone['stage'] else 'protected'
        if not success and role == 'required':
            report['missed_current'].append(identifier)
        elif role == 'protected':
            if success and before is False:
                report['inherited_recovered'].append(identifier)
            elif not success:
                if before is True or (before is None and not starting_parse_failed):
                    report['new_regressions'].append(identifier)
                elif before is False:
                    report['inherited_unrepaired'].append(identifier)
                else:
                    report['other_failures'].append({'object': identifier, 'group': group, 'passed_at_start': before})
        elif not success:
            report['other_failures'].append({'object': identifier, 'group': group, 'passed_at_start': before})
    return report


def _score_start(frozen, output):
    package = Path(frozen['package'])
    oracle = read_json(package / 'private' / frozen['oracle_file'])
    score = hotel_agent.grade(package / 'public', oracle)
    save_json(output, score)
    return score, oracle, read_json(package / 'private/milestone.json')


def verify_pair(output, phase, teacher, carry):
    if teacher['oracle_sha256'] != carry['oracle_sha256']:
        raise ValueError(f'{phase} comparison used a different Oracle')
    teacher_prompt = output / TEACHERS[phase] / 'prompt.txt'
    carry_prompt = output / f'{phase}_carry_forward' / 'prompt.txt'
    if not (teacher_prompt.is_file() and carry_prompt.is_file()):
        return {'same_prompt': None, 'same_oracle': True}
    if hotel_agent.digest(teacher_prompt) != hotel_agent.digest(carry_prompt):
        raise ValueError(f'{phase} comparison used a different sample prompt')
    return {'same_prompt': True, 'same_oracle': True, 'prompt_sha256': hotel_agent.digest(teacher_prompt),
            'oracle_sha256': teacher['oracle_sha256']}


def run(source, output, timeout, *, preflight_only=False, prepare_stage=None):
    validation_path = source / 'summary.json'
    validation = read_json(validation_path)
    if validation.get('passed') is not True:
        raise ValueError('The milestone case must pass offline validation before attempting a live run')
    packages = {name: source / PHASES[name] for name in START}
    frozen = hotel_agent.freeze_packages(packages, original_oracle=True)
    output.mkdir(parents=True)
    plan = {'model': MODEL, 'effort': EFFORT, 'max_active_sessions': 3, 'max_total_sessions': 5,
            'timeout_seconds_each': timeout, 'fresh_thread_each': True, 'source': str(source),
            'case_validation_sha256': hotel_agent.digest(validation_path), 'frozen_inputs': frozen,
            'milestone_metadata_sha256': {phase: hotel_agent.digest(source / phase / 'private/milestone.json')
                                          for phase in sorted(set(PHASES.values()))},
            'control_start': 'Teacher-forced phases receive canonical public Office files.',
            'actual_start': 'Use prior submitted Office files only, plus the authorized prepare_stage transition.'}
    save_json(output / 'plan.json', plan)
    if preflight_only:
        with TemporaryDirectory(prefix='hotel_milestones_preflight_') as scratch:
            for predecessor, successor in TRANSITIONS.items():
                upstream = source / PHASES[predecessor] / 'private/reference'
                phase = PHASES[successor]
                freeze_carry(upstream, source / phase, Path(scratch) / phase, frozen[TEACHERS[phase]],
                             prepare_stage=prepare_stage)
        save_json(output / 'results.json', {'status': 'PREFLIGHT_PASSED', 'agent_calls': 0})
        return

    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login'] or route['model'] != MODEL:
        raise RuntimeError(f'The official Codex login and {MODEL} are required')
    records = {}

    def history_unavailable(label, reason):
        while label is not None:
            records[label] = {'status': 'HISTORY_UNAVAILABLE', 'native_pass': None, 'error': reason}
            label = TRANSITIONS.get(label)

    with ThreadPoolExecutor(max_workers=3) as pool:
        def submit(label):
            return pool.submit(hotel_agent.run_layout, label, frozen[label], output, route, EFFORT, timeout, False)

        pending = {submit(name): name for name in START}
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                name = pending.pop(future)
                try:
                    report = future.result()
                except Exception as exc:
                    report = {'status': 'RUN_ERROR', 'native_pass': None, 'error': f'{type(exc).__name__}: {exc}'}
                records[name] = report
                print('HOTEL_MILESTONE_AGENT_RESULT', name, report['status'], report['native_pass'], flush=True)
                next_step = TRANSITIONS.get(name)
                if next_step is not None:
                    if report['status'] != 'COMPLETED':
                        history_unavailable(next_step, f'{name} did not complete; there is no substitute history.')
                    else:
                        stage = PHASES[next_step]
                        try:
                            frozen[next_step] = freeze_carry(output / name / 'submission', source / stage,
                                                           output / 'inherited_inputs' / stage, frozen[TEACHERS[stage]],
                                                           prepare_stage=prepare_stage)
                        except Exception as exc:
                            history_unavailable(next_step, f'{type(exc).__name__}: {exc}')
                        else:
                            save_json(output / 'plan.json', plan)
                            pending[submit(next_step)] = next_step
                save_json(output / 'results.json', records)

    comparisons = {}
    for phase, teacher in TEACHERS.items():
        carry = f'{phase}_carry_forward'
        if carry in frozen:
            comparisons[phase] = verify_pair(output, phase, frozen[teacher], frozen[carry])
    thread_ids = [thread for record in records.values() for thread in record.get('trace', {}).get('thread_ids', ())]
    comparisons['observed_distinct_thread_ids'] = len(set(thread_ids))
    comparisons['all_observed_threads_distinct'] = len(set(thread_ids)) == len(thread_ids)
    for name, report in records.items():
        native_evaluation = output / name / 'native_evaluation.json'
        if report['status'] == 'COMPLETED' and native_evaluation.is_file():
            start, oracle, milestone = _score_start(frozen[name], output / name / 'starting_evaluation.json')
            report['failure_attribution'] = attribute_changes(start, read_json(native_evaluation), oracle, milestone)
    save_json(output / 'results.json', records)
    save_json(output / 'comparisons.json', comparisons)
    print('HOTEL_MILESTONES_AGENT_OUTPUT', output, flush=True)


def main(*, default_source=DEFAULT, prepare_stage=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=default_source)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    source = args.source.resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.out or source / 'agent_runs' / f'official_gpt62_sol_medium_{stamp}').resolve()
    run(source, output, args.timeout, preflight_only=args.preflight_only, prepare_stage=prepare_stage)


if __name__ == '__main__':
    main()
