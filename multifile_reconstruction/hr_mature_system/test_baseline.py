from __future__ import annotations

from datetime import date
from pathlib import Path
from shutil import copytree
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from . import oracle
from .hr_workflow import (
    WorkflowError, approve_departure, approve_hiring, issue_hiring, onboard,
    publish_workspace, set_hiring_decision, submit_departure,
)
from .workspace import build_workspace


def make(tmp_path):
    root = tmp_path / "workspace"
    build_workspace(root)
    return root


def test_f1_employee_query_reads_correct_employee(tmp_path):
    root = make(tmp_path)
    actual = oracle.employee(root / "HR_Operations.xlsx", "E001")
    assert actual["DisplayName"] == "Avery"
    assert actual["Department"] == "People"
    assert oracle.employee(root / "HR_Operations.xlsx", "E007")["Department"] == "Technology"


def test_f2_department_statistics_change_after_hire(tmp_path):
    root = make(tmp_path)
    book = load_workbook(root / "HR_Operations.xlsx")
    before = oracle.headcount_by_department(root / "HR_Operations.xlsx", date(2026, 7, 1))
    book["Employees"].append(["E009", "Morgan", "People", "Coordinator", date(2026, 6, 15), date(2027, 6, 15), "Bachelor", 60000, "Active", "", "People"])
    book.save(root / "HR_Operations.xlsx")
    after = oracle.headcount_by_department(root / "HR_Operations.xlsx", date(2026, 7, 1))
    assert after["People"] == before["People"] + 1
    assert sum(after.values()) == sum(before.values()) + 1


def test_f3_monthly_hires_and_departures_are_distinct_events(tmp_path):
    root = make(tmp_path)
    book = load_workbook(root / "HR_Operations.xlsx")
    book["Employees"].append(["E009", "Morgan", "People", "Coordinator", date(2026, 6, 15), date(2027, 6, 15), "Bachelor", 60000, "Active", "", "People"])
    book.save(root / "HR_Operations.xlsx")
    assert oracle.headcount_by_department(root / "HR_Operations.xlsx", date(2026, 7, 1))["People"] == 3
    submit_departure(root / "HR_Operations.xlsx", "D001")
    approve_departure(root / "HR_Operations.xlsx", "D001")
    assert oracle.headcount_by_department(root / "HR_Operations.xlsx", date(2026, 8, 1))["Operations"] == 1


def test_f4_contract_reminder_inclusive_boundary(tmp_path):
    root = make(tmp_path)
    book = load_workbook(root / "HR_Operations.xlsx")
    baseline = oracle.contract_due(root / "HR_Operations.xlsx", date(2026, 11, 25))
    book["Employees"]["F2"] = date(2027, 1, 9)
    book.save(root / "HR_Operations.xlsx")
    changed = oracle.contract_due(root / "HR_Operations.xlsx", date(2026, 11, 25))
    assert "E001" not in baseline and "E001" in changed


def test_f5_cost_aggregation_matches_independent_calculation(tmp_path):
    root = make(tmp_path)
    before = oracle.cost_total(root / "HR_Operations.xlsx", date(2026, 1, 1))
    book = load_workbook(root / "HR_Operations.xlsx")
    book["MonthlyCosts"]["C2"] = book["MonthlyCosts"]["C2"].value + 1000
    book.save(root / "HR_Operations.xlsx")
    after = oracle.cost_total(root / "HR_Operations.xlsx", date(2026, 1, 1))
    assert after == before + 1000


def test_f6_hiring_request_eligibility_uses_policy_and_capacity(tmp_path):
    root = make(tmp_path)
    approve_hiring(root / "HR_Operations.xlsx", "R001")
    approve_hiring(root / "HR_Operations.xlsx", "R002")
    approve_hiring(root / "HR_Operations.xlsx", "R003")
    book = load_workbook(root / "HR_Operations.xlsx", data_only=True)
    states = {book["HiringRequests"].cell(row, 1).value: book["HiringRequests"].cell(row, 8).value for row in range(2, 5)}
    book.close()
    assert states == {"R001": "Pending", "R002": "Rejected", "R003": "Rejected"}


