"""Build and grade persistent Excel workflows by replaying business actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from shutil import copy2, which
from subprocess import TimeoutExpired, run
import sys
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_milestone_formulas import WORKBOOK_ERRORS
from .hotel_stateful_reference import reference_source

DEFAULT = ARTIFACTS / 'hotel_stateful_actions'
BOOK, PROGRAM = 'approval.xlsx', 'approval.py'
PUBLIC = (BOOK, PROGRAM, 'instruction.md')
STATE_READ_ERRORS = (*WORKBOOK_ERRORS, KeyError)
TEMPLATE = {
    'Policy': [['Min Occupancy %', 'Min ADR in cents', 'Capacity'], [75, 12500, 1]],
    'Ledger': [['Hotel', 'Sold nights', 'Available nights', 'Net revenue in cents'],
               ['Alder', 80, 100, 1040000], ['Birch', 80, 100, 1024000], ['Cedar', 80, 100, 1056000],
               ['Dale', 80, 100, 880000]],
    'Applications': [['Hotel', 'Status', 'Reviewed ADR cents']],
    'Issued': [['Hotel', 'Signed ADR cents']],
    'Releases': [['Release ID', 'Released by hotel', 'Consumed by hotel']],
    'Refunds': [['Event ID', 'Hotel', 'Amount in cents']],
    'Finance notes': [['Ledger total', '=SUM(Ledger!D2:D5)'], ['Owner', 'Finance Operations']],
}


def task_text(stage):
    shared = """# Hotel approvals / 酒店审批

Implement this milestone in the existing **approval.py**. The command interface is
`python approval.py /path/to/approval.xlsx '<JSON action>'`.
Output exactly one JSON object on stdout, with one key `result` and the result code below. Exit normally for business
rejections. The grader starts a new process per action and reopens the same workbook after each call. Persist each
accepted action in that workbook before returning. Rejections must leave its business state unchanged.

Deliver approval.py and keep the supplied approval.xlsx as the initial template. For local testing use copies.
Use openpyxl and the standard library. Keep the sheet names, column headers, row order and Finance notes intact.
The approved inputs are in Policy and Ledger. Currency is integer cents. ADR is net revenue divided by sold nights
with the fraction rounded down. A hotel is eligible when sold nights are positive and both occupancy and ADR meet their
minimums. An application is a row in Applications. Append records at the bottom.
"""
    stages = {
        1: """
**M1 — Request and formally issue.** Implement request and issue, each with a `hotel` field.

- `request`: reject unknown hotels with `unknown_hotel` and hotels with an application with `already_applied`.
  Then check eligibility (`ineligible`) and capacity (`no_capacity`) in that order. Each reserved or issued
  application occupies one seat. For success append [hotel, reserved, ADR] to Applications and return `reserved`.
- `issue`: require an existing reserved application (`not_reserved` otherwise). Check current eligibility
  (`ineligible` otherwise), set its status to issued, and append [hotel, current ADR] to Issued. Return `issued`.
  Issued rows are permanent signed snapshots. This action can issue an application at most once.
""",
        2: """
**M2 — Release and fill a seat.** Keep M1 request and issue working. Add these operations in the same command.

- `waitlist` with a hotel: check unknown hotel, existing application and eligibility in M1 request order.
  On success append [hotel, waiting, ADR] to Applications and return `waiting`. A waiting entry occupies no seat.
- `release` with a hotel: require a reserved application (`not_reserved` otherwise). Change its status to released
  and append a Releases row [next sequential integer ID from 1, hotel, blank]. Return `released`. A signed seat stays occupied.
- An unconsumed Releases row earmarks a seat. The existing `request` must return `no_capacity` while such a row exists.
- `promote` has no hotel field. It requires an actual unconsumed Releases row (`no_release` otherwise), then free
  physical capacity (`no_capacity` otherwise). Find the first currently eligible waiting entry in Applications row
  order (`no_candidate` otherwise). Change it to reserved with current ADR and write its name into the oldest unconsumed
  Releases row. Return `promoted`. Each release is usable once. A rejection leaves the release unused.
""",
        3: """
**M3 — Incorporate refunds into the existing approval workflow.** Keep all prior operations. Add `refund` with a
hotel, a string `id`, and an integer `amount` in cents. Unknown hotel returns `unknown_hotel`. An existing ID with
the same hotel and amount returns `duplicate`; a reused ID with other data returns `event_conflict`. A new amount
must be a positive integer no larger than the current net revenue, else return `invalid_amount`.

