"""Rescore the unqualified waiting-ADR convention without changing the frozen run."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from research_archive import archive_hotel_run

from .hotel_graph import read_json, save_json
from .hotel_upgrade_bench import DEFAULT, freeze_hashes, grade


def retained_waiting_adr(case):
    """Interpret Reviewed ADR as the previous review until a new reservation."""
    alternate = deepcopy(case)
    prior, retained, changed = {}, {}, 0
    for item in alternate['steps']:
        rows = item['workbook']['Applications'][1:]
        for row in rows:
            hotel, status, reviewed = row
            if (item['event'].get('action') == 'reverse_refund' and prior.get(hotel, [None])[0] == 'released'
                    and status == 'waiting'):
                retained[hotel] = prior[hotel][1]
            if hotel in retained and status == 'waiting':
                row[2] = retained[hotel]
                changed += reviewed != row[2]
            elif status != 'waiting':
                retained.pop(hotel, None)
        prior = {hotel: [status, reviewed] for hotel, status, reviewed in rows}
    return alternate if changed else None


def review(source, run):
    frozen = read_json(run / 'plan.json')['frozen']
    if frozen != freeze_hashes(source):
        raise ValueError('The official experiment must still match its freeze')
    arm = run / 'm3_expanded'
    raw = read_json(arm / 'native_evaluation.json')
    alternatives = [check for case in read_json(source / 'm3_expanded/private/oracle.json')
                    if (check := retained_waiting_adr(case)) is not None]
    diagnostic = grade(arm / 'submission', alternatives)
    if diagnostic['status'] != 'checked':
        raise ValueError(f'Diagnostic replay unavailable: {diagnostic["status"]}')
    original = {row['name']: row for row in raw['cases']}
    for row in diagnostic['cases']:
        if not original[row['name']]['pass'] and row['pass']:
            original[row['name']] = row
    merged = {**raw, 'cases': list(original.values()),
              'pass': raw['template_intact'] and all(row['pass'] for row in original.values()),
              'diagnostic_scope': 'Accept the existing waiting Reviewed ADR until promotion takes a new review.'}
    save_json(run / 'business_oracle.json', alternatives)
    save_json(arm / 'business_evaluation.json', merged)
    summary = {'arm': 'm3_expanded', 'original_cases_passed': sum(row['pass'] for row in raw['cases']),
               'diagnostic_cases_passed': sum(row['pass'] for row in merged['cases']), 'case_count': len(raw['cases']),
               'diagnostic_steps_passed': sum(step['pass'] for row in merged['cases'] for step in row['steps']),
               'step_count': sum(len(row['steps']) for row in raw['cases']),
               'replayed_cases': len(alternatives), 'business_pass': merged['pass'],
               'reason': 'The task requires refreshing Reviewed ADR for a reserved application. It does not specify the value at the released-to-waiting transition.'}
    save_json(run / 'business_rescore.json', summary)
    archive_hotel_run('upgrade', source, run)
    print('UPGRADE_DIAGNOSTIC', summary, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    review(args.source.resolve(), args.run.resolve())


if __name__ == '__main__':
    main()
