"""Check matched fixtures and real actions on native Office files."""
from .hotel_stateful_bench import BOOK, PROGRAM, start_workbook
from .hotel_upgrade_bench import grade, public_reports, validate_order
from .hotel_upgrade_cases import LedgerModel, fixture, generated_worlds
from .hotel_upgrade_diagnostic import retained_waiting_adr
from .hotel_upgrade_reference import upgrade_source


def test_generated_worlds_are_matched_and_order_sensitive():
    narrow = generated_worlds(3, 'narrow')
    expanded = generated_worlds(3, 'expanded')
    assert {(case['seed'], case['hotels'], case['capacity']) for case in narrow} == {
        (101, 4, 1), (202, 6, 2), (303, 8, 3),
    }
    cases = {case['name']: case for case in expanded}
    assert all(row == cases[row['name']] for row in narrow)
    validate_order(expanded)
    first = cases['101:refund_before_signing']['steps'][-1]['workbook']
    second = cases['101:signing_before_refund']['steps'][-1]['workbook']
    assert first['Ledger'] == second['Ledger']
    assert first['Issued'] != second['Issued']


def test_native_reports_and_signed_snapshot_follow_business_actions(tmp_path):
    template = fixture(0, 4, 1)
    start_workbook(tmp_path / BOOK, template)
    public_reports(tmp_path, LedgerModel(template).views())
    script = upgrade_source(3, 'expanded')
    cases = [row for row in generated_worlds(3, 'expanded')
             if row['name'] in {'101:cross_month_publish_versions', '101:reversal_after_slot_taken'}]
    (tmp_path / PROGRAM).write_text(script)
    assert grade(tmp_path, cases)['pass']
    (tmp_path / PROGRAM).write_text(script.replace("cell(application_row, 2, 'waiting')",
                                                   "cell(application_row, 2, 'reserved')"))
    wrong = grade(tmp_path, cases)
    assert wrong['cases'][0]['pass'] is False


def test_waiting_adr_diagnostic_keeps_original_and_promotion_authoritative():
    original = next(row for row in generated_worlds(3, 'expanded') if row['name'] == '101:reversal_before_promotion')
    alternative = retained_waiting_adr(original)
    reversal = 3
    assert original['steps'][reversal]['workbook']['Applications'][1] == ['Alder', 'waiting', 13000]
    assert alternative['steps'][reversal]['workbook']['Applications'][1] == ['Alder', 'waiting', 11900]
    assert alternative['steps'][reversal + 1:] == original['steps'][reversal + 1:]
