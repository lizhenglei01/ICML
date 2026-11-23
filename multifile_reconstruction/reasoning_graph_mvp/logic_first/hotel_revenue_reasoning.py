"""Compare a written answer with native edits on the same frozen hotel correction."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
from shutil import copy2, copytree

from alignment_probe.codex_transport import configured_route

from . import hotel_agent
from .hotel_graph import read_json, save_json
from .hotel_revenue_sequence import DEFAULT
from .hotel_revenue_sequence_agent import EFFORT, MODEL


ANSWER = 'answer_only'
EDIT = 'native_edit'
WORD_RESULTS = (
    'peer_cohort', 'peer_adr_mean', 'review_adr_floor',
    'qualified', 'qualified_count', 'qualified_adr_mean',
)
ANSWER_PROMPT = """Read the three Office files and apply the policy to the full roster after the correction.
Return one JSON object in your final message with these business results:
- peer_cohort: list of hotels in the price comparison group.
- peer_adr_mean: mean daily room rate of that group, in yuan.
- review_adr_floor: the minimum qualifying room rate, in yuan.
- qualified: list of qualifying hotels.
- qualified_count: number of qualifying hotels.
- qualified_adr_mean: unweighted mean daily room rate of qualifying hotels, in yuan.
- changed_qualifications: list of hotels whose qualification flag changes.
- unchanged_qualifications: list of hotels whose qualification flag stays the same.

Use the full hotel names printed in the files. Leave all three Office files unchanged.
The workspace files and the task instruction are your evidence. Use the available Python Office libraries if useful.
"""


def reasoning_package(stage, destination):
    public = destination / 'public'
    public.mkdir(parents=True)
    private = destination / 'private'
    private.mkdir()
    for name in hotel_agent.OFFICE_FILES:
        copy2(stage / 'public' / name, public / name)
    for name in ('oracle.json', 'native_manifest.json'):
        copy2(stage / 'private' / name, private / name)
    copytree(stage / 'private' / 'reference', private / 'reference')
    # Keep the original correction and incoming-state context in both languages.
    title, context_cn, edit_cn, context_en, edit_en = (
        (stage / 'public' / 'instruction.md').read_text(encoding='utf-8').strip().split('\n\n')
    )
    if not edit_cn.startswith('请依据材料') or not edit_en.startswith('Apply the policy'):
        raise ValueError('Unexpected source instruction structure')
    (public / 'instruction.md').write_text('\n\n'.join((title, context_cn, context_en, ANSWER_PROMPT)), encoding='utf-8')
    return destination


def same_value(observed, expected):
    if isinstance(expected, list):
        return isinstance(observed, list) and all(isinstance(name, str) for name in observed) and sorted(observed) == sorted(expected)
    try:
        return isinstance(observed, (str, int, float)) and Fraction(str(observed)) == Fraction(str(expected))
    except (ValueError, ZeroDivisionError):
        return False


def score_answer(text, case):
    text = text.strip()
    if text.startswith(('```json\n', '```\n')) and text.endswith('\n```'):
        text = text.split('\n', 1)[1].removesuffix('\n```')
    try:
        answer = json.loads(text)
        if not isinstance(answer, dict):
            raise ValueError('Expected a JSON object')
    except ValueError as exc:
        return {'pass': False, 'parse_error': str(exc), 'checks': []}
    after = case['after']
    changed = set(case['before']['qualified']) ^ set(after['qualified'])
    expected = {key: after[key] for key in WORD_RESULTS}
    expected['changed_qualifications'] = sorted(changed)
    expected['unchanged_qualifications'] = sorted(after['sold_room_nights'].keys() - changed)
    checks = [{'node_id': key, 'expected': value, 'observed': answer.get(key),
               'pass': same_value(answer.get(key), value)} for key, value in expected.items()]
    return {'pass': all(item['pass'] for item in checks), 'checks': checks,
            'common_business_targets': {'passed': sum(item['pass'] for item in checks if item['node_id'] in WORD_RESULTS),
                                        'total': len(WORD_RESULTS)}}


def edit_business_checks(evaluation, oracle):
    ids = {row['semantic_node_id']: row['evidence_id'] for row in oracle['bindings']
           if row['file'] == 'hotel_policy.docx' and row['role'] == 'output'}
    checks = {row['object']: row for row in evaluation['checks']}
    common = [{'node_id': key, **checks[ids[key]]} for key in WORD_RESULTS]
    return {'passed': sum(item['pass'] for item in common), 'total': len(common), 'checks': common}


def run(source, output, timeout):
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login'] or route['model'] != MODEL:
        raise RuntimeError(f'This comparison requires the official Codex login with {MODEL}')
    output.mkdir(parents=True)
    stage = source / 'stage_01'
    package = reasoning_package(stage, output / 'answer_source')
    frozen = hotel_agent.freeze_packages({ANSWER: package, EDIT: stage}, original_oracle=True)
    office_sha = {name: frozen[EDIT]['public_sha256'][name] for name in hotel_agent.OFFICE_FILES}
    if any(hashes['public_sha256'][name] != digest for hashes in frozen.values() for name, digest in office_sha.items()):
        raise ValueError('The comparison arms received different native evidence')
    save_json(output / 'plan.json', {'fresh_thread_per_arm': True, 'model': MODEL, 'effort': EFFORT,
                                      'shared_office_sha256': office_sha, 'layouts': frozen})

    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        trials = {pool.submit(hotel_agent.run_layout, name, frozen[name], output, route, EFFORT, timeout, False,
                              response_only=name == ANSWER): name for name in (ANSWER, EDIT)}
        for future in as_completed(trials):
            name = trials[future]
            result = future.result()
            results[name] = result
            if result['status'] == 'COMPLETED' and name == ANSWER:
                completion = output / name / 'completion' / 'completion.txt'
                score = score_answer(completion.read_text(encoding='utf-8') if completion.is_file() else '',
                                     read_json(stage / 'private' / 'case.json'))
                save_json(output / name / 'answer_evaluation.json', score)
                result['answer_pass'] = score['pass']
                result['common_business_targets'] = score.get('common_business_targets')
            if result['status'] == 'COMPLETED' and name == EDIT:
                report = output / name / 'native_evaluation.json'
                if report.is_file():
                    result['common_business_targets'] = edit_business_checks(read_json(report), read_json(stage / 'private' / 'oracle.json'))
            save_json(output / 'results.json', results)
            print('HOTEL_REASONING_RESULT', name, result['status'], str(output), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.out or args.source / 'agent_runs' / f'reasoning_vs_edit_{stamp}'
    run(args.source.resolve(), output.resolve(), args.timeout)


if __name__ == '__main__':
    main()
