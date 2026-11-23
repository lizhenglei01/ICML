"""Run one shared prefix and a frozen, matched third-round upgrade."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2
from threading import Lock

from docx import Document
from docx.opc.exceptions import PackageNotFoundError as WordPackageError
from pptx import Presentation
from pptx.exc import PackageNotFoundError as SlidePackageError

from alignment_probe.codex_transport import configured_route
from research_archive import archive_hotel_run, capture_run_environment

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_stateful_agent import MODEL, diagnostics, score_for
from .hotel_stateful_bench import STATE_READ_ERRORS
from .hotel_upgrade_bench import BOOK, DEFAULT, INPUTS, OFFICE, PACKAGES, PROGRAM, REPORTS, freeze_hashes, grade, read_state


def prepare(source, name, destination, previous):
    canonical = source / name
    if name == 'm1':
        return canonical
    hotel_agent.check_public(canonical / 'public', INPUTS)
    public = destination / 'public'
    if previous is None or set(hotel_agent.file_hashes(previous, (*OFFICE, PROGRAM))) != {*OFFICE, PROGRAM}:
        return None
    try:
        read_state(previous / BOOK)
        Document(previous / REPORTS[0])
        Presentation(previous / REPORTS[1])
        (previous / PROGRAM).read_text(encoding='utf-8')
    except (*STATE_READ_ERRORS, WordPackageError, SlidePackageError):
        return None
    public.mkdir(parents=True)
    for file in (*OFFICE, PROGRAM):
        copy2(previous / file, public / file)
    copy2(canonical / 'public/instruction.md', public / 'instruction.md')
    (destination / 'private').symlink_to(canonical / 'private', target_is_directory=True)
    return destination


def freeze(package):
    private = package / 'private'
    files = (*OFFICE, PROGRAM)
    return {'package': str(package), 'office_files': files, 'editable_files': (PROGRAM,),
            'public_sha256': hotel_agent.file_hashes(package / 'public', INPUTS),
            'reference_sha256': hotel_agent.file_hashes(private / 'reference', files),
            'oracle_sha256': hotel_agent.digest(private / 'oracle.json')}


def score_summary(score, common):
    if not score or score['status'] != 'checked':
        return {}
    by_case = score['cases']
    return {name: {'passed': sum(c['pass'] for c in cells), 'total': len(cells)}
            for name, cells in (
                ('matched_cases', [c for c in by_case if c['name'] in common]),
                ('expanded_only', [c for c in by_case if c['name'] not in common]))}


def run(source, output, timeout):
    summary = read_json(source / 'summary.json')
    if not summary['passed'] or summary['freeze'] != freeze_hashes(source):
        raise ValueError('Task, reference or scorer changed after validation')
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login']:
        raise RuntimeError('Use the official OpenAI route with saved ChatGPT login')
    route['model'] = MODEL
    output.mkdir(parents=True)
    save_json(output / 'plan.json', {'model': MODEL, 'effort': 'medium', 'shared_prefix': ['m1', 'm2'],
        'environment_at_run': capture_run_environment('upgrade'), 'frozen': summary['freeze'],
        'm3_arms': ['m3_narrow', 'm3_expanded'], 'controls': 'Same actual M2, input assets and shared cases.'})
    common = {case['name'] for case in read_json(source / 'm3_narrow/private/oracle.json')}
    records, lock = {}, Lock()

    def evaluated(name, frozen, prior_score):
        stage, arm = PACKAGES[name]
        directory = output / name
        if frozen:
            record = hotel_agent.run_layout(
                name, frozen, output, route, 'medium', timeout, False, grader=grade,
                task_suffix='Implement approval.py. Test on copies. Deliver the intact template and executable program.',
            )
            score = score_for(record, directory, Path(frozen['package']), stage,
                              grader=lambda path, _, oracle: grade(path, oracle))
        else:
            record, score = {'status': 'CHAIN_INTERRUPTED'}, None
        with lock:
            records[name] = {**record, 'starting_point': ('original_workspace' if stage == 1 else 'actual_predecessor') if frozen
                             else 'chain_interrupted', 'arm': arm,
                             'business_score': score['pass'] if score and score['status'] == 'checked' else None,
                             **diagnostics(score, stage, prior_score), **score_summary(score, common)}
            save_json(output / 'results.json', records)
        print('HOTEL_UPGRADE_MODEL_RESULT', name, records[name]['business_score'], record['status'], flush=True)
        return directory / 'submission' if (directory / 'submission').is_dir() else None, score

    previous, prior_score = None, None
    for name in ('m1', 'm2'):
        package = prepare(source, name, output / 'carried' / name, previous)
        previous, prior_score = evaluated(name, freeze(package) if package else None, prior_score)
    branches = ('m3_narrow', 'm3_expanded')
    starts = {name: prepare(source, name, output / 'carried' / name, previous) for name in branches}
    if all(starts.values()):
        fingerprints = {name: hotel_agent.file_hashes(path / 'public', (*OFFICE, PROGRAM)) for name, path in starts.items()}
        if fingerprints[branches[0]] != fingerprints[branches[1]]:
            raise ValueError('Third-round inputs outside the instruction differ')
        save_json(output / 'paired_input_audit.json', {'matched_files': fingerprints[branches[0]],
                                                       'shared_cases': len(common)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluated, name, freeze(path) if path else None, prior_score)
                   for name, path in starts.items()]
        for future in futures:
            future.result()
    if summary['freeze'] != freeze_hashes(source):
        raise ValueError('Task or scoring code changed during the run')
    archive_hotel_run('upgrade', source, output)
    print('HOTEL_UPGRADE_COMPLETE', output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or args.source / 'agent_runs' / ('gpt62_upgrade_' + stamp)
    run(args.source.resolve(), output.resolve(), args.timeout)


if __name__ == '__main__':
    main()
