"""Freeze and grade a matched narrow/expanded upgrade on native Office assets."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from .hotel_agent import file_hashes
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_stateful_bench import BOOK, PROGRAM, STATE_READ_ERRORS, invoke, read_state, start_workbook, state_diff
from .hotel_upgrade_cases import LedgerModel, fixture, generated_worlds
from .hotel_upgrade_reference import LABELS, REPORTS, STATES, upgrade_source, update_reports

DEFAULT = ARTIFACTS / 'hotel_upgrade_scope'
OFFICE = (BOOK, *REPORTS)
INPUTS = (*OFFICE, PROGRAM, 'instruction.md')
PACKAGES = {'m1': (1, 'common'), 'm2': (2, 'common'), 'm3_narrow': (3, 'narrow'), 'm3_expanded': (3, 'expanded')}
RUNTIME_FILES = ('hotel_upgrade_bench.py', 'hotel_upgrade_cases.py', 'hotel_upgrade_reference.py',
                 'hotel_stateful_reference.py', 'hotel_stateful_bench.py', 'hotel_agent.py', 'hotel_stateful_agent.py',
                 'hotel_upgrade_agent.py')
WRITES = {'reserved', 'issued', 'waiting', 'released', 'promoted', 'refunded', 'reversed', 'adjusted', 'withdrawn', 'published'}


def instructions(stage, arm):
    from .hotel_stateful_bench import task_text

    original = task_text(stage).replace('Use openpyxl and the standard library.',
                                        'Use openpyxl, python-docx, python-pptx and the standard library.')
    # Reports are a common M2 capability, including both third-round arms.
    publish = """
## Shared native reports (M2 onward)

Alongside approval.xlsx are current.docx and current.pptx. After every accepted action, refresh both from the July
workbook state. Keep the title, existing two-column tables, and native chart editable. The rows are Period (YYYYMM),
Net revenue cents (sum of all July net revenues), Eligible hotels (independent of application status), and the five
application-status counts in template order. The chart and its embedded data use the same five labels and counts.
On successful issue, also save the synchronized Word and PowerPoint files under `signed/<hotel>/current.docx` and
`signed/<hotel>/current.pptx`. These and the existing Excel Issued record are immutable. Rejected operations must
leave the current reports and all snapshots unchanged.
"""
    expanded = """
## Expanded third milestone

Add the four actions below alongside refund. Keep all operations in the same approval.py command interface. Multiple
actions in one scenario reopen and update the same files. Use the existing reservation, release and promotion state
so no event can allocate a seat twice. Hotel IDs, event IDs and publication IDs are safe single path components.

- `reverse_refund` takes `id` and `refund_id`. The Reversals sheet records [id, refund_id]. Reusing an ID with the
  same refund returns `duplicate`; reusing it with another refund returns `event_conflict`. An absent refund returns
  `unknown_refund`. Reversing the same refund with a different new ID returns `already_reversed`. Otherwise restore
  the revenue and return `reversed`. Update reviewed ADR for a reserved application. If a released applicant
  becomes eligible, place it in waiting at its original application row. Its old release remains usable only if
  unconsumed. Any committed seat, issued snapshot or withdrawn status remains unchanged.
- `adjust_month` takes `id`, `hotel`, `month`, and a nonzero signed integer `delta`. Adjustments records
  [id, hotel, month, delta], with the same exact-payload duplicate/conflict rule. July (202607) edits Ledger and uses
  the normal reserved recheck and release path. June (202606) edits the matching row in Prior ledger. This leaves
  July applications, reservations and reports at their prior business values. Reject other months with
  `unknown_period`, unknown hotels with `unknown_hotel`, and invalid or negative-result amounts with
  `invalid_amount`. Success returns `adjusted`.
- `withdraw` takes `hotel`. A reserved applicant releases the seat through the existing release path. A waiting
  or released applicant changes status without creating another release. In all three cases persist withdrawn and
  return `withdrawn`. Issued or missing applications return `not_withdrawable`. Promotion skips withdrawn rows.
- `amend_report` takes `id` and `month`. Use `duplicate` for an existing ID with the same month and
  `event_conflict` for a different month. Only 202606 and 202607 are supported (`unknown_period` otherwise).
  Append [id, month, net revenue cents, eligible hotels] to Publications. Write a new immutable pair beneath
  `amendments/<id>/` using the same filenames and layouts. July copies the current report. June uses Prior ledger
  and the ADR floor in Prior policy with the shared occupancy rule. Its application counts are all zero. Do not
  overwrite previously issued or amended reports. Return `published`.
"""
    note = """
