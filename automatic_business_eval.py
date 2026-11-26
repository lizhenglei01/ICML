"""Evaluate the frozen two-topic Office packages with three isolated model routes.

This runner imports the existing sandbox and native grader. It never rebuilds a
task, changes an Oracle, supplies a private answer, or resumes a model thread.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from research_archive import ROOT, digest, git, read, write
from multifile_reconstruction.reasoning_graph_mvp.logic_first import hotel_agent as runner
from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_office import grade
from multifile_reconstruction.workspace_bench_lite38.responses_transport import credential_proxy

MODELS = {'gpt62': 'gpt-6.2-sol', 'deepseek': 'deepseek-v4-pro', 'qwen4': 'qwen4-max'}
SOURCE = ROOT / 'artifacts/automatic_business_20261126T140730686Z/verified'
SUFFIX = ('Use the Office libraries available in Python when useful. Preserve unrelated content and native chart data. '
          'The workspace files and the task instruction are your evidence. Save the edited files in place. '
          'A final chat message does not replace the modified files.')


def api_route(route):
    if route == 'deepseek':
        return 'https://api.deepseek.com/responses', os.environ['DEEPSEEK_API_KEY'], None
    if route != 'qwen4':
        raise ValueError('Unknown hosted route')
    endpoint = os.environ['LITE38_DASHSCOPE_BASE_URL'].rstrip('/')
    target = urlsplit(endpoint)
    if (target.scheme != 'https' or not (target.hostname or '').endswith('.maas.aliyuncs.com')
            or target.path != '/compatible-mode/v1' or target.username or target.password
            or target.query or target.fragment):
        raise ValueError('Expected an Alibaba Cloud Model Studio workspace URL')
    return endpoint + '/responses', os.environ['DASHSCOPE_API_KEY'], {'enable_thinking': True}


def freeze(source):
    if (source / 'summary.json').is_file():
        summary = read(source / 'summary.json')
        variants = summary.get('variants')
        if summary.get('passed') is not True or set(variants or ()) != {'individual', 'shared_pool'}:
            raise ValueError('Expected a validated decision pair')
        frozen = runner.freeze_packages({name: source / 'case' / name for name in variants}, original_oracle=True)
        for name, record in frozen.items():
            details = variants[name]
            record.update(topic=read(source / 'input.json')['topic'], layout=name,
                          minimum_depth=details['minimum_depth'], minimum_files=details['minimum_files'])
        return frozen
    packages = {}
    for layout in runner.LAYOUTS:
        for topic in ('hotel', 'procurement'):
            summary = read(source / topic / 'summary.json')
            if (summary.get('passed') is not True
                    or summary['layouts'][layout]['controls'].get('reference') is not True):
                raise ValueError(f'Unvalidated package: {topic}/{layout}')
            packages[f'{topic}__{layout}'] = source / topic / 'case/layouts' / layout
    frozen = runner.freeze_packages(packages, original_oracle=True)
    for name, record in frozen.items():
        topic, layout = name.split('__')
        details = read(source / topic / 'summary.json')['layouts'][layout]
        record.update(topic=topic, layout=layout, minimum_depth=details['minimum_depth'],
                      minimum_files=details['minimum_files'])
    return frozen


def source_unchanged(frozen):
    package = Path(frozen['package'])
    return (runner.file_hashes(package / 'public', (*frozen['office_files'], 'instruction.md')) == frozen['public_sha256']
            and digest(package / 'private' / frozen['oracle_file']) == frozen['oracle_sha256']
            and runner.file_hashes(package / 'private/reference', frozen['office_files']) == frozen['reference_sha256'])


def fatal_runtime_events(events, model):
    """A known Codex metadata fallback warning is not a failed model response."""
    warning = (f'Model metadata for `{model}` not found. Defaulting to fallback metadata; '
               'this can degrade performance and cause issues.')
    return [e for e in events if not (e.get('type') == 'item.completed'
            and (e.get('item') or {}).get('type') == 'error'
            and e['item'].get('message') == warning)]


def run_one(route, name, frozen, directory, effort, preflight=False):
    started = time.monotonic()
    package = Path(frozen['package'])
    work, completion = directory / 'submission', directory / 'completion'
    names = (*frozen['office_files'], 'instruction.md')
    if not source_unchanged(frozen):
        raise ValueError('Frozen source changed before run')
    runner.prepare_workspace(package / 'public', work, names)
    completion.mkdir()
    prompt = (work / 'instruction.md').read_text() + '\n\n'
    prompt += runner.edit_scope(frozen['office_files'], frozen.get('editable_files')) + SUFFIX
    (directory / 'prompt.txt').write_text(prompt, encoding='utf-8')
    report = {'model': MODELS[route], 'effort_requested': effort, 'task': name,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'overall_timeout_seconds': None,
              'minimum_depth': frozen['minimum_depth'], 'minimum_files': frozen['minimum_files'],
              'initial_sha256': frozen['public_sha256'], 'oracle_sha256': frozen['oracle_sha256']}
    provider_trace = []
    codex = Path(shutil.which('codex')).resolve()
    with ExitStack() as stack:
        auth = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='automatic-business-auth-')))
        if route == 'gpt62':
            home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
            shutil.copy2(home / 'auth.json', auth / 'auth.json')
        mounts = runner.sandbox_arguments(work, completion, auth, codex)
        env = runner.isolated_env()
        report['isolation_check'] = runner.isolated_check(mounts, env, package, directory, names)
        if preflight:
            report.update(status='PREFLIGHT_PASSED', wall_seconds=time.monotonic() - started)
            write(directory / 'run.json', report)
            return report
        command = runner.model_command(MODELS[route], effort)
        if route != 'gpt62':
            endpoint, key, extra = api_route(route)
            proxy = stack.enter_context(credential_proxy(endpoint, key, provider_trace, extra_fields=extra))
            command[-1:-1] = [
                '-c', 'model_provider="benchmark_api"',
                '-c', 'model_providers.benchmark_api.name="Benchmark API"',
                '-c', f'model_providers.benchmark_api.base_url={json.dumps(proxy)}',
                '-c', 'model_providers.benchmark_api.wire_api="responses"',
                '-c', 'model_providers.benchmark_api.supports_websockets=false',
            ]
        write(directory / 'started.json', report)
        print('STARTED', route, name, flush=True)
        with (directory / 'events.jsonl').open('w') as stdout, (directory / 'stderr.log').open('w') as stderr:
            result = subprocess.run(mounts + command, input=prompt, text=True, stdout=stdout, stderr=stderr, env=env)
    report.update(wall_seconds=round(time.monotonic() - started, 3), returncode=result.returncode,
                  source_unchanged=source_unchanged(frozen), provider_requests=provider_trace,
                  trace=runner.event_summary(directory / 'events.jsonl'),
                  submission_sha256=runner.file_hashes(work, frozen['office_files']))
    report['modified_files'] = [f for f in frozen['office_files']
                                if report['submission_sha256'].get(f) != frozen['public_sha256'][f]]
    report['native_pass'] = None
    report['fatal_runtime_events'] = fatal_runtime_events(report['trace']['runtime_events'], MODELS[route])
    if not report['source_unchanged']:
        report['status'] = 'SOURCE_CHANGED'
    elif any(r['model'] != MODELS[route] for r in provider_trace):
        report['status'] = 'MODEL_MISMATCH'
    elif result.returncode or report['fatal_runtime_events'] or not report['trace']['usage']:
        report['status'] = 'RUN_ERROR'
    else:
        try:
            score = grade(work, read(package / 'private' / frozen['oracle_file']))
            write(directory / 'native_evaluation.json', score)
            report.update(status='COMPLETED', native_pass=score['pass'], score_groups=score['groups'])
        except Exception as exc:
            report.update(status='SCORING_ERROR', scoring_error=type(exc).__name__)
    write(directory / 'run.json', report)
    print('FINISHED', route, name, report['status'], report['native_pass'], report['wall_seconds'], flush=True)
    return report


def run_route(route, frozen, output, effort, preflight):
    results = {}
    for name, package in frozen.items():
        try:
            results[name] = run_one(route, name, package, output / route / name, effort, preflight)
        except Exception as exc:
            results[name] = {'model': MODELS[route], 'status': 'HARNESS_ERROR', 'native_pass': None,
                             'error_type': type(exc).__name__}
            print('HARNESS_ERROR', route, name, type(exc).__name__, flush=True)
        write(output / route / 'results.json', results)
        if results[name]['status'] not in {'COMPLETED', 'PREFLIGHT_PASSED'}:
            # Preserve all evidence; do not spend eight attempts on a broken route.
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--route', choices=MODELS, action='append')
    parser.add_argument('--task', action='append', help='Select frozen task identifiers; omit for all eight')
    parser.add_argument('--workers', type=int, default=24, help='Maximum independent trials in parallel')
    parser.add_argument('--effort', default='medium')
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 24:
        parser.error('--workers must be between 1 and 24')
    routes = list(dict.fromkeys(args.route or MODELS))
    for route in routes:
        if route != 'gpt62' and not args.preflight_only:
            api_route(route)
    frozen = freeze(args.source.resolve())
    if args.task:
        if not set(args.task) <= frozen.keys():
            raise ValueError('Unknown frozen task')
        frozen = {k:v for k,v in frozen.items() if k in args.task}
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), ROOT / 'research_archive.py',
             ROOT / 'multifile_reconstruction/workspace_bench_lite38/responses_transport.py',
             *(Path(runner.__file__).parent / name for name in
               ('hotel_agent.py', 'hotel_office.py', 'hotel_visibility.py', 'scenario_bench.py', 'scenario_graph.py'))]
    write(output / 'plan.json', {'run_id': output.name, 'base_commit': git('rev-parse', 'HEAD'),
          'models': {r:MODELS[r] for r in routes}, 'effort_requested': args.effort,
          'planned_trials': len(routes)*len(frozen), 'preflight_only': args.preflight_only,
          'source_sha256': {str(p.relative_to(ROOT)):digest(p) for p in files},
          'python': sys.version, 'libraries': {p:version(p) for p in ('openpyxl','python-docx','python-pptx')},
          'packages': frozen, 'workers': args.workers,
          'execution': 'Each model/task pair has an independent workspace and fresh thread; up to workers trials concurrently.',
          'controls': 'Same frozen files, prompt suffix, sandbox, CLI tools, and original Oracle. No history or grader feedback.',
          'scope': 'Static native-file revision. One run per layout. Code-capable agents; not GUI-only evaluation.',
          'known_scoring_limits': ['Exact output text comparison may reject equivalent decimal representations.',
                                  'New equivalent formulas may fail literal-value checks.',
                                  'Protection counts and evidence volume differ across layouts.'],
          'transport_timeout': 'No overall deadline. Existing hosted proxy uses a 600-second socket timeout.'})
    with ThreadPoolExecutor(max_workers=min(args.workers, len(routes)*len(frozen))) as pool:
        jobs = {pool.submit(run_one, route, name, package, output / route / name,
                            args.effort, args.preflight_only):(route, name)
                for route in routes for name, package in frozen.items()}
        results = {route:{} for route in routes}
        for job in as_completed(jobs):
            route, name = jobs[job]
            try:
                result = job.result()
            except Exception as exc:
                result = {'model': MODELS[route], 'task': name, 'status': 'HARNESS_ERROR',
                          'native_pass': None, 'error_type': type(exc).__name__}
                write(output / route / name / 'run.json', result)
            results[route][name] = result
            write(output / route / 'results.json', results[route])
            write(output / 'results.json', results)
    if not all(source_unchanged(record) for record in frozen.values()):
        raise ValueError('Frozen sources changed during trial')
    print('OUTPUT', str(output), flush=True)


if __name__ == '__main__':
    main()
