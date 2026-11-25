"""Score cumulative Lite 38 workbooks through the published spreadsheet interfaces."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from ..temporary_assignment import evaluate as m2


PERIOD_HEADERS = ('Employee ID', 'Employment Period ID', 'Hire Date', 'Separation Date', 'Starting Department')
PERIOD_FIELDS = ('employee_id', 'period_id', 'hire_date', 'separation_date', 'department')
M3_INPUTS = ('EmploymentEmployeeID', 'EmploymentReportDate')
M3_OUTPUTS = {
    'monthly_rehires': 'MonthlyRehireCounts',
    'annual_hires': 'AnnualHireCounts',
    'annual_separations': 'AnnualSeparationCounts',
    'annual_rehires': 'AnnualRehireCounts',
}


def _range(book, name):
    item = book.defined_names.get(name)
    destinations = list(item.destinations) if item is not None else []
    if len(destinations) != 1:
        raise ValueError(f'{name} must identify a single range')
    sheet, cells = destinations[0]
    destination = book[sheet]
    if destination.sheet_state != 'visible':
        raise ValueError(f'{name} must be visible')
    return destination, cells


def _set_input(book, name, value):
    sheet, cells = _range(book, name)
    if len(m2.row_values(sheet, cells)) != 1:
        raise ValueError(f'{name} must be a single input cell')
    sheet[cells.replace('$', '')] = value


def _table(book, sheet_name, fields, records):
    sheet = book[sheet_name]
    for row in sheet.iter_rows(min_row=2, max_row=max(2, sheet.max_row), max_col=len(fields)):
        for cell in row:
            cell.value = None
    for index, record in enumerate(records, 2):
        for col, key in enumerate(fields, 1):
            value = record.get(key)
            sheet.cell(index, col).value = date.fromisoformat(value) if value and key.endswith('_date') else value


def recalculated(source, case, destination):
    errors = {}
    with TemporaryDirectory(prefix='lite38_evolution_') as tmp:
        copy = Path(tmp) / 'candidate.xlsx'
        book = load_workbook(source)
        try:
            if 'EmploymentPeriods' in book:
                _table(book, 'EmploymentPeriods', PERIOD_FIELDS, case.get('periods', []))
            elif case.get('periods'):
                errors['EmploymentPeriods'] = 'missing input sheet'
            for name, value in zip(M3_INPUTS, (case.get('lookup_id'), case.get('report_date'))):
                if value is None and name not in book.defined_names:
                    continue
                try:
                    if value and name == 'EmploymentReportDate':
                        value = date.fromisoformat(value)
                    _set_input(book, name, value)
                except (KeyError, ValueError) as exc:
                    errors[name] = str(exc)
            book.save(copy)
        finally:
            book.close()
        m2.recalculate(copy, case, destination)
    return errors


def _match(actual, expected):
    if expected is None:
        return actual is None or actual == ''
    if type(expected) is int:
        return type(actual) in (int, float) and actual == expected
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            _match(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _match(value, wanted) for value, wanted in zip(actual, expected))
    return actual == expected


def _m3_outputs(path, keys):
    book = load_workbook(path, read_only=True, data_only=True)
    values, errors = {}, {}
    try:
        labels = m2.row_values(book['MonthlyChangeQuery'], 'C5:I5')
        for key in keys:
            try:
                sheet, cells = _range(book, M3_OUTPUTS[key])
                values[key] = m2._lookup(labels, m2.row_values(sheet, cells))
            except (KeyError, ValueError, TypeError) as exc:
                errors[key] = str(exc)
                values[key] = None
    finally:
        book.close()
    return values, errors


def _workbook_checks(source, start, original):
    info = m2.inventory(source)
    checks = {
        'transfer_headers': info['transfer_headers'],
        'permanent_records_preserved': info['permanent_records'] == start['permanent_records'],
        'temporary_assignment_headers': info['temporary_assignment_headers'],
        'temporary_assignment_visible': info['temporary_assignment_visible'],
        'temporary_records_preserved': info['temporary_records'] == start['temporary_records'],
        'starting_department_values_preserved': info['starting_departments'] == original['starting_departments'],
        'personal_contract_formula_preserved': info['personal_contract_formula'] == original['personal_contract_formula'],
    }
    book = load_workbook(source, read_only=True)
    try:
        if 'EmploymentPeriods' in book:
            periods = book['EmploymentPeriods']
            checks['employment_period_interface'] = periods.sheet_state == 'visible' and tuple(
                m2.row_values(periods, 'A1:E1')) == PERIOD_HEADERS
            checks['employment_periods_blank'] = all(
                v is None for row in periods.iter_rows(min_row=2, max_col=5, values_only=True) for v in row)
        else:
            checks.update(employment_period_interface=False, employment_periods_blank=False)
        for name in M3_INPUTS:
            try:
                sheet, cells = _range(book, name)
                checks[name] = m2.row_values(sheet, cells) == [None]
            except (KeyError, ValueError):
                checks[name] = False
    finally:
        book.close()
    return checks


def grade(source, cases, recalc_dir, start, original):
    result = {'structure': _workbook_checks(source, start, original), 'cases': {}}
    for case in cases:
        out = recalc_dir / f"{case['id']}.xlsx"
        setup_errors = recalculated(source, case, out)
        actual, legacy, annual_error = m2.read_results(out)
        errors = {'inputs': setup_errors, 'annual_headcounts': annual_error}
        extra, extra_errors = _m3_outputs(out, case.get('extra_gt', {}))
        gt = case['gt'] | case.get('extra_gt', {})
        data = actual | legacy | extra
        checks = {k: _match(data.get(k), expected) for k, expected in (gt | case['legacy_gt']).items()}
        for endpoint in ('opening', 'ending'):
            counts = actual[endpoint]
            checks[f'{endpoint}_total'] = (all(type(n) in (int, float) for n in counts.values()) and
                                             sum(counts.values()) == sum(gt[endpoint].values()))
        result['cases'][case['id']] = {'stage': case['stage'], 'checks': checks, 'actual': data,
                                        'gt': gt | case['legacy_gt'], 'effect_keys': case['effect_keys'],
                                        'interface_errors': errors | {'extra': extra_errors}}
    return result
