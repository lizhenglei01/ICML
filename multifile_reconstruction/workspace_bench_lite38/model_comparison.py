"""Replay frozen Lite 38 milestones with hosted models in the native tool runner."""

import argparse
from datetime import datetime, timezone
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from research_archive import Archive, ROOT, digest, git, read, verify_inventory, write
from .responses_transport import credential_proxy
from .temporary_assignment import evaluate as m2_grade
from .temporary_assignment import run as isolated


BASE = Path(__file__).resolve().parent
PRIVATE = BASE / 'private'
ROUTES = {
    'deepseek': ('deepseek-v4-pro', 'https://api.deepseek.com/responses', 'DEEPSEEK_API_KEY'),
    'qwen': ('qwen3.5-27b', 'https://maas.qianwenaiapi.com/compatible-mode/v1/responses', 'DASHSCOPE_API_KEY'),
}
LABELS = ('lite38_generic_transfer_clean_m1', 'lite38_temporary_assignment_m2',
          *(f'lite38_evolution_m{number}' for number in range(3, 8)))
PROMPT = ('Read instruction.md and upgrade the supplied Excel workbook. Save the final workbook as '
          'submission.xlsx in this directory. The workbook is the deliverable.\n')
WORKBOOK = isolated.INPUT


def verify_frozen():
    baselines = [read(ROOT / 'experiments' / label / 'manifest.json') for label in LABELS]
    run_paths = [ROOT / manifest['private_archive']['location'] for manifest in baselines]
    for run, manifest in zip(run_paths, baselines):
        verify_inventory(run, manifest['private_archive']['inventory_sha256'])
    m1 = run_paths[0] / 'private_test/v2'
    frozen = read(m1 / 'freeze_manifest_clean_v2.json')
    for name, key in (('score.py', 'scorer_sha256'), ('oracle.py', 'oracle_sha256'),
                      ('hidden_cases_and_gt.json', 'hidden_cases_and_gt_sha256')):
        if digest(m1 / name) != frozen[key]:
            raise ValueError(f'M1 frozen input changed: {name}')
    task = run_paths[0] / 'm1/input/instruction.md'
    original = task.with_name(WORKBOOK)
    if (digest(task) != frozen['task_sha256'] or
            digest(original) != baselines[0]['provenance']['input_sha256']):
        raise ValueError('M1 original workbook or task changed')
    freezes = [m1 / 'freeze_manifest_clean_v2.json', run_paths[1] / 'private_test/freeze.json',
               *(run / 'private/freeze.json' for run in run_paths[2:])]
    tasks = [task, BASE / 'temporary_assignment/task.md',
             *(BASE / 'evolution' / f'm{number}.md' for number in range(3, 8))]
    for freeze in freezes[1:]:
        isolated.verify_suite(freeze)
    return original, tasks, freezes, run_paths


def run_agent(route, prompt, task_name, staging, directory, original, freeze):
    model, endpoint, env_key = ROUTES[route]
    key = os.environ[env_key]
    workspace = directory / 'workspace'
    output = directory / 'completion'
    isolated.prepare_workspace(staging, workspace, (WORKBOOK, task_name))
    output.mkdir()
    (directory / 'prompt.txt').write_text(prompt, encoding='utf-8')
    env = isolated.isolated_env()
    trace = []
    codex = Path(shutil.which('codex')).resolve()
    started_utc = datetime.now(timezone.utc).isoformat()
    start = monotonic()
    with TemporaryDirectory(prefix='lite38_api_auth_') as auth, credential_proxy(endpoint, key, trace) as proxy:
        mounts = isolated.sandbox_arguments(workspace, output, Path(auth), codex)
        probe = isolated.isolated_check(mounts, env, original, freeze, (WORKBOOK, task_name))
        command = isolated.model_command(model, isolated.EFFORT)
        command[-1:-1] = [
            '-c', 'model_provider="benchmark_api"',
            '-c', 'model_providers.benchmark_api.name="Benchmark API"',
            '-c', f'model_providers.benchmark_api.base_url={json.dumps(proxy)}',
            '-c', 'model_providers.benchmark_api.wire_api="responses"',
            '-c', 'model_providers.benchmark_api.supports_websockets=false',
        ]
        print('MODEL_STARTED', model, directory.name, started_utc, flush=True)
        with (directory / 'events.jsonl').open('w', encoding='utf-8') as stdout, \
                (directory / 'stderr.log').open('w', encoding='utf-8') as stderr:
            result = subprocess.run(mounts + command, input=prompt, text=True, stdout=stdout, stderr=stderr, env=env)
    submitted = workspace / 'submission.xlsx'
    events = isolated.event_summary(directory / 'events.jsonl')
    if any(event['model'] != model for event in trace):
        raise ValueError('Codex requested an unexpected provider model')
    delivered = submitted.is_file() and not submitted.is_symlink()
    completed = result.returncode == 0 and bool(events['usage'])
    report = {'model': model, 'effort_requested': isolated.EFFORT, 'started_utc': started_utc,
              'duration_seconds': round(monotonic() - start, 3), 'isolation_check': probe,
              'cli_exit_code': result.returncode, 'input_sha256': digest(original),
              'submission_sha256': digest(submitted) if delivered else None,
              'status': ('completed' if delivered else 'no_submission') if completed else 'runtime_error',
              'tool_events': events['tool_events'], 'usage': events['usage'], 'providers': trace}
    write(directory / 'run.json', report)
    print('MODEL_FINISHED', model, directory.name, report['status'], report['duration_seconds'], flush=True)
    return submitted, report


