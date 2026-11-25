"""Exercise a submitted workbook against private inputs and precomputed expectations."""

from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import shutil
import subprocess

from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries


AS_OF = date(2026, 11, 25)
BASELINE_LAYOUTS = {
    'first_m1': ('C44:I44', 'C45:I45', 'C46:I46'),
    'second_m1': ('B46:B52', 'E46:E52', 'H46:H52'),
}
ANNUAL_NAMES = ('AnnualDepartmentLabels', 'AnnualOpeningCounts', 'AnnualEndingCounts')
TEMP_HEADERS = ('Employee ID', 'Start Date', 'End Date', 'Host Department')
TRANSFER_HEADERS = ('Employee ID', 'Effective Date', 'From Department', 'To Department')


def period(month):
    year, number = map(int, month.split('-'))
    return date(year, number, 1), date(year, number, monthrange(year, number)[1])


def row_values(sheet, ref):
    c1, r1, c2, r2 = range_boundaries(ref)
    if None in (c1, r1, c2, r2) or (c1 != c2 and r1 != r2):
        raise ValueError(f'Expected one contiguous row or column: {ref}')
    return [v for row in sheet.iter_rows(min_col=c1, min_row=r1, max_col=c2,
                                         max_row=r2, values_only=True) for v in row]


def _headers(book, sheet, wanted):
    return sheet in book and tuple(row_values(book[sheet], 'A1:D1')) == wanted


def _entries(book, sheet):
    if sheet not in book:
        return []
    return [list(row) for row in book[sheet].iter_rows(min_row=2, min_col=1, max_col=4, values_only=True)
            if any(v is not None for v in row)]


def inventory(path):
    """Record the submitted structure before writing any tests into a copy."""
    with path.open('rb') as file:
        book = load_workbook(file, read_only=True)
        try:
            return {
                'transfer_headers': _headers(book, 'DepartmentTransfers', TRANSFER_HEADERS),
                'temporary_assignment_headers': _headers(book, 'TemporaryAssignments', TEMP_HEADERS),
                'temporary_assignment_visible': 'TemporaryAssignments' in book and
                book['TemporaryAssignments'].sheet_state == 'visible',
                'permanent_records': _entries(book, 'DepartmentTransfers'),
                'temporary_records': _entries(book, 'TemporaryAssignments'),
                'starting_departments': {
                    'active': row_values(book['ActiveEmployees'], 'V6:V69'),
                    'archive': row_values(book['SeparatedEmployeeArchive'], 'O6:O126'),
                },
                'personal_contract_formula': book['ActiveEmployees']['AA6'].value,
            }
        finally:
            book.close()


def _write_ledger(book, sheet_name, fields, events):
    if sheet_name not in book:
        return
    sheet = book[sheet_name]
    for row in sheet.iter_rows(min_row=2, max_row=max(sheet.max_row, len(events) + 1), max_col=4):
        for cell in row:
            cell.value = None
    for row_index, event in enumerate(events, start=2):
        for col, name in enumerate(fields, start=1):
            value = event[name]
            sheet.cell(row_index, col).value = date.fromisoformat(value) if name.endswith('_date') else value


def recalculate(path, case, output, *, original_legacy=False):
    """Use the workbook's own report selectors, including the original special month labels."""
    begin, end = period(case['month'])
    with TemporaryDirectory(prefix='lite38m2_recalc_') as directory:
        root = Path(directory)
        book = load_workbook(path)
        for sheet in book:
            for cell in sheet._cells.values():
                if cell.data_type == 'f' and isinstance(cell.value, str) and 'TODAY()' in cell.value.upper():
                    cell.value = re.sub(r'TODAY\(\)', f'DATE({AS_OF.year},{AS_OF.month},{AS_OF.day})',
                                        cell.value, flags=re.IGNORECASE)
        month = book['MonthlyChangeQuery']
        month['C4'] = f'{begin.year}Year'
        month['Q4'] = f'{begin.year}Year'
        month['E4'] = {11: '1Jan', 12: '1Feb'}.get(begin.month, begin.strftime('%b'))
        # The unedited source uses locale-sensitive string-to-date conversions.
        # Only the legacy comparison uses normalized dates in both versions.
        book['AnnualHeadcountHelper']['H4'] = begin
        book['AnnualHeadcountHelper']['J4'] = end
        if original_legacy:
            month['H4'] = begin
            month['J4'] = end
        book['EmployeeRecordQuery']['C5'] = case['query_name']
        _write_ledger(book, 'DepartmentTransfers',
                      ('employee_id', 'effective_date', 'from_department', 'to_department'), case['events'])
        _write_ledger(book, 'TemporaryAssignments',
                      ('employee_id', 'start_date', 'end_date', 'host_department'), case['assignments'])
        prepared = root / 'case.xlsx'
        book.save(prepared)
        book.close()
        subprocess.run(['libreoffice', f'-env:UserInstallation={(root / "profile").as_uri()}',
                        '--headless', '--norestore', '--nodefault', '--nofirststartwizard',
                        '--convert-to', 'xlsx:Calc MS Excel 2007 XML', '--outdir', str(root / 'out'), str(prepared)],
                       check=True, capture_output=True, text=True, timeout=180)
        result = root / 'out/case.xlsx'
        if not result.is_file():
            raise RuntimeError('LibreOffice returned without producing a recalculated workbook')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result, output)


