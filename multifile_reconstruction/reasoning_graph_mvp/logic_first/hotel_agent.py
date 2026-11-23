"""Run a bounded native Office editing trial through the existing Codex route."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from ...build import digest, save
from .hotel_office import FILES, active_oracle_path, grade


LAYOUTS = ('shallow_concentrated', 'shallow_cross_file', 'deep_concentrated', 'deep_cross_file')
OFFICE_FILES = tuple(FILES.values())
PUBLIC_NAMES = OFFICE_FILES + ('instruction.md',)
DEFAULT_SOURCE = Path(__file__).resolve().parent / 'business_artifacts' / 'hotel_evidence'


def file_hashes(folder, expected_names):
    return {name: digest(folder / name) for name in expected_names
            if (folder / name).is_file() and not (folder / name).is_symlink()}


def check_public(public, public_names=PUBLIC_NAMES):
    names = {entry.name for entry in public.iterdir()}
    if public.is_symlink() or names != set(public_names) or any(entry.is_symlink() or not entry.is_file() for entry in public.iterdir()):
        raise ValueError(f'{public} must contain exactly the three native inputs and instruction.md')


def freeze_layouts(source, selected):
    return freeze_packages({name: source / 'layouts' / name for name in selected})


def freeze_packages(packages, *, original_oracle=False):
    """Record the exact bytes and verify native reference artifacts before any call."""
    layouts = {}
    for name, package in packages.items():
        public, private = package / 'public', package / 'private'
        oracle_path = private / 'oracle.json' if original_oracle else active_oracle_path(private)
        oracle = json.loads(oracle_path.read_text(encoding='utf-8'))
        office_files = tuple(oracle['files'])
        if tuple(Path(name).suffix for name in office_files) != ('.xlsx', '.docx', '.pptx') or any(
                Path(name).name != name for name in office_files):
            raise ValueError('Invalid native package file names')
        names = office_files + ('instruction.md',)
        check_public(public, names)
        reference = grade(private / 'reference', oracle)
        baseline = grade(public, oracle)
        if not reference['pass'] or baseline['pass']:
            raise ValueError(f'{name} needs a passing native reference and a failing pre-edit baseline')
        manifest = json.loads((private / 'native_manifest.json').read_text(encoding='utf-8'))
        original_hashes = file_hashes(public, names)
        reference_hashes = file_hashes(private / 'reference', office_files)
        if ({file: original_hashes[file] for file in office_files} != manifest['public_sha256'] or
                reference_hashes != manifest['reference_sha256']):
            raise ValueError(f'{name} native assets differ from the validated manifest')
        layouts[name] = {'package': str(package), 'office_files': office_files, 'public_sha256': original_hashes, 'oracle_file': oracle_path.name,
                         'oracle_sha256': digest(oracle_path), 'reference_sha256': reference_hashes,
                         'reference_grade': reference['groups'],
                         'unedited_grade': baseline['groups']}
        if 'editable_files' in oracle:
            layouts[name]['editable_files'] = tuple(oracle['editable_files'])
    return layouts


def prepare_workspace(public, writable, public_names=PUBLIC_NAMES):
    check_public(public, public_names)
    writable.mkdir(parents=True)
    for name in public_names:
        shutil.copyfile(public / name, writable / name)
    check_public(writable, public_names)
    if file_hashes(writable, public_names) != file_hashes(public, public_names):
        raise ValueError('Writable copies differ from frozen assets')


def sandbox_arguments(writable, completion, auth_only, codex_file):
    """Mount the interpreter and system runtime explicitly, not the host project tree."""
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise RuntimeError('Bubblewrap is required for the public-only native trial')
    auth_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
    python_root = Path(sys.base_prefix).resolve()
    if '/' == str(python_root):
        raise RuntimeError('Use a standalone Python installation or a virtual environment')
    args = [bwrap, '--die-with-parent', '--new-session', '--unshare-pid', '--unshare-ipc',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/mnt', '--tmpfs', '/run']
    for resource in ('/etc/ssl/certs', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf',
                     '/etc/ld.so.cache', '/etc/passwd', '/etc/group', '/etc/localtime'):
        if Path(resource).exists():
            args += ['--ro-bind', resource, resource]
    if not python_root.is_relative_to('/usr'):
        project = DEFAULT_SOURCE.parents[4]
        if python_root.is_relative_to(project) or project.is_relative_to(python_root):
            raise RuntimeError('Python root must not expose the benchmark project')
        args += ['--ro-bind', str(python_root), str(python_root)]
    args += ['--ro-bind', str(codex_file.parent), '/mnt/codex-bin',
             '--bind', str(auth_only), str(auth_home), '--bind', str(writable), '/mnt/workspace',
             '--bind', str(completion), '/mnt/model-output', '--chdir', '/mnt/workspace']
    return args


def isolated_env():
    # CLI networking still needs the workstation's routes. The inner tool sandbox
    # independently disables command network access for the tested session.
    keys = ('HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TZ', 'CODEX_HOME',
            'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy')
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env['PATH'] = f'{sys.base_prefix}/bin:/usr/bin:/bin'
    return env


def isolated_check(mounts, env, source, run_directory, public_names=PUBLIC_NAMES):
    code = ('import pathlib,sys,openpyxl,docx,pptx; '
            'root=pathlib.Path.cwd(); '
            'entries={p.name:p for p in root.iterdir()}; '
            'assert set(sys.argv[1:5]) <= set(entries); '
            'extras=set(entries)-set(sys.argv[1:5]); '
            'assert extras <= {".git",".agents",".aws",".codex"}; '
            'assert all(entries[e].is_dir() and not list(entries[e].iterdir()) for e in extras); '
            'assert all(not pathlib.Path(p).exists() for p in sys.argv[5:]); '
            'assert not pathlib.Path("/mnt/workspace/private").exists(); '
            'probe=(root/".write_probe"); probe.write_text("ok"); probe.unlink(); '
            'print("isolated native input, Python libraries, and writable tool workspace verified")')
    sandbox_probe = ['/mnt/codex-bin/codex', 'sandbox', '-c', 'sandbox_mode="workspace-write"',
                     '-c', 'sandbox_workspace_write.network_access=false', '--', 'python', '-c', code,
                     *public_names, str(source), str(run_directory), str(DEFAULT_SOURCE.parents[4])]
    probe = subprocess.run(mounts + sandbox_probe, env=env, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise RuntimeError(f'Public-only filesystem check failed ({probe.returncode}): {probe.stderr.strip()}')
    return probe.stdout.strip()


def model_command(model, effort):
    return ['/mnt/codex-bin/codex', '-a', 'never', 'exec', '--ignore-user-config', '--ignore-rules',
            '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--json',
            '--color', 'never', '--model', model,
            '-c', f'model_reasoning_effort={json.dumps(effort)}',
            '-c', 'sandbox_workspace_write.network_access=false',
            '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"',
            '-c', 'features.multi_agent=false', '-c', 'features.multi_agent_v2=false',
            '-c', 'features.apps=false', '-c', 'features.code_mode_host=true',
            '-c', 'features.shell_snapshot=false',
            '--output-last-message', '/mnt/model-output/completion.txt', '-']


def event_summary(path):
    usage, errors, tool_events, threads = [], [], [], []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append({'type': 'malformed event stream'})
            continue
        kind, item = event.get('type'), event.get('item') or {}
        if kind == 'thread.started':
            threads.append(event.get('thread_id'))
        elif kind == 'turn.completed':
            usage.append(event.get('usage'))
        elif kind in {'error', 'turn.failed'} or item.get('type') == 'error':
            errors.append(event)
        elif kind == 'item.completed' and item.get('type') in {'command_execution', 'mcp_tool_call', 'file_change', 'web_search'}:
            tool_events.append({'type': item['type'], 'status': item.get('status'), 'exit_code': item.get('exit_code')})
    return {'thread_ids': threads, 'usage': usage, 'runtime_events': errors, 'tool_events': tool_events}


def edit_scope(office_files, editable_files):
    if editable_files is None:
        return 'Edit the three existing native Office files in the current directory as required. '
    if not editable_files or not set(editable_files) <= set(office_files):
        raise ValueError('Editable files must be listed Office files')
    editable = ', '.join(editable_files)
    evidence = ', '.join(file for file in office_files if file not in editable_files)
    if not evidence:
        return f'Edit only these files as requested: {editable}. '
    return (f'Edit and submit only {editable}. '
            f'The following files are read-only evidence: {evidence}. Leave their contents and files unchanged. ')


def run_layout(name, frozen, output, route, effort, timeout, preflight_only, *, response_only=False):
    package = Path(frozen['package'])
    office_files = tuple(frozen.get('office_files', OFFICE_FILES))
    names = office_files + ('instruction.md',)
    output = output / name
    workspace, completion = output / 'submission', output / 'completion'
    prepare_workspace(package / 'public', workspace, names)
    initial = file_hashes(workspace, names)
    reference = package / 'private' / 'reference'
    oracle_path = package / 'private' / frozen.get('oracle_file', 'oracle.json')
    if (initial != frozen['public_sha256'] or digest(oracle_path) != frozen['oracle_sha256']
            or file_hashes(reference, office_files) != frozen['reference_sha256']):
        raise ValueError('Frozen source changed before trial')
    completion.mkdir()

    codex = shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex is not installed')
    codex_file = Path(codex).resolve()
    auth_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    prompt = (workspace / 'instruction.md').read_text(encoding='utf-8')
    if not response_only:
        prompt += ('\n\n' + edit_scope(office_files, frozen.get('editable_files'))
                   + 'Use the Office libraries available in Python when useful. Preserve unrelated content and native chart data. '
                   'The workspace files and the task instruction are your evidence. Save the edited files in place. '
                   'A final chat message does not replace the modified files.')
    (output / 'prompt.txt').write_text(prompt, encoding='utf-8')

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='hotel-edit-auth-') as temp:
        auth = Path(temp)
        shutil.copy2(auth_home / 'auth.json', auth / 'auth.json')
        mounts = sandbox_arguments(workspace, completion, auth, codex_file)
        env = isolated_env()
        isolation = isolated_check(mounts, env, package, output, names)
        if preflight_only:
            return {'layout': name, 'status': 'PREFLIGHT_PASSED', 'isolation': isolation,
                    'public_sha256': initial, 'wall_seconds': time.monotonic() - start}
        events = output / 'events.jsonl'
        with events.open('w', encoding='utf-8') as stdout, (output / 'stderr.log').open('w', encoding='utf-8') as stderr:
            print('HOTEL_AGENT_STARTED', name, str(output), flush=True)
            try:
                result = subprocess.run(mounts + model_command(route['model'], effort), input=prompt,
                                        text=True, stdout=stdout, stderr=stderr, env=env, timeout=timeout)
                returncode = result.returncode
            except subprocess.TimeoutExpired:
                returncode = 'timeout'

    execution_wall = time.monotonic() - start
    unchanged = (frozen['public_sha256'] == file_hashes(package / 'public', names)
                 and frozen['oracle_sha256'] == digest(oracle_path))
    reference_unchanged = frozen['reference_sha256'] == file_hashes(reference, office_files)
    outputs = file_hashes(workspace, office_files)
    inventory = {entry.name for entry in workspace.iterdir()}
    report = {'layout': name, 'model': route['model'], 'effort': effort, 'wall_seconds': execution_wall,
              'returncode': returncode, 'originals_and_oracle_unchanged': unchanged, 'isolation': isolation,
              'reference_unchanged': reference_unchanged,
              'public_sha256': initial, 'submission_sha256': outputs,
              'modified_office_files': [file for file in office_files if outputs.get(file) != initial[file]],
              'missing_office_files': sorted(set(office_files)-outputs.keys()),
              'extra_workspace_entries': sorted(inventory-set(names)),
              'trace': event_summary(events)}
    if not unchanged or not reference_unchanged:
        report.update(status='SOURCE_CHANGED', native_pass=None)
    elif returncode != 0 or report['trace']['runtime_events']:
        report.update(status='RUN_ERROR', native_pass=None)
    elif not report['trace']['usage']:
        report.update(status='INCOMPLETE_EVENT_STREAM', native_pass=None)
    elif response_only:
        changed = initial != file_hashes(workspace, names)
        report.update(status='ANSWER_INPUT_CHANGED' if changed else 'COMPLETED', native_pass=None)
    elif report['missing_office_files']:
        report.update(status='COMPLETED', native_pass=False, reason='missing or non-regular native submission')
    else:
        score = grade(workspace, json.loads(oracle_path.read_text(encoding='utf-8')))
        save(output / 'native_evaluation.json', score)
        report.update(status='COMPLETED', native_pass=score['pass'], score_groups=score['groups'])
    save(output / 'run.json', report)
    return report


def main():
    from alignment_probe.codex_transport import configured_route

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--layout', action='append', choices=LAYOUTS)
    parser.add_argument('--single-case', action='store_true', help='Read public and private files directly under --source')
    parser.add_argument('--validation', type=Path, help='Validated case or four-layout suite report')
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--effort', default='medium')
    parser.add_argument('--require-model', help='Verify the configured model matches this requested baseline')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.timeout <= 0 or not 1 <= args.workers <= 4:
        parser.error('timeout must be positive and workers must be in [1, 4]')
    if args.single_case and args.layout:
        parser.error('--layout applies only to the four-layout suite')
    source = args.source.resolve()
    validation = (args.validation or source / 'summary.json').resolve()
    if not args.preflight_only:
        authoring = json.loads(validation.read_text(encoding='utf-8'))
        if authoring.get('passed') is not True:
            raise ValueError('Authoring validation must pass before Agent editing')
        if not args.single_case:
            records = authoring.get('layouts', {})
            if set(records) != set(LAYOUTS) or not all(record.get('passed') is True for record in records.values()):
                raise ValueError('Four-layout validation must pass before Agent editing')
    if args.single_case:
        selected = (source.name,)
        frozen = freeze_packages({source.name: source}, original_oracle=True)
    else:
        selected = tuple(dict.fromkeys(args.layout or LAYOUTS))
        frozen = freeze_layouts(source, selected)
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login']:
        raise RuntimeError('The native runner uses the configured official Codex login')
    if args.require_model and route['model'] != args.require_model:
        raise RuntimeError(f"Expected configured model {args.require_model}, found {route['model']}")
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.out or source / 'agent_runs' / stamp).resolve()
    output.mkdir(parents=True)
    save(output / 'plan.json', {'model': route['model'], 'effort': args.effort, 'timeout_seconds': args.timeout,
                               'layouts': frozen, 'validation_report': str(validation),
                               'validation_sha256': digest(validation) if validation.exists() else None,
                               'preflight_only': args.preflight_only, 'public_files': list(PUBLIC_NAMES),
                               'copies': 'Fresh byte-identical writable copies in each isolated run',
                               'scoring': 'Native object Oracle runs in the host after the editing session',
                               'exposure': 'Three native Office files and natural-language instruction, plus tools runtime. Benchmark graphs, mappings and reference files stay outside the mounts.'})
    results = {}
    with ThreadPoolExecutor(max_workers=min(args.workers, len(selected))) as pool:
        jobs = {pool.submit(run_layout, name, frozen[name], output, route, args.effort,
                            args.timeout, args.preflight_only): name for name in selected}
        for job in as_completed(jobs):
            name = jobs[job]
            try:
                results[name] = job.result()
            except Exception as exc:
                results[name] = {'layout': name, 'status': 'RUN_ERROR', 'native_pass': None,
                                 'error': type(exc).__name__ + ': ' + str(exc)}
            save(output / 'results.json', {layout: results[layout] for layout in selected if layout in results})
            print('HOTEL_AGENT_RESULT', name, results[name]['status'], results[name].get('native_pass'), flush=True)
    print('HOTEL_AGENT_OUTPUT', str(output), flush=True)
    if any(record['status'] not in {'COMPLETED', 'PREFLIGHT_PASSED'} for record in results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