def test_f7_decision_and_slot_reservation_prevent_second_issue(tmp_path):
    root = make(tmp_path)
    path = root / "HR_Operations.xlsx"
    approve_hiring(path, "R001")
    set_hiring_decision(path, "R001", "Approved")
    issue_hiring(path, "R001", date(2026, 6, 10))
    book = load_workbook(path, data_only=True)
    assert book["HiringRequests"]["H2"].value == "Issued"
    assert book["HiringRequests"]["J2"].value.date() == date(2026, 6, 10)
    assert oracle.assert_event_count(path, "issue", "R001") == 1
    book.close()
    try:
        issue_hiring(path, "R001", date(2026, 6, 10))
    except WorkflowError:
        pass
    else:
        raise AssertionError("Issuing the same request twice must be rejected")


def test_f8_onboarding_requires_issue_and_is_idempotently_blocked(tmp_path):
    root = make(tmp_path)
    path = root / "HR_Operations.xlsx"
    try:
        onboard(path, "R001", "E009", "Morgan", date(2026, 6, 15))
    except WorkflowError:
        pass
    else:
        raise AssertionError("Draft request cannot create an employee")
    approve_hiring(path, "R001")
    set_hiring_decision(path, "R001", "Approved")
    issue_hiring(path, "R001", date(2026, 6, 10))
    onboard(path, "R001", "E009", "Morgan", date(2026, 6, 15))
    book = load_workbook(path, data_only=True)
    assert sum(book["Employees"].cell(row, 1).value == "E009" for row in range(2, book["Employees"].max_row + 1)) == 1
    assert book["HiringRequests"]["H2"].value == "Closed"
    assert oracle.headcount_by_department(path, date(2026, 7, 1))["People"] == 3
    book.close()


def test_f9_departure_archives_once_and_preserves_department(tmp_path):
    root = make(tmp_path)
    path = root / "HR_Operations.xlsx"
    submit_departure(path, "D001")
    approve_departure(path, "D001")
    book = load_workbook(path, data_only=True)
    assert book["Employees"]["I4"].value == "Departed"
    assert book["Departures"]["C2"].value == "Operations"
    assert book["Departures"]["F2"].value == "Approved"
    assert oracle.headcount_by_department(path, date(2026, 8, 1))["Operations"] == 1
    book.close()
    try:
        approve_departure(path, "D001")
    except WorkflowError:
        pass
    else:
        raise AssertionError("Approved departure cannot be approved again")


def test_f10_published_ppt_snapshot_is_immutable(tmp_path):
    root = make(tmp_path)
    path = root / "HR_Operations.xlsx"
    deck_path = root / "HR_Operations.pptx"
    first = publish_workspace(root, date(2026, 6, 1))
    deck = Presentation(deck_path)
    first_text = [shape.text for shape in deck.slides[2].shapes if shape.has_text_frame]
    book = load_workbook(path)
    book["MonthlyCosts"]["C2"] = book["MonthlyCosts"]["C2"].value + 1000
    book.save(path)
    second = publish_workspace(root, date(2026, 7, 1))
    deck = Presentation(deck_path)
    assert first != second
    assert [shape.text for shape in deck.slides[2].shapes if shape.has_text_frame] == first_text
    assert len(deck.slides) == 4
    book = load_workbook(path, data_only=True)
    assert book["PublishedSnapshots"].max_row == 3
    assert book["PublishedSnapshots"]["A2"].value == first
    assert book["PublishedSnapshots"]["A3"].value == second
    book.close()


def test_word_policy_is_versioned_and_contains_state_rules(tmp_path):
    root = make(tmp_path)
    text = "\n".join(paragraph.text for paragraph in Document(root / "HR_Policy.docx").paragraphs)
    assert "P2026" in text and "Effective 1 January 2026" in text
    assert "Only an Approved request" in text
    assert "Published snapshots" in text


def test_workbook_has_formula_views_and_snapshot_ledger(tmp_path):
    root = make(tmp_path)
    book = load_workbook(root / "HR_Operations.xlsx", data_only=False)
    assert len(book.sheetnames) == 9
    assert book["MonthlyDashboard"]["F2"].value.startswith("=")
    assert book["MonthlyDashboard"]["G2"].value.startswith("=")
    assert book["MonthlyDashboard"]["H2"].value.startswith("=")
    assert book["PublishedSnapshots"].max_row == 1
    book.close()
    with ZipFile(root / "HR_Operations.pptx") as package:
        assert "ppt/presentation.xml" in package.namelist()