def launched(route, starting, task, number, baselines, freeze, directory):
    task_name = 'task.md' if number == 1 else 'instruction.md'
    if number == 1:
        archived = (baselines[0] / 'm1/input/prompt.txt').read_text(encoding='utf-8')
        prompt, replacements = re.subn(r'/tmp/[^/\s]+/submission\.xlsx', '/mnt/workspace/submission.xlsx', archived)
        if replacements != 1:
            raise ValueError('Unknown original M1 output path')
    else:
        prompt = PROMPT
    with TemporaryDirectory(prefix='lite38_comparison_input_') as temp:
        staged = Path(temp)
        shutil.copyfile(starting, staged / WORKBOOK)
        shutil.copyfile(task, staged / task_name)
        return run_agent(route, prompt, task_name, staged, directory, starting, freeze)


def score_stage(number, submission, starting, original, directory, baselines):
    if number == 1:
        # Run the frozen scorer within a private copy, because it writes recalc files relative to __file__.
        scorer = directory / 'frozen_score' / 'private' / 'generic_transfer'
        shutil.copytree(baselines[0] / 'private_test/v2', scorer, ignore=shutil.ignore_patterns('reference.xlsx'))
        shutil.copyfile(original, scorer.parent / 'lite38_original.xlsx')
        result = subprocess.run([sys.executable, str(scorer / 'score.py'), str(submission)],
                                text=True, capture_output=True, check=False)
        if result.returncode not in (0, 1) or not result.stdout.strip():
            (directory / 'grade_error.txt').write_text(result.stderr, encoding='utf-8')
            raise RuntimeError('Frozen M1 grader produced no report')
        return json.loads(result.stdout)
    if number == 2:
        gt = read(baselines[1] / 'private_test/hidden_cases_and_gt.json')
        return m2_grade.grade(submission, gt, directory / 'recalc', m2_grade.inventory(starting),
                              m2_grade.inventory(original))
    sys.path.insert(0, str(PRIVATE / 'evolution'))
    try:
        from .evolution import evaluate, linked_reports
        stage = f'm{number}'
        if number == 3:
            scorer = evaluate.grade
        else:
            scorer = getattr(import_module(f'prepare_{stage}'), 'score', linked_reports.score)
        gt = read(baselines[number - 1] / 'private/gt.json')
        return scorer(submission, gt, directory / 'recalc', m2_grade.inventory(starting), m2_grade.inventory(original))
    finally:
        sys.path.pop(0)


def flags(result):
    structure = result.get('structure', result.get('workbook_checks', {}))
    values = {('structure', key): value for key, value in structure.items()}
    for name, case in result['cases'].items():
        for category in ('checks', 'business', 'protection'):
            values.update({(name, category, key): value for key, value in case.get(category, {}).items()})
    return values