def _annual_ranges(book, baseline_layout):
    if baseline_layout:
        sheet = book['MonthlyChangeQuery']
        return [row_values(sheet, ref) for ref in BASELINE_LAYOUTS[baseline_layout]]
    groups = []
    for name in ANNUAL_NAMES:
        defined = book.defined_names.get(name)
        destinations = list(defined.destinations) if defined is not None else []
        if len(destinations) != 1:
            raise ValueError(f'{name} must have one range')
        sheet, ref = destinations[0]
        if book[sheet].sheet_state != 'visible':
            raise ValueError(f'{name} must reference a visible sheet')
        groups.append(row_values(book[sheet], ref))
    return groups


def _lookup(labels, values):
    if len(labels) != 7 or len(set(labels)) != 7 or len(values) != 7:
        raise ValueError('Expected seven distinct department labels and seven counts')
    return dict(zip(labels, values))


def _iso(value):
    return (value.date() if isinstance(value, datetime) else value).isoformat() if isinstance(value, date) else value


def read_results(path, *, annual_baseline=None, legacy_only=False):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        month = book['MonthlyChangeQuery']
        annual_helper = book['AnnualHeadcountHelper']
        legacy = {
            'monthly_hires_separations': [row_values(month, f'C{r}:I{r}') for r in (6, 7)],
            'annual_hires_separations': [row_values(annual_helper, f'C{r}:I{r}') for r in (6, 7)],
            'salary_cost': book['HRCostAnalysis']['C11'].value,
            'total_cost': book['HRCostAnalysis']['K11'].value,
            'contract_dashboard': book['Home']['J12'].value,
        }
        if legacy_only:
            return legacy
        labels = row_values(month, 'C5:I5')
        structure = book['WorkforceStructureAnalysis']
        report = {
            'opening': _lookup(labels, row_values(month, 'C8:I8')),
            'ending': _lookup(labels, row_values(month, 'C11:I11')),
            'current_summary': _lookup(row_values(structure, 'H25:H31'), row_values(structure, 'I25:I31')),
            'query_department': book['EmployeeRecordQuery']['E19'].value,
            'monthly_dates': [_iso(month['H4'].value), _iso(month['J4'].value)],
        }
        annual_error = None
        try:
            names, opening, ending = _annual_ranges(book, annual_baseline)
            report['annual_opening'] = _lookup(names, opening)
            report['annual_ending'] = _lookup(names, ending)
        except (ValueError, KeyError, TypeError) as exc:
            annual_error = str(exc)
            report['annual_opening'] = report['annual_ending'] = None
        return report, legacy, annual_error
    finally:
        book.close()


def grade(path, cases, recalc_dir, start_inventory, original_inventory, *, annual_baseline=None):
    actual_inventory = inventory(path)
    workbook_checks = {
        'transfer_headers': actual_inventory['transfer_headers'],
        'permanent_input_records_preserved': actual_inventory['permanent_records'] == start_inventory['permanent_records'],
        'starting_departments_preserved': actual_inventory['starting_departments'] == original_inventory['starting_departments'],
        'personal_contract_formula_preserved': actual_inventory['personal_contract_formula'] ==
        original_inventory['personal_contract_formula'],
    }
    if not annual_baseline:
        workbook_checks.update({
            'temporary_assignment_headers': actual_inventory['temporary_assignment_headers'],
            'temporary_assignment_visible': actual_inventory['temporary_assignment_visible'],
            'temporary_inputs_blank': not actual_inventory['temporary_records'],
        })
    result = {'workbook_checks': workbook_checks, 'cases': {}}
    for case in cases:
        path_recalc = recalc_dir / f"{case['id']}.xlsx"
        recalculate(path, case, path_recalc)
        report, legacy, errors = read_results(path_recalc, annual_baseline=annual_baseline)
        gt = case['gt']
        business = {key: report[key] == expected for key, expected in gt.items()}
        for endpoint in ('opening', 'ending'):
            counts = report[endpoint]
            business[f'{endpoint}_total'] = (all(type(n) is int for n in counts.values()) and
                                             sum(counts.values()) == sum(gt[endpoint].values()))
        protection = {key: val == case['legacy_gt'][key] for key, val in legacy.items()}
        result['cases'][case['id']] = {
            'stage': case['stage'], 'business': business, 'protection': protection,
            'effect_keys': case['effect_keys'], 'report_error': errors, 'actual': report,
            'expected': gt, 'legacy_actual': legacy, 'legacy_expected': case['legacy_gt'],
        }
    return result
