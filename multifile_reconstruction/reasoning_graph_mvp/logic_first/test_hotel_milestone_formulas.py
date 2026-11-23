"""Check calculations and the dependency on prior milestone cells."""
from fractions import Fraction
from shutil import which

from openpyxl import Workbook
import pytest

from .hotel_milestone_formulas import verify_formula_function


pytestmark = pytest.mark.skipif(not (which('libreoffice') and which('bwrap')), reason='requires isolated Calc')
PUBLISHED = '酒店指标'


def exercise(tmp_path, occupancy, eligibility):
    original = tmp_path / 'workbook.xlsx'
    workbook = Workbook()
    ledger = workbook.active
    ledger.title = 'Ledger'
    ledger.append(['Hotel', 'Sold', 'Available', 'Revenue'])
    ledger.append(['Willowbank', 800, 1000, 120000])
    output = workbook.create_sheet(PUBLISHED)
    output['C2'] = occupancy
    output['D2'] = '=SUM(Ledger!D2)/Ledger!B2'
    output['E2'] = eligibility
    workbook.save(original)
    data_before = original.read_bytes()
    scenarios = [
        {'name': 'current', 'expected': {(PUBLISHED, 'C2'): Fraction(4, 5),
                                         (PUBLISHED, 'D2'): 150, (PUBLISHED, 'E2'): 1}},
        {'name': 'ledger_correction', 'inputs': {('Ledger', 'B2'): 900},
         'expected': {(PUBLISHED, 'C2'): Fraction(9, 10), (PUBLISHED, 'D2'): Fraction(400, 3)}},
        {'name': 'prior_metric_override', 'inputs': {(PUBLISHED, 'C2'): 0.70, (PUBLISHED, 'D2'): 140},
         'expected': {(PUBLISHED, 'E2'): 0}},
    ]
    result = verify_formula_function(original, scenarios)
    assert original.read_bytes() == data_before
    return result


def test_calculation_accepts_alternative_excel_formula_and_uses_prior_metrics(tmp_path):
    result = exercise(tmp_path, '=Ledger!B2/Ledger!C2', '=--AND(C2>=0.75,D2>=125)')
    assert result['pass'], result
    assert result['scenarios'][2]['mutations'][0]['before'] == '=Ledger!B2/Ledger!C2'
    assert result['scenarios'][2]['mutations'][0]['changed']


def test_calculation_rejects_correct_on_start_constant_and_dependency_shortcut(tmp_path):
    result = exercise(tmp_path, '=0.8', '=--AND(Ledger!B2/Ledger!C2>=0.75,Ledger!D2/Ledger!B2>=125)')
    assert not result['pass']
    assert result['scenarios'][0]['pass']
    assert not result['scenarios'][1]['checks'][0]['pass']
    assert not result['scenarios'][2]['checks'][0]['pass']


def test_caller_text_is_opt_in_and_remains_a_native_literal(tmp_path):
    path = tmp_path / 'approval.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '审批办理'
    sheet['B6'] = '新签'
    sheet['B8'] = '=IF(B6="续约",1,0)'
    workbook.save(path)
    workbook.close()
    target = ('审批办理', 'B8')
    caller = ('审批办理', 'B6')
    scenarios = [
        {'name': 'current', 'expected': {target: 0}},
        {'name': 'renew', 'inputs': {caller: '续约'}, 'expected': {target: 1}},
    ]
    with pytest.raises(ValueError, match='enabled safe text literals'):
        verify_formula_function(path, scenarios)
    result = verify_formula_function(path, scenarios, allow_text_inputs=True)
    assert result['pass']
    assert result['scenarios'][1]['mutations'][0]['observed'] == '续约'
    scenarios[1]['inputs'][caller] = '=1+1'
    with pytest.raises(ValueError, match='enabled safe text literals'):
        verify_formula_function(path, scenarios, allow_text_inputs=True)
