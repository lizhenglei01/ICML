"""Compare scored tests with observed outcomes in a SWE-Milestone trial."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

from research_archive import digest, read


def checkpoint_order(trial):
    log = (trial / 'orchestrator.log').read_text(encoding='utf-8')
    submitted = re.findall(r'\b(milestone_[\w-]+): New tag detected, starting debounce', log)
    evaluated = re.findall(r'\b(milestone_[\w-]+): Debounce complete.*starting evaluation', log)
    if not submitted or submitted != evaluated or len(set(submitted)) != len(submitted):
        raise ValueError('Submission and evaluation logs do not establish an unambiguous order. Supply --order.')
    return submitted


def test_id(name, go_module):
    if go_module and name.startswith(f'{go_module}/'):
        return name.removeprefix(f'{go_module}/')
    return name


def observed_outcomes(results, go_module):
    outcomes = defaultdict(set)
    for status in ('passed', 'failed', 'error', 'xpassed', 'xfailed'):
        for test in results.get(status, []):
            name = test if isinstance(test, str) else test['nodeid']
            outcomes[test_id(name, go_module)].add(status)
    for group in results.get('skipped', []):
        for name in group['tests']:
            outcomes[test_id(name, go_module)].add('skipped')
    return {name: next(iter(statuses)) if len(statuses) == 1 else 'conflicting'
            for name, statuses in outcomes.items()}


def evaluate(directory, previous, go_module, *, filtered=True):
    raw_path = directory / 'evaluation_result.json'
    raw = read(raw_path)
    filtered_path = directory / 'evaluation_result_filtered.json'
    score_path = filtered_path if filtered and filtered_path.is_file() else raw_path
    score = read(score_path) if score_path != raw_path else raw
    identity = raw['scoring_identity']
    payload = directory / identity['payload_path']
    actual_digest = digest(payload)
    if identity.get('payload_sha256') and identity['payload_sha256'] != actual_digest:
        raise ValueError(f'Raw test payload changed: {payload}')
    observed = observed_outcomes(read(payload)['results'], go_module)
    statuses = score['tests_status']
    row = {
        'score_file': score_path.name,
        'resolved': score['resolved'],
        'old_reported_missing': statuses['PASS_TO_PASS'].get('missing'),
        'raw_payload_sha256': actual_digest,
        'scored_new_failures': [],
        'scored_old_failures': [],
        'excluded_failures': [],
    }
    for category in ('FAIL_TO_PASS', 'NONE_TO_PASS', 'PASS_TO_PASS'):
        key = 'scored_old_failures' if category == 'PASS_TO_PASS' else 'scored_new_failures'
        scored = set(statuses[category]['failure'])
        reported = scored | set(raw['tests_status'][category]['failure'])
        for name in sorted(reported):
            normalized = test_id(name, go_module)
            current = observed.get(normalized, 'absent')
            prior = previous.get(normalized, 'absent') if previous is not None else None
            destination = key if name in scored else 'excluded_failures'
            row[destination].append({'test': name, 'category': category,
                                     'observed': current, 'previous_observed': prior})
    return row, observed


def audit(trial, order, go_module, corrected=None):
    checkpoints = {path.name for path in (trial / 'evaluation').glob('milestone_*')
                   if (path / 'evaluation_result.json').is_file()}
    if len(set(order)) != len(order) or set(order) != checkpoints:
        raise ValueError('Checkpoint order must cover every recorded milestone exactly once.')
    records = {}
    previous = None
    review = None
    for name in order:
        record, observed = evaluate(trial / 'evaluation' / name, previous, go_module)
        if corrected and name == corrected[0]:
            review, _ = evaluate(corrected[1], previous, go_module, filtered=False)
        records[name] = record
        previous = observed
    if corrected and review is None:
        raise ValueError(f'Replay checkpoint is absent from the trial: {corrected[0]}')
    return {'submission_order': order, 'original': records,
            'diagnostic_replay': {'checkpoint': corrected[0], 'result': review} if corrected else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=Path)
    parser.add_argument('--order', nargs='+', help='Verified chronologically ordered milestone directory names')
    parser.add_argument('--go-module', help='Module prefix to omit from Go test IDs')
    parser.add_argument('--replay', nargs=2, metavar=('MILESTONE', 'EVALUATION_DIR'))
    args = parser.parse_args()
    replay = (args.replay[0], Path(args.replay[1])) if args.replay else None
    order = args.order or checkpoint_order(args.trial)
    print(json.dumps(audit(args.trial, order, args.go_module, replay), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
