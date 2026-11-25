"""Editable business workflow shipped as part of the initial HR system."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from openpyxl import load_workbook
from pptx import Presentation
from pptx.util import Inches, Pt


class WorkflowError(ValueError):
    pass


def _book(path):
    return load_workbook(path)


def _row(sheet, column, value, start=2):
    for row in range(start, sheet.max_row + 1):
        if sheet.cell(row, column).value == value:
            return row
    raise WorkflowError(f"Unknown record: {value}")


def query_employee(path: Path, employee_id: str) -> dict:
    book = _book(path)
    sheet = book["Employees"]
    row = _row(sheet, 1, employee_id)
    result = {sheet.cell(1, col).value: sheet.cell(row, col).value for col in range(1, sheet.max_column + 1)}
    book.close()
    return result


def department_counts(path: Path, report_month: date) -> dict:
    book = _book(path, )
    employees, departures = book["Employees"], book["Departures"]
    result = {}
    for row in range(2, employees.max_row + 1):
        if employees.cell(row, 9).value != "Active":
            continue
        hired, dept = employees.cell(row, 5).value, employees.cell(row, 3).value
        if hired and hired < report_month:
            result[dept] = result.get(dept, 0) + 1
    for row in range(2, departures.max_row + 1):
        if departures.cell(row, 6).value == "Approved" and departures.cell(row, 5).value < report_month:
            dept = departures.cell(row, 3).value
            result[dept] = result.get(dept, 0) - 1
    book.close()
    return result


def check_hiring(path: Path, request_id: str) -> tuple[bool, str]:
    book = _book(path)
    sheet = book["HiringRequests"]
    row = _row(sheet, 1, request_id)
    dept, headcount, salary, budget = (sheet.cell(row, col).value for col in (2, 4, 5, 6))
    policy = book["Policy"]
    policy_row = _row(policy, 4, dept)
    cap, salary_cap = policy.cell(policy_row, 5).value, policy.cell(policy_row, 6).value
    active = sum(1 for r in range(2, book["Employees"].max_row + 1)
                 if book["Employees"].cell(r, 3).value == dept and book["Employees"].cell(r, 9).value == "Active")
    reserved = sum(book["HiringRequests"].cell(r, 4).value or 0 for r in range(2, sheet.max_row + 1)
                   if book["HiringRequests"].cell(r, 2).value == dept and book["HiringRequests"].cell(r, 8).value in {"Pending", "Approved", "Issued"})
    ok = active + reserved + headcount <= cap and salary <= min(budget, salary_cap)
    reason = "eligible" if ok else ("headcount cap exceeded" if active + reserved + headcount > cap else "salary cap exceeded")
    book.close()
    return ok, reason


def _append_event(book, kind, entity_id, when, from_state, to_state, actor, details=""):
    events = book["Events"]
    event_id = f"EV-{uuid4().hex[:10]}"
    events.append([event_id, kind, entity_id, when, from_state, to_state, actor, details])


def approve_hiring(path: Path, request_id: str, actor="HR") -> None:
    book = _book(path)
    sheet = book["HiringRequests"]
    row = _row(sheet, 1, request_id)
    if sheet.cell(row, 8).value != "Draft":
        book.close()
        raise WorkflowError("Only Draft requests can enter review")
    ok, reason = check_hiring(path, request_id)
    sheet.cell(row, 8).value = "Pending" if ok else "Rejected"
    sheet.cell(row, 9).value = reason
    _append_event(book, "eligibility_check", request_id, date.today(), "Draft", sheet.cell(row, 8).value, actor, reason)
    book.save(path)
    book.close()


def issue_hiring(path: Path, request_id: str, issue_date: date, actor="HR") -> None:
    book = _book(path)
    sheet = book["HiringRequests"]
    row = _row(sheet, 1, request_id)
    if sheet.cell(row, 8).value != "Approved":
        book.close()
        raise WorkflowError("Only Approved requests can be issued")
    sheet.cell(row, 8).value = "Issued"
    sheet.cell(row, 10).value = issue_date
    _append_event(book, "issue", request_id, issue_date, "Approved", "Issued", actor)
    book.save(path)
    book.close()


def set_hiring_decision(path: Path, request_id: str, decision: str, actor="HR") -> None:
    if decision not in {"Approved", "Rejected"}:
        raise WorkflowError("Decision must be Approved or Rejected")
    book = _book(path)
    sheet = book["HiringRequests"]
    row = _row(sheet, 1, request_id)
    if sheet.cell(row, 8).value != "Pending":
        book.close()
        raise WorkflowError("Only Pending requests can be decided")
    old = sheet.cell(row, 8).value
    sheet.cell(row, 8).value = decision
    _append_event(book, "decision", request_id, date.today(), old, decision, actor)
    book.save(path)
    book.close()


def onboard(path: Path, request_id: str, employee_id: str, display_name: str, hire_date: date) -> None:
    book = _book(path)
    requests, employees = book["HiringRequests"], book["Employees"]
    req_row = _row(requests, 1, request_id)
    if requests.cell(req_row, 8).value != "Issued":
        book.close()
        raise WorkflowError("Only Issued requests can create an employee")
    if any(employees.cell(r, 1).value == employee_id for r in range(2, employees.max_row + 1)):
        book.close()
        raise WorkflowError("Employee ID already exists")
    dept, role, salary = requests.cell(req_row, 2).value, requests.cell(req_row, 3).value, requests.cell(req_row, 5).value
    employees.append([employee_id, display_name, dept, role, hire_date, date(hire_date.year + 1, hire_date.month, hire_date.day), "Not supplied", salary, "Active", "", dept])
    requests.cell(req_row, 8).value = "Closed"
    requests.cell(req_row, 11).value = employee_id
    _append_event(book, "onboard", request_id, hire_date, "Issued", "Closed", "HR", employee_id)
    book.save(path)
    book.close()


def submit_departure(path: Path, departure_id: str, actor="HR") -> None:
    book = _book(path)
    sheet = book["Departures"]
    row = _row(sheet, 1, departure_id)
    if sheet.cell(row, 6).value != "Draft":
        book.close()
        raise WorkflowError("Only Draft departures can be submitted")
    sheet.cell(row, 6).value = "Pending"
    _append_event(book, "departure_submit", departure_id, date.today(), "Draft", "Pending", actor)
    book.save(path)
    book.close()


def approve_departure(path: Path, departure_id: str, actor="HR") -> None:
    book = _book(path)
    sheet = book["Departures"]
    row = _row(sheet, 1, departure_id)
    if sheet.cell(row, 6).value != "Pending":
        book.close()
        raise WorkflowError("Only Pending departures can be approved")
    employee_id, last_day = sheet.cell(row, 2).value, sheet.cell(row, 5).value
    active_row = _row(book["Employees"], 1, employee_id)
    old_dept = book["Employees"].cell(active_row, 3).value
    if book["Employees"].cell(active_row, 9).value != "Active":
        book.close()
        raise WorkflowError("Employee is not active")
    book["Employees"].cell(active_row, 9).value = "Departed"
    sheet.cell(row, 3).value = old_dept
    sheet.cell(row, 6).value = "Approved"
    sheet.cell(row, 8).value = date.today()
    _append_event(book, "departure_approve", departure_id, last_day, "Pending", "Approved", actor, f"department_at_exit={old_dept}")
    book.save(path)
    book.close()


def publish_month(path: Path, ppt_path: Path, month: date) -> str:
    book = _book(path)
    snap_id = f"S-{month:%Y%m}-{book['PublishedSnapshots'].max_row:03d}"
    employees = book["Employees"]
    headcount = sum(1 for r in range(2, employees.max_row + 1) if employees.cell(r, 9).value == "Active")
    cost = sum(sum((book["MonthlyCosts"].cell(r, col).value or 0) for col in range(3, 8))
               for r in range(2, book["MonthlyCosts"].max_row + 1)
               if book["MonthlyCosts"].cell(r, 1).value == month)
    pending = sum(1 for r in range(2, book["HiringRequests"].max_row + 1) if book["HiringRequests"].cell(r, 8).value == "Pending")
    policy_id = "P2026"
    published = date.today()
    book["PublishedSnapshots"].append([snap_id, month, policy_id, published, headcount, round(cost, 2), pending, snap_id])
    book.save(path)
    book.close()

    deck = Presentation(ppt_path)
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = f"Snapshot {snap_id} · {month:%B %Y}"
    for i, (label, value) in enumerate([("Headcount", headcount), ("People cost", f"{cost:,.2f}"), ("Pending requests", pending), ("Policy", policy_id)]):
        box = slide.shapes.add_textbox(Inches(0.9 + (i % 2) * 5.7), Inches(1.8 + (i // 2) * 1.8), Inches(5.0), Inches(1.0))
        box.text_frame.text = f"{label}: {value}"
        box.text_frame.paragraphs[0].font.size = Pt(24)
    deck.save(ppt_path)
    return snap_id


def publish_workspace(workspace: Path, month: date) -> str:
    return publish_month(workspace / "HR_Operations.xlsx", workspace / "HR_Operations.pptx", month)