Use the existing workbook and reports as the submission template. They must be ready for independent grading;
perform local trials on copies. The workbook may contain more than four hotels and a capacity greater than one.
You may refactor approval.py. Use openpyxl, python-docx, python-pptx and the standard library.
"""
    return original + (publish if stage >= 2 else '') + (expanded if arm == 'expanded' else '') + note


def public_reports(folder, values):
    doc = Document()
    doc.add_heading('Hotel approval report', 0)
    doc_table = doc.add_table(rows=len(LABELS) + 1, cols=2)
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = 'Hotel approval report'
    ppt_table = slide.shapes.add_table(len(LABELS) + 1, 2, Inches(.4), Inches(1.4), Inches(4), Inches(4.5)).table
    displayed = [('Measure', 'Value'), *zip(LABELS, values)]
    for row, fields in enumerate(displayed):
        for col, text in enumerate(fields):
            doc_table.cell(row, col).text = ppt_table.cell(row, col).text = str(text)
    chart_data = CategoryChartData()
    chart_data.categories = STATES
    chart_data.add_series('Applications', values[3:])
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(4.6), Inches(1.4), Inches(5), Inches(4.5), chart_data)
    doc.save(folder / REPORTS[0])
    deck.save(folder / REPORTS[1])


def read_reports(folder):
    paths = [folder / file for file in REPORTS]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError('Missing or non-regular current reports')
    document, deck = Document(paths[0]), Presentation(paths[1])
    slide = deck.slides[0]
    chart = next(shape.chart for shape in slide.shapes if shape.has_chart)
    ppt = next(shape.table for shape in slide.shapes if shape.has_table)
    doc = document.tables[0]
    if ([row.cells[0].text for row in doc.rows][1:] != list(LABELS)
            or [row.cells[0].text for row in ppt.rows][1:] != list(LABELS)
            or document.paragraphs[0].text != 'Hotel approval report' or slide.shapes.title.text != 'Hotel approval report'):
        raise ValueError('Protected report headers differ')
    values = [int(row.cells[1].text) for row in list(doc.rows)[1:]]
    ppt_values = [int(row.cells[1].text) for row in list(ppt.rows)[1:]]
    if (values != ppt_values or list(chart.series[0].values) != values[3:]
            or [category.label for category in chart.plots[0].categories] != list(STATES)):
        raise ValueError('Word, slides or chart cache disagree')
    book = load_workbook(BytesIO(chart.part.chart_workbook.xlsx_part.blob), data_only=True, read_only=True)
    try:
        series = list(book.active.values)[1:]
    finally:
        book.close()
    if [row[0] for row in series] != list(STATES) or [row[1] for row in series] != values[3:]:
        raise ValueError('Embedded native chart data disagree')
    return values


def check_versions(folder, versions, fingerprints):
    actual = {str(path.relative_to(folder)) for section in ('signed', 'amendments')
              for path in (folder / section).rglob('*') if path.is_file()}
    expected = {str(Path(key) / file) for key in versions for file in REPORTS}
    if actual != expected:
        raise ValueError('Published snapshot inventory differs')
    for key, values in versions.items():
        target = folder / key
        if target.is_symlink() or read_reports(target) != values:
            raise ValueError(f'Published snapshot content differs: {key}')
        digest = file_hashes(target, REPORTS)
        if key in fingerprints and fingerprints[key] != digest:
            raise ValueError(f'Published snapshot bytes changed: {key}')
        fingerprints[key] = digest


def grade(submission, oracle):
    submission = Path(submission)
    results = []
    if set(file_hashes(submission, (*OFFICE, PROGRAM))) != {*OFFICE, PROGRAM}:
        return {'pass': False, 'status': 'missing_submission', 'cases': []}
    try:
        seed = fixture(0, 4, 1)
        template_ok = read_state(submission / BOOK) == seed and read_reports(submission) == LedgerModel(seed).views()
    except (OSError, ValueError, KeyError, IndexError, StopIteration):
        template_ok = False
    for case in oracle:
        with TemporaryDirectory(prefix='hotel-upgrade-') as scratch:
            folder = Path(scratch)
            start_workbook(folder / BOOK, case['initial'])
            for name in REPORTS:
                copy2(submission / name, folder / name)
            try:
                update_reports(folder, case['initial_report'])
            except (OSError, ValueError, KeyError, IndexError, StopIteration) as exc:
                return {'pass': False, 'status': 'invalid_report_template', 'detail': type(exc).__name__, 'cases': results}
            checked, fingerprints = [], {}
            for step in case['steps']:
                event = step['event']
                before_reports = file_hashes(folder, REPORTS)
                if 'intervention' in event:
                    with load_for_intervention(folder / BOOK) as book:
                        book['Releases'].delete_rows(2, book['Releases'].max_row - 1)
                        book.save(folder / BOOK)
                    actual, error = None, None
                else:
                    actual, error = invoke((submission / PROGRAM).resolve(), folder, event)
                observed, changed, report_problem = None, {}, None
                try:
                    row = read_state(folder / BOOK)
                    changed = state_diff(row, step['workbook'])
                    observed = read_reports(folder)
                    check_versions(folder, step['versions'], fingerprints)
                    if step['reply'] and step['reply']['result'] not in WRITES and before_reports != file_hashes(folder, REPORTS):
                        report_problem = 'Rejection changed a current report'
                except (*STATE_READ_ERRORS, IndexError, StopIteration) as exc:
                    report_problem = str(exc)
                accepted = (actual == step['reply'] and observed == step['current'] and not changed
                            and not error and not report_problem)
                checked.append({'event': event, 'reply': actual, 'expected_reply': step['reply'],
                                'state_diff': changed, 'observed_reports': observed, 'expected_reports': step['current'],
                                'report_error': report_problem, 'execution_error': error, 'pass': accepted})
        results.append({**{k: case[k] for k in ('name', 'seed', 'scenario', 'introduced', 'hotels', 'capacity')},
                        'pass': all(step['pass'] for step in checked), 'steps': checked})
    return {'pass': template_ok and all(case['pass'] for case in results), 'status': 'checked',
            'template_intact': template_ok, 'cases': results}


@contextmanager
def load_for_intervention(path):
    book = load_workbook(path)
    try:
        yield book
    finally:
        book.close()


def validate_order(cases):
    by_name = {case['name']: case for case in cases}
    final = by_name['101:reversal_after_slot_taken']['steps'][-1]
    assert final['workbook']['Ledger'][1][3] == 1040000
    assert final['workbook']['Applications'][1:3] == [['Alder', 'waiting', 13000], ['Birch', 'issued', 12800]]
    assert final['workbook']['Releases'][1:] == [[1, 'Alder', 'Birch']]
    assert len(final['versions']) == 1


def fault_controls(root, oracle):
    ref = root / 'm3_expanded/private/reference'
    script = (ref / PROGRAM).read_text()
    variants = {
        'reversal_reclaims_taken_slot': ("cell(application_row, 2, 'waiting')", "cell(application_row, 2, 'reserved')",
                                         'reversal_after_slot_taken'),
        'release_gate_bypass': ("if grant_row is None:\n        return 'no_release'",
                                "if grant_row is None:\n        grant_row = 2", 'refund_release_gate'),
        'overwrites_published_history': ('update_reports(folder, report_values(book, 202607))',
            "update_reports(folder, report_values(book, 202607))\n"
            "            for previous in (folder / 'signed').glob('*'):\n"
            "                update_reports(folder, report_values(book, 202607), previous)", 'signing_before_refund'),
    }
    for name, (pattern, wrong, wanted) in variants.items():
        assert script.count(pattern) == 1
        folder = root / 'controls' / name
        folder.mkdir(parents=True)
        for file in OFFICE:
            copy2(ref / file, folder / file)
        (folder / PROGRAM).write_text(script.replace(pattern, wrong))
        score = grade(folder, [row for row in oracle if row['name'] == f'101:{wanted}'])
        assert score['status'] == 'checked' and not score['pass'], name
        save_json(folder / 'score.json', score)


def freeze_hashes(root):
    files = {}
    for package in PACKAGES:
        base = root / package
        for sub, names in [('public', INPUTS), ('private/reference', (*OFFICE, PROGRAM)),
                           ('private', ('oracle.json',))]:
            for name, digest in file_hashes(base / sub, names).items():
                files[f'{package}/{sub}/{name}'] = digest
    return {'assets': files, 'runtime': file_hashes(Path(__file__).parent, RUNTIME_FILES)}


def build(root):
    root.mkdir(parents=True)
    validated = {}
    seed = fixture(0, 4, 1)
    packages = {}
    for package, (stage, arm) in PACKAGES.items():
        public, private = root / package / 'public', root / package / 'private'
        reference = private / 'reference'
        for folder, level in ((public, stage - 1), (reference, stage)):
            folder.mkdir(parents=True)
            start_workbook(folder / BOOK, seed)
            public_reports(folder, LedgerModel(seed).views())
            (folder / PROGRAM).write_text(upgrade_source(level, arm))
        (public / 'instruction.md').write_text(instructions(stage, arm))
        cases = generated_worlds(stage, arm)
        if arm == 'expanded':
            validate_order(cases)
        save_json(private / 'oracle.json', cases)
        packages[package] = (public, private, reference, cases)

    def validate(package, public, private, reference, cases):
        stage, _ = PACKAGES[package]
        for name, path in [('reference', reference), ('untouched', public)]:
            selected = cases if name == 'reference' else [row for row in cases if row['introduced'] == stage][:3]
            result = grade(path, selected)
            save_json(private / f'{name}_score.json', result)
            if result['status'] != 'checked' or result['pass'] != (name == 'reference'):
                raise ValueError(f'{package} {name} failed validation. See {private / (name + "_score.json")}')
        result = {'cases': len(cases), 'steps': sum(len(case['steps']) for case in cases)}
        print('UPGRADE_VALIDATED', package, result, flush=True)
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = {name: pool.submit(validate, name, *parts) for name, parts in packages.items()}
        validated.update({name: job.result() for name, job in checks.items()})
    expanded = read_json(root / 'm3_expanded/private/oracle.json')
    common = {case['name']: case for case in expanded}
    assert all(case == common[case['name']] for case in read_json(root / 'm3_narrow/private/oracle.json'))
    fault_controls(root, expanded)
    save_json(root / 'summary.json', {'passed': True, 'stages': validated, 'fault_controls': 3, 'freeze': freeze_hashes(root)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == '__main__':
    main()
