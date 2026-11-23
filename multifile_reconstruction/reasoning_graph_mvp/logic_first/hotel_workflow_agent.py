"""Run GPT-6.2-sol on the shared workflow prefix and integration comparison."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from alignment_probe.codex_transport import configured_route

from .hotel_agent import freeze_packages
from .hotel_dependency_scope_agent import MODEL, summarize
from .hotel_graph import read_json, save_json
from .hotel_long_evolution_agent import evaluate
from .hotel_revenue_milestones_agent import freeze_carry
from .hotel_workflow_bench import DEFAULT, FILES, PACKAGES, audit_pair, prepare_stage, trace_integration
from .hotel_workflow_cases import COLS, REVIEW


def feature_summary(report):
    labels = dict(COLS)
    result = {}
    for case in report.get('failed_checks', []):
        object_id = case['object']
        if not object_id.startswith('functional_excel:') or f'{REVIEW}!' not in object_id:
            continue
        _, world, address = object_id.split(':')
        column = address.split('!')[1].rstrip('0123456789')
        group = result.setdefault(labels[column], {})
        group.setdefault(world, []).append(address)
    return result


def compare_submissions(source, output):
    diagnostic_worlds = set(read_json(source / 'summary.json')['verified_business_examples']['diagnostic_handoff_worlds'])
    labels = {column: (i // 10 + 1, label) for i, (column, label) in enumerate(COLS)}
    result = {}
    for arm, (_, mode) in PACKAGES.items():
        directory = output / arm
        path = next((directory / name for name in ('submitted_files_rescore.json', 'native_evaluation.json')
                     if (directory / name).is_file()), None)
        if not path:
            continue
        native = read_json(path)
        cases = {}
        for row in native['checks']:
            fields = row['object'].split(':')
            if fields[0] != 'functional_excel' or len(fields) != 3:
                continue
            _, scenario, address = fields
            if not address.startswith(f'{REVIEW}!'):
                continue
            col = address.split('!')[1].rstrip('0123456789')
            stage, label = labels[col]
            cases.setdefault((stage, label, scenario), []).append(row['pass'])
        bins = {}
        failed = []
        for (stage, label, scenario), checks in cases.items():
            name = 'handoff_diagnostic' if scenario in diagnostic_worlds else f'm{stage}'
            totals = bins.setdefault(name, {'passed': 0, 'total': 0})
            totals['passed'] += all(checks)
            totals['total'] += 1
            if not all(checks):
                failed.append({'milestone': stage, 'feature': label, 'scenario': scenario})
        row = {'feature_scenario_pass_counts': bins, 'failures': failed, 'full_native_pass': native['pass']}
        if arm.startswith('m3_'):
            trace = trace_integration(directory / 'submission' / FILES['excel'], mode)
            save_json(directory / 'integration_trace.json', trace)
            row['integration_trace_pass'] = trace['pass']
        result[arm] = row
    save_json(output / 'comparison_metrics.json', result)
    comparison = read_json(output / 'comparison.json')
    starts = [Path(comparison[arm]['inputs']['package']) / 'public' for arm in ('m3_manual', 'm3_linked')]
    save_json(output / 'paired_input_audit.json', audit_pair(*starts))
    return result


def run(source, output, timeout):
    if not read_json(source / 'summary.json')['passed']:
        raise ValueError('Validate the build and fault controls before model calls')
    route = configured_route()
    if route['provider'] != 'openai' or not route['use_saved_chatgpt_login']:
        raise RuntimeError('Require the official OpenAI route with saved ChatGPT login')
    route['model'] = MODEL
    packages = {label: source / label for label in PACKAGES}
    output.mkdir(parents=True)
    save_json(output / 'plan.json', {'model': MODEL, 'effort': 'medium', 'timeout_seconds': timeout,
        'shared_prefix': ['m1', 'm2'], 'branches': ['m3_manual', 'm3_linked'],
        'starting_point': 'Carry the actual predecessor after a passing score. Use canonical controls after a nonpassing or invalid predecessor.'})
    print('HOTEL_WORKFLOW_FREEZING', str(output), flush=True)
    canonical = freeze_packages(packages, original_oracle=True)
    records = {}

    def starting_point(label, prior):
        if records.get(prior, {}).get('summary', {}).get('pass') is True:
            actual = freeze_carry(output / prior / 'submission', packages[label],
                                  output / 'carried_inputs' / label, canonical[label], prepare_stage=prepare_stage)
            return actual, f'actual_{prior}_submission'
        return canonical[label], 'canonical_predecessor' if prior else 'original_workspace'

    def finish(label, record, start):
        report = summarize(record, output / label)
        records[label] = {**record, 'starting_point': start, 'summary': report,
                          'feature_failures': feature_summary(report)}
        save_json(output / 'results.json', records)
        print('HOTEL_WORKFLOW_RESULT', label, report['status'], report['pass'], flush=True)

    for name, prior in [('m1', None), ('m2', 'm1')]:
        frozen, start = starting_point(name, prior)
        finish(name, evaluate(name, frozen, output, route, timeout), start)

    pair = {name: starting_point(name, 'm2') for name in ('m3_manual', 'm3_linked')}
    if len({start for _, start in pair.values()}) != 1:
        raise ValueError('The matched pair must share its predecessor')
    save_json(output / 'comparison.json', {label: {'inputs': frozen, 'starting_point': start}
                                            for label, (frozen, start) in pair.items()})
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = {pool.submit(evaluate, label, frozen, output, route, timeout): (label, start)
                 for label, (frozen, start) in pair.items()}
        for call in as_completed(calls):
            name, start = calls[call]
            finish(name, call.result(), start)
    compare_submissions(source, output)
    print('HOTEL_WORKFLOW_COMPLETE', str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--review-run', type=Path)
    args = parser.parse_args()
    if args.review_run:
        compare_submissions(args.source.resolve(), args.review_run.resolve())
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or args.source / 'agent_runs' / ('gpt62_workflow_' + stamp)
    run(args.source.resolve(), output.resolve(), args.timeout)


if __name__ == '__main__':
    main()
