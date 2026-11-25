"""Recalculate large submissions after applying a case in a single workbook pass."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import shutil
import subprocess

from openpyxl import load_workbook

from . import evaluate as ev
from . import linked_reports as linked


def prepare(source, case, target):
    errors = {}
    book = load_workbook(source)
    try:
        for sheet, records in case.get('inputs', {}).items():
            if sheet in book:
                ev._table(book, sheet, linked.TABLES[sheet], records)
            elif records:
                errors[sheet] = 'missing input ledger'
        names = case.get('ledger_overlays', {}) | dict(zip(ev.M3_INPUTS, (case.get('lookup_id'), case.get('report_date'))))
        for name, value in names.items():
            if value is None and name not in book.defined_names:
                continue
            try:
                ev._set_input(book, name, date.fromisoformat(value) if value and name.endswith('Date') else value)
            except (KeyError, ValueError) as exc:
                errors[name] = str(exc)
        if case.get('cost_month'):
            book['HRCostAnalysis']['C6'] = case['cost_month']
        if 'EmploymentPeriods' in book:
            ev._table(book, 'EmploymentPeriods', ev.PERIOD_FIELDS, case.get('periods', []))
        elif case.get('periods'):
            errors['EmploymentPeriods'] = 'missing input sheet'
        as_of = ev.m2.AS_OF
        replacement = f'DATE({as_of.year},{as_of.month},{as_of.day})'
        for sheet in book:
            for cell in sheet._cells.values():
                if cell.data_type == 'f' and isinstance(cell.value, str) and 'TODAY()' in cell.value.upper():
                    cell.value = re.sub(r'TODAY\(\)', replacement, cell.value, flags=re.IGNORECASE)
        begin, end = ev.m2.period(case['month'])
        month = book['MonthlyChangeQuery']
        month['C4'] = month['Q4'] = f'{begin.year}Year'
        month['E4'] = {11: '1Jan', 12: '1Feb'}.get(begin.month, begin.strftime('%b'))
        book['AnnualHeadcountHelper']['H4'] = begin
        book['AnnualHeadcountHelper']['J4'] = end
        book['EmployeeRecordQuery']['C5'] = case['query_name']
        ev.m2._write_ledger(book, 'DepartmentTransfers',
                            ('employee_id', 'effective_date', 'from_department', 'to_department'), case['events'])
        ev.m2._write_ledger(book, 'TemporaryAssignments',
                            ('employee_id', 'start_date', 'end_date', 'host_department'), case['assignments'])
        book.save(target)
    finally:
        book.close()
    return errors


def recalculate(source, case, destination):
    with TemporaryDirectory(prefix='lite38_replay_') as temp:
        root = Path(temp)
        pending = root / 'case.xlsx'
        errors = prepare(source, case, pending)
        subprocess.run(['libreoffice', f'-env:UserInstallation={(root / "profile").as_uri()}',
                        '--headless', '--norestore', '--nodefault', '--nofirststartwizard',
                        '--convert-to', 'xlsx:Calc MS Excel 2007 XML', '--outdir', str(root / 'out'), str(pending)],
                       check=True, capture_output=True, text=True, timeout=180)
        calculated = root / 'out/case.xlsx'
        if not calculated.is_file():
            raise RuntimeError('LibreOffice did not produce a recalculated workbook')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(calculated, destination)
    return errors


def score(source, cases, recalc_dir, start, original):
    result = {'structure': ev._workbook_checks(source, start, original), 'cases': {}}
    for case in cases:
        out = recalc_dir / f"{case['id']}.xlsx"
        setup = recalculate(source, case, out)
        rendered, legacy, annual_error = ev.m2.read_results(out)
        extra, m3_errors = ev._m3_outputs(out, case.get('extra_gt', {}))
        later, later_errors = linked.additional_outputs(out, case.get('linked_gt', {}))
        actual = rendered | legacy | extra | later
        expected = case['gt'] | case['legacy_gt'] | case.get('extra_gt', {}) | case.get('linked_gt', {})
        if case['stage'] not in ('m1', 'm2'):
            expected.pop('annual_hires_separations', None)
        checks = {key: linked.matches(actual.get(key), value) for key, value in expected.items()}
        for endpoint in ('opening', 'ending'):
            values = rendered[endpoint].values()
            checks[f'{endpoint}_total'] = all(type(value) in (int, float) for value in values) and (
                sum(values) == sum(expected[endpoint].values()))
        result['cases'][case['id']] = {'stage': case['stage'], 'checks': checks, 'gt': expected,
                                       'actual': actual, 'effect_keys': case['effect_keys'],
                                       'interface_errors': {'inputs': setup, 'annual': annual_error,
                                                            'm3': m3_errors, 'later': later_errors}}
    return result
