"""Exercise the published input and output contracts beyond re-employment."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from . import evaluate as ev


TABLES = {
    'DepartmentChanges': ('effective_date', 'source_department', 'successor_department'),
    'PayrollCoverage': ('period_date',),
    'PayrollCosts': ('employee_id', 'period_date', 'salary'),
    'DepartmentLimits': ('period_date', 'department', 'headcount_limit', 'salary_budget'),
    'MovementRequests': ('request_id', 'kind', 'employee_id', 'start_date', 'end_date',
                         'from_department', 'to_department'),
}

NAMED_SCALARS = {'unallocated_salary': 'UnallocatedSalary'}
DEPARTMENT_VECTORS = {'allocated_salary': 'AllocatedSalary', 'allocated_days': 'AllocatedEmployeeDays'}


def injected_case(source, case, destination):
    if not (case.get('inputs') or case.get('ledger_overlays') or case.get('cost_month')):
        return ev.recalculated(source,case,destination)
    missing = {}
    with TemporaryDirectory(prefix='lite38_link_input_') as temp:
        pending = Path(temp)/'input.xlsx'
        book = load_workbook(source)
        try:
            if case.get('cost_month'):
                book['HRCostAnalysis']['C6'] = case['cost_month']
            for sheet,records in case.get('inputs',{}).items():
                if sheet in book:
                    ev._table(book,sheet,TABLES[sheet],records)
                elif records:
                    missing[sheet]='missing input ledger'
            for label,value in case.get('ledger_overlays',{}).items():
                try:
                    ev._set_input(book,label,date.fromisoformat(value) if label.endswith('Date') else value)
                except (KeyError,ValueError) as exc:
                    missing[label]=str(exc)
            book.save(pending)
        finally:
            book.close()
        errors=ev.recalculated(pending,case,destination)
    return missing|errors


def additional_outputs(path, expected):
    values,errors={},{}
    if not expected:
        return values,errors
    book=load_workbook(path,read_only=True,data_only=True)
    try:
        labels=ev.m2.row_values(book['MonthlyChangeQuery'],'C5:I5')
        for key,want in expected.items():
            try:
                if key in NAMED_SCALARS:
                    sheet,ref=ev._range(book,NAMED_SCALARS[key])
                    value,=ev.m2.row_values(sheet,ref)
                elif key in DEPARTMENT_VECTORS:
                    sheet,ref=ev._range(book,DEPARTMENT_VECTORS[key])
                    value=ev.m2._lookup(labels,ev.m2.row_values(sheet,ref))
                elif key=='decisions':
                    request=book['MovementRequests']
                    value={row[0]:list(row[7:9]) for row in request.iter_rows(min_row=2,max_col=9,values_only=True)
                           if row[0] in want}
                else:
                    raise ValueError(f'Unknown output contract {key}')
                values[key]=value
            except (KeyError,ValueError,TypeError) as exc:
                errors[key]=str(exc)
                values[key]=None
    finally:
        book.close()
    return values,errors


def score(source,cases,recalc_dir,start,original):
    output={'structure':ev._workbook_checks(source,start,original),'cases':{}}
    for case in cases:
        calculated=recalc_dir/f"{case['id']}.xlsx"
        setup=injected_case(source,case,calculated)
        report,legacy,annual_error=ev.m2.read_results(calculated)
        added,m3_errors=ev._m3_outputs(calculated,case.get('extra_gt',{}))
        later,later_errors=additional_outputs(calculated,case.get('linked_gt',{}))
        actual=report|legacy|added|later
        expected=case['gt']|case['legacy_gt']|case.get('extra_gt',{})|case.get('linked_gt',{})
        # The frozen M3 scorer treated this duplicate range on a hidden
        # template as an output. The annual activity contract uses the named
        # visible ranges. Preserve the original legacy checks in M1/M2 cases.
        if case['stage'] not in ('m1','m2'):
            expected.pop('annual_hires_separations',None)
        checks={key:matches(actual.get(key),value) for key,value in expected.items()}
        for endpoint in ('opening','ending'):
            counts=report[endpoint]
            checks[f'{endpoint}_total']=all(type(n) in (int,float) for n in counts.values()) and (
                sum(counts.values())==sum(expected[endpoint].values()))
        output['cases'][case['id']]={'stage':case['stage'],'checks':checks,'gt':expected,'actual':actual,
                                      'effect_keys':case['effect_keys'],'interface_errors':{
                                          'inputs':setup,'annual':annual_error,'m3':m3_errors,'later':later_errors}}
    return output


def matches(actual,expected):
    if type(expected) is float:
        return type(actual) in (int,float) and abs(actual-expected) < 0.000001
    if type(expected) is dict and isinstance(actual,dict):
        return actual.keys() == expected.keys() and all(matches(actual[k],v) for k,v in expected.items())
    return ev._match(actual,expected)