def summarize(number, outcome, before):
    current = flags(outcome)
    summary = {'frozen_score': [sum(current.values()), len(current)]}
    if number == 1:
        legacy = [value for key, value in current.items() if key[-1].startswith(('legacy_', 'starting_'))]
        summary['old_checks'] = [sum(legacy), len(legacy)]
        return summary
    new, effects = [], []
    for case in outcome['cases'].values():
        if case['stage'] == f'm{number}':
            new_case = case.get('checks', case.get('business'))
            new.extend(new_case.values())
            effects.extend(new_case[key] for key in case['effect_keys'])
    summary['new_case_checks'] = [sum(new), len(new)]
    summary['new_effect_checks'] = [sum(effects), len(effects)]
    if before is not None:
        from .evolution.evaluate import M3_INPUTS

        previous = flags(before)
        old = [key for key in current if outcome['cases'].get(key[0], {}).get('stage') != f'm{number}']
        if number == 2:
            old = [key for key in old if not (key[0] == 'structure' and key[-1].startswith('temporary_'))]
            old += [key for key in current if key[1] == 'protection' and key not in old]
        if number == 3:
            new_items = ('employment_period_interface', 'employment_periods_blank', *M3_INPUTS)
            old = [key for key in old if key[0] != 'structure' or key[-1] not in new_items]
        transitions = {'matched': 0, 'passed_to_failed': [], 'failed_to_failed': [],
                       'failed_to_passed': [], 'without_baseline': []}
        for key in old:
            if key not in previous:
                transitions['without_baseline'].append('/'.join(key))
                continue
            transitions['matched'] += 1
            if previous[key] and current[key]:
                continue
            origin = 'passed' if previous[key] else 'failed'
            destination = 'passed' if current[key] else 'failed'
            transitions[f'{origin}_to_{destination}'].append('/'.join(key))
        summary['old_check_transitions'] = transitions
    return summary


def run(route, directory, last_stage):
    original, tasks, freezes, baselines = verify_frozen()
    code_commit = git('rev-parse', 'HEAD')
    current = original
    label = f'lite38_model_comparison_{route}'
    archive = Archive(label, directory.name)
    steps = {}
    for number in range(1, last_stage + 1):
        stage = f'm{number}'
        work = directory / stage
        task = tasks[number - 1]
        freeze = freezes[number - 1]
        submission, report = launched(route, current, task, number, baselines, freeze, work)
        row = {'status': report['status'], 'input_sha256': report['input_sha256'],
               'submission_sha256': report['submission_sha256'], 'duration_seconds': report['duration_seconds']}
        for file, name, role in ((task, 'instruction.md', 'task'), (current, WORKBOOK, 'input'),
                                 (freeze, 'freeze.json', 'frozen_suite')):
            archive.add(file, f'{stage}/input/{name}', role)
        if report['submission_sha256']:
            archive.add(submission, f'{stage}/submission.xlsx', 'submission')
        if report['status'] == 'completed':
            try:
                outcome = score_stage(number, submission, current, original, work / 'after', baselines)
                write(work / 'scoring.json', outcome)
                archive.add(work / 'scoring.json', f'{stage}/scoring.json', 'scoring')
                before = None
                if number > 1:
                    before = score_stage(number, current, current, original, work / 'before', baselines)
                    write(work / 'before.json', before)
                    archive.add(work / 'before.json', f'{stage}/before.json', 'pre_edit_scoring')
                summary = summarize(number, outcome, before)
                transitions = summary.pop('old_check_transitions', None)
                if transitions is not None:
                    archive.derived(f'{stage}/old_check_transitions.json', transitions, 'test_transitions')
                    summary['old_check_transitions'] = {
                        key: len(value) if isinstance(value, list) else value for key, value in transitions.items()}
                row.update(summary)
            except (RuntimeError, TypeError, ValueError, KeyError) as exc:
                row['status'] = 'grader_did_not_complete'
                write(work / 'grade_issue.json', {'error': type(exc).__name__, 'detail': str(exc)})
                archive.add(work / 'grade_issue.json', f'{stage}/grade_issue.json', 'scoring_diagnostic')
        for name in ('run.json', 'prompt.txt', 'events.jsonl', 'stderr.log', 'completion/completion.txt'):
            if (work / name).is_file():
                archive.add(work / name, f'{stage}/{name}', 'execution_trace')
        print('STAGE_RESULT', route, stage, row, flush=True)
        steps[stage] = row
        archive.seal(f'Lite 38 连续模型对照 · {ROUTES[route][0]}', 'office_business_workflow',
                     {'model': ROUTES[route][0], 'requested_reasoning_effort': isolated.EFFORT,
                      'code_commit_at_start': code_commit, 'target_last_stage': last_stage, 'steps': steps})
        if report['status'] != 'completed':
            break
        current = submission


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('route', choices=ROUTES)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--last-stage', type=int, choices=range(1, 8), default=7)
    options = parser.parse_args()
    run(options.route, options.directory.resolve(), options.last_stage)