For success, subtract the amount from Ledger net revenue and append [id, hotel, amount] to Refunds. If the affected
application is currently reserved, update its Reviewed ADR with the new number. If it is now ineligible, release it
using the same persisted release path as the release operation. Return `refunded`. If already issued, leave the
application, signed ADR and occupied seat unchanged. Waiting candidates are rechecked when promotion runs.

Promotion remains a separate operation. It must consume the recorded release and can run on a later invocation.
All existing request, issue, release and promote actions must function with the updated state.
""",
    }
    return shared + ''.join(stages[n] for n in range(1, stage + 1))


def start_workbook(path):
    book = Workbook()
    book.remove(book.active)
    for title, rows in TEMPLATE.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='245270')
            sheet.column_dimensions[cell.column_letter].width = max(len(str(cell.value)) + 4, 22)
        sheet.freeze_panes = 'A2'
    book.save(path)
    book.close()


def step(action, result, hotel=None, *, expected=None, **inputs):
    return {'event': {'action': action, **({'hotel': hotel} if hotel else {}), **inputs},
            'result': result, 'replace': expected or {}}


def test_cases():
    """Manually checked state changes. This Oracle never imports action logic."""
    a = ['Alder', 'reserved', 13000]
    b = ['Birch', 'waiting', 12800]
    c = ['Cedar', 'waiting', 13200]
    reserve = step('request', 'reserved', 'Alder', expected={'Applications': [a]})
    reserve_b = step('request', 'reserved', 'Birch', expected={'Applications': [['Birch', 'reserved', 12800]]})
    wait = step('waitlist', 'waiting', 'Birch', expected={'Applications': [a, b]})
    signed = ['Alder', 'issued', 13000]
    issue = step('issue', 'issued', 'Alder', expected={'Applications': [signed], 'Issued': [['Alder', 13000]]})
    released = ['Alder', 'released', 13000]
    release = step('release', 'released', 'Alder', expected={'Applications': [released, b],
                                                           'Releases': [[1, 'Alder', None]]})
    promoted = ['Birch', 'reserved', 12800]
    promote = step('promote', 'promoted', expected={'Applications': [released, promoted],
                                                  'Releases': [[1, 'Alder', 'Birch']]})
    corrected = ['Alder', 'released', 11900]
    refund_rows = [['r1', 'Alder', 88000]]
    lower_ledger = [['Alder', 80, 100, 952000], *TEMPLATE['Ledger'][2:]]
    low_refund_before = step('refund', 'refunded', 'Alder', id='r1', amount=88000, expected={
        'Ledger': lower_ledger, 'Refunds': refund_rows, 'Applications': [corrected, b],
        'Releases': [[1, 'Alder', None]]})
    low_refund_after = step('refund', 'refunded', 'Alder', id='r1', amount=88000, expected={
        'Ledger': lower_ledger, 'Refunds': refund_rows})
    promote_after_refund = step('promote', 'promoted', expected={'Applications': [corrected, promoted],
                                                               'Releases': [[1, 'Alder', 'Birch']]})
    refund_birch = step('refund', 'refunded', 'Birch', id='r2', amount=80000, expected={
        'Ledger': [TEMPLATE['Ledger'][1], ['Birch', 80, 100, 944000], *TEMPLATE['Ledger'][3:]],
        'Refunds': [['r2', 'Birch', 80000]]})
    no_grant = {'block': 'Releases', 'replace': {'Releases': []}}
    grouped = {
        1: {
            'sign_and_preserve': [reserve, issue, step('issue', 'not_reserved', 'Alder'),
                                  step('request', 'no_capacity', 'Birch')],
            'approval_preconditions': [step('issue', 'not_reserved', 'Alder'), step('request', 'ineligible', 'Dale'),
                                       step('request', 'unknown_hotel', 'Unknown'), reserve_b,
                                       step('request', 'already_applied', 'Birch')],
        },
        2: {
            'release_consumed_once': [reserve, wait, step('promote', 'no_release'), release,
                                      step('request', 'no_capacity', 'Cedar'), promote,
                                      step('promote', 'no_release'), step('issue', 'issued', 'Birch', expected={
                                          'Applications': [released, ['Birch', 'issued', 12800]],
                                          'Issued': [['Birch', 12800]]})],
            'release_is_required': [reserve, wait, release, no_grant, step('promote', 'no_release')],
            'issued_seat_cannot_release': [reserve, issue, step('release', 'not_reserved', 'Alder'),
                                           step('promote', 'no_release')],
        },
        3: {
            'refund_then_attempt_to_sign': [reserve, wait, low_refund_before, step('issue', 'not_reserved', 'Alder'),
                                            promote_after_refund, step('refund', 'duplicate', 'Alder', id='r1', amount=88000),
                                            step('promote', 'no_release')],
            'sign_then_refund': [reserve, wait, step('issue', 'issued', 'Alder', expected={
                'Applications': [signed, b], 'Issued': [['Alder', 13000]]}), low_refund_after,
                step('release', 'not_reserved', 'Alder'), step('promote', 'no_release')],
            'refund_with_release_blocked': [reserve, wait, low_refund_before, no_grant, step('promote', 'no_release')],
            'refund_then_sign_at_boundary': [reserve, step('refund', 'refunded', 'Alder', id='edge', amount=40000,
                expected={'Ledger': [['Alder', 80, 100, 1000000], *TEMPLATE['Ledger'][2:]],
                          'Refunds': [['edge', 'Alder', 40000]], 'Applications': [['Alder', 'reserved', 12500]]}),
                step('issue', 'issued', 'Alder', expected={'Applications': [['Alder', 'issued', 12500]],
                                                           'Issued': [['Alder', 12500]]})],
            'promotion_rechecks_waiting_eligibility': [reserve, wait, step('waitlist', 'waiting', 'Cedar',
                expected={'Applications': [a, b, c]}), refund_birch, step('release', 'released', 'Alder',
                expected={'Applications': [released, b, c], 'Releases': [[1, 'Alder', None]]}),
                step('promote', 'promoted', expected={'Applications': [released, b, ['Cedar', 'reserved', 13200]],
                                                      'Releases': [[1, 'Alder', 'Cedar']]})],
            'refund_validation_and_id_conflict': [
                step('refund', 'invalid_amount', 'Alder', id='bad', amount=0),
                step('refund', 'invalid_amount', 'Alder', id='bad', amount=1040001),
                low_refund_after, step('refund', 'event_conflict', 'Alder', id='r1', amount=88001),
                step('request', 'ineligible', 'Alder')],
        },
    }
    return [{'name': name, 'introduced': version, 'steps': actions}
            for version, scenarios in grouped.items() for name, actions in scenarios.items()]


def read_state(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('The workbook must remain a regular file')
    book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        return {sheet.title: [list(row) for row in sheet.values] for sheet in book}
    finally:
        book.close()


def state_diff(actual, expected):
    return {sheet: {'expected': expected.get(sheet), 'observed': actual.get(sheet)}
            for sheet in sorted(set(expected) | set(actual)) if expected.get(sheet) != actual.get(sheet)}


def sandbox_command(program, case_dir, event):
    bwrap = which('bwrap')
    if not bwrap:
        raise RuntimeError('The action evaluator requires bubblewrap')
    command = [bwrap, '--die-with-parent', '--unshare-all', '--clearenv', '--ro-bind', '/usr', '/usr',
               '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
               '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--bind', str(case_dir), '/case',
               '--ro-bind', str(program), '/exercise/approval.py', '--chdir', '/case', '--setenv', 'HOME', '/tmp',
               '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    runtime = Path(sys.base_prefix).resolve()
    if not runtime.is_relative_to('/usr'):
        if runtime == Path('/') or Path(__file__).resolve().is_relative_to(runtime):
            raise RuntimeError('Use an isolated Python installation outside the project')
        command.extend(('--ro-bind', str(runtime), str(runtime)))
    return command + [sys.executable, '/exercise/approval.py', f'/case/{BOOK}', json.dumps(event)]


def invoke(program, case_dir, event):
    try:
        result = run(sandbox_command(program, case_dir, event), text=True, capture_output=True, timeout=8)
    except TimeoutExpired:
        return None, 'action timed out'
    if result.returncode:
        if result.stderr.startswith('bwrap:'):
            raise RuntimeError(result.stderr.strip())
        return None, result.stderr[-1400:]
    try:
        return json.loads(result.stdout), None
    except ValueError:
        return None, 'invalid JSON response'


def grade(submission, stage, oracle=None):
    submission = Path(submission).resolve()
    selected = [case for case in (oracle if oracle is not None else test_cases()) if case['introduced'] <= stage]
    if any(not (submission / name).is_file() or (submission / name).is_symlink() for name in (BOOK, PROGRAM)):
        return {'pass': False, 'status': 'missing_submission', 'cases': []}
    try:
        template_diff = state_diff(read_state(submission / BOOK), TEMPLATE)
    except STATE_READ_ERRORS as exc:
        return {'pass': False, 'status': 'unreadable_submission', 'detail': str(exc), 'cases': []}
    results = []
    try:
        for case in selected:
            expected = dict(TEMPLATE)
            checked = []
            with TemporaryDirectory(prefix='hotel-action-case-') as scratch:
                folder = Path(scratch)
                workbook = folder / BOOK
                copy2(submission / BOOK, workbook)
                for action in case['steps']:
                    for sheet, rows in action['replace'].items():
                        expected[sheet] = [TEMPLATE[sheet][0], *rows]
                    if 'block' in action:
                        book = load_workbook(workbook)
                        book[action['block']].delete_rows(2, book[action['block']].max_row - 1)
                        book.save(workbook)
                        book.close()
                        actual, error, wanted = None, None, None
                    else:
                        actual, error = invoke(submission / PROGRAM, folder, action['event'])
                        wanted = {'result': action['result']}
                    differences = state_diff(read_state(workbook), expected)
                    checked.append({'input': action.get('event', {'grader_removes_rows': action.get('block')}),
                                    'expected_reply': wanted, 'reply': actual, 'error': error,
                                    'state_diff': differences, 'pass': actual == wanted and not error and not differences})
            results.append({'name': case['name'], 'introduced': case['introduced'], 'steps': checked,
                            'pass': all(entry['pass'] for entry in checked)})
    except RuntimeError as exc:
        return {'pass': False, 'status': 'evaluator_error', 'detail': str(exc), 'cases': results}
    except STATE_READ_ERRORS as exc:
        return {'pass': False, 'status': 'invalid_workbook_during_replay', 'detail': str(exc), 'cases': results}
    return {'pass': not template_diff and all(row['pass'] for row in results), 'status': 'checked',
            'initial_state_diff': template_diff, 'cases': results}


def build(output):
    output.mkdir(parents=True)
    cases = test_cases()
    summary = {}
    for stage in range(1, 4):
        package = output / f'm{stage}'
        public, private = package / 'public', package / 'private'
        for directory, level in ((public, stage - 1), (private / 'reference', stage)):
            directory.mkdir(parents=True)
            start_workbook(directory / BOOK)
            (directory / PROGRAM).write_text(reference_source(level), encoding='utf-8')
        (public / 'instruction.md').write_text(task_text(stage), encoding='utf-8')
        save_json(private / 'oracle.json', [case for case in cases if case['introduced'] <= stage])
        results = {name: grade(path, stage, cases) for name, path in
                   [('reference', private / 'reference'), ('untouched', public)]}
        for name, result in results.items():
            save_json(private / f'{name}_score.json', result)
        if not results['reference']['pass'] or results['untouched']['pass'] or results['untouched']['status'] != 'checked':
            raise ValueError(f'Stage {stage} could not validate reference and untouched controls. See {private}')
        summary[f'm{stage}'] = {'cases': len(results['reference']['cases']),
                               'steps': sum(len(case['steps']) for case in results['reference']['cases'])}
        print('STATEFUL_REFERENCE_VALIDATED', stage, summary[f'm{stage}'], flush=True)
    validate_faults(output, cases)
    save_json(output / 'summary.json', {'passed': True, 'stages': summary, 'fault_controls': 3})


def validate_faults(output, cases):
    reference = output / 'm3/private/reference'
    script = (reference / PROGRAM).read_text()
    faults = {
        'promote_without_release': ("if grant_row is None:\n        return 'no_release'",
                                    "if grant_row is None:\n        grant_row = 2", 'release_is_required'),
        'refund_does_not_release': ('            release(book, event)', '            pass', 'refund_then_attempt_to_sign'),
        'refund_mutates_signed_snapshot': ("    return 'refunded'", "    if application and application[1] == 'issued':\n"
                                           "        book['Issued'].cell(2, 2, metrics(book, hotel)[0])\n"
                                           "    return 'refunded'", 'sign_then_refund'),
    }
    for name, (original, wrong, affected) in faults.items():
        if script.count(original) != 1:
            raise ValueError(f'Control edit is ambiguous: {name}')
        folder = output / 'controls' / name
        folder.mkdir(parents=True)
        copy2(reference / BOOK, folder / BOOK)
        (folder / PROGRAM).write_text(script.replace(original, wrong))
        score = grade(folder, 3, [case for case in cases if case['name'] == affected])
        save_json(folder / 'score.json', score)
        if score['status'] != 'checked' or score['pass']:
            raise ValueError(f'Failure to detect fault: {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == '__main__':
    main()
