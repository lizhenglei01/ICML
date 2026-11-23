"""Run the shared hotel prefix and the matched GPT-6.2-sol third milestones."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from alignment_probe.codex_transport import configured_route

from . import hotel_agent
from .hotel_dependency_scope import DEFAULT, PACKAGES, business_oracle, prepare_stage, validate_pair
from .hotel_graph import read_json, save_json
from .hotel_long_evolution_agent import evaluate
from .hotel_revenue_milestones_agent import freeze_carry

MODEL = 'gpt-6.2-sol'


def summarize(record, directory):
    score = next((read_json(directory / filename) for filename in (
        'submitted_files_rescore.json', 'native_evaluation.json') if (directory / filename).is_file()), None)
    if not score:
        return {'status': record['status'], 'pass': None}
    failed = [row for row in score['checks'] if not row['pass']]
    blocked = [row for row in failed if row['object'] == 'functional_excel:calculator']
    return {'status': record['status'], 'pass': score['pass'] if not blocked else None,
            'wall_seconds': round(record['wall_seconds'], 2),
            'formula_by_milestone': record['functional_by_milestone'], 'scores': score['groups'],
            'scorer_blocked': blocked, 'failed_checks': failed if not blocked else [],
            'attribution': record['attribution'] if not blocked else {}}


def run(source, output, timeout):
    if not read_json(source / 'summary.json')['passed']:
        raise ValueError('Validate the reference and negative controls before model calls')
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login']:
        raise RuntimeError('The official OpenAI route with saved ChatGPT login is required')
    route['model'] = MODEL
    packages = {name: source / name for name in PACKAGES}
    output.mkdir(parents=True)
    save_json(output / 'plan.json', {'model': MODEL, 'effort': 'medium', 'per_call_timeout_seconds': timeout,
        'shared_prefix': ['m1', 'm2'], 'branches': ['m3_a', 'm3_b'], 'planned_calls': 4,
        'starting_point_rule': 'Use the same actual M2 submission if M2 passes. Otherwise run both M3 arms from the same canonical M2 state.'})
    print('HOTEL_SCOPE_FREEZING', str(output), flush=True)
    canonical = hotel_agent.freeze_packages(packages, original_oracle=True)
    records = {}
    starts = {}

    def persist(name, record):
        records[name] = {**record, 'starting_point': starts[name], 'summary': summarize(record, output / name)}
        save_json(output / 'results.json', records)
        print('HOTEL_SCOPE_RESULT', name, records[name]['summary']['status'], records[name]['summary']['pass'], flush=True)
        return records[name]

    def inherited(name, previous):
        if records.get(previous, {}).get('summary', {}).get('pass') is True:
            actual = freeze_carry(output / previous / 'submission', packages[name],
                                  output / 'carried_inputs' / name, canonical[name], prepare_stage=prepare_stage)
            starts[name] = f'actual_{previous}_submission'
            return actual
        starts[name] = 'canonical_predecessor' if previous else 'original_workspace'
        return canonical[name]

    for name, predecessor in [('m1', None), ('m2', 'm1')]:
        frozen = inherited(name, predecessor)
        persist(name, evaluate(name, frozen, output, route, timeout))
    paired = {name: inherited(name, 'm2') for name in ('m3_a', 'm3_b')}
    a, b = (Path(item['package']) for item in paired.values())
    changes = validate_pair(a, b)
    if starts['m3_a'] != starts['m3_b']:
        raise ValueError('The A/B pair must share the same predecessor')
    save_json(output / 'comparison.json', {'same_prompt_and_oracle': True, 'same_predecessor': starts['m3_a'],
        'differing_native_objects': changes, 'frozen': paired})
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(evaluate, name, frozen, output, route, timeout): name
                for name, frozen in paired.items()}
        for job in as_completed(jobs):
            persist(jobs[job], job.result())
    print('HOTEL_SCOPE_COMPLETE', str(output), flush=True)


def review_run(source, output):
    """Keep the original scores and recompute the same submissions against business behavior."""
    runs = read_json(output / 'results.json')
    oracle = business_oracle(read_json(source / 'm3_a/private/oracle.json'))
    controls = read_json(source / 'summary.json')['controls']
    controls['central_edit_only'] = {}
    safety = {}
    for name in sorted(controls):
        control = hotel_agent.grade(source / 'controls' / name, oracle)
        if control['pass'] or control['groups']['structure']['total']:
            raise ValueError(f'The revised contract did not correctly reject {name}')
        safety[name] = [item['object'] for item in control['checks'] if not item['pass']]
    review = {}
    for arm in ('m3_a', 'm3_b'):
        gold = hotel_agent.grade(source / arm / 'private/reference', oracle)
        if not gold['pass']:
            raise ValueError(f'Revised contract rejected the {arm} reference')
        score = hotel_agent.grade(output / arm / 'submission', oracle)
        save_json(output / arm / 'business_evaluation.json', score)
        original = runs[arm]['summary']
        review[arm] = {
            'strict_pass': original['pass'], 'business_pass': score['pass'],
            'business_groups': score['groups'],
            'ignored_original_checks': sorted({item['object'] for item in original['failed_checks']}
                                              - {item['object'] for item in score['checks']}),
            'failed_business_checks': [item['object'] for item in score['checks'] if not item['pass']],
        }
    save_json(output / 'business_oracle.json', oracle)
    save_json(output / 'business_rescore.json', {'arms': review, 'rejected_controls': safety,
        'reason': 'No output contract for numerical renewal eligibility on rows for which only new signing applies. '
                  'All workflow decisions and published artifacts remain checked. Shared renewal formulas are still required.'})
    print('HOTEL_SCOPE_BUSINESS_RESCORE', {name: row['business_pass'] for name, row in review.items()}, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--review-run', type=Path)
    args = parser.parse_args()
    if args.review_run:
        review_run(args.source.resolve(), args.review_run.resolve())
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or args.source / 'agent_runs' / ('gpt62_scope_' + stamp)
    run(args.source.resolve(), output.resolve(), args.timeout)


if __name__ == '__main__':
    main()
