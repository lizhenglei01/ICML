"""Independent, deliberately small business oracle for the HR workspace."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


def employee(workbook: Path, employee_id: str) -> dict:
    book = load_workbook(workbook, data_only=True, read_only=True)
    sheet = book["Employees"]
    headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
    row = next((row for row in sheet.iter_rows(min_row=2, values_only=True) if row[0] == employee_id), None)
    book.close()
    if row is None:
        raise KeyError(employee_id)
    return dict(zip(headers, row))


def headcount_by_department(workbook: Path, cutoff: date) -> dict[str, int]:
    book = load_workbook(workbook, data_only=True, read_only=True)
    employees = list(book["Employees"].values)
    departures = list(book["Departures"].values)
    count = Counter()
    for row in employees[1:]:
        hire_date = row[4].date() if isinstance(row[4], datetime) else row[4]
        if hire_date and hire_date < cutoff:
            count[row[2]] += 1
    for row in departures[1:]:
        last_day = row[4].date() if isinstance(row[4], datetime) else row[4]
        if row[5] == "Approved" and last_day and last_day < cutoff:
            count[row[2]] -= 1
    book.close()
    return dict(count)


def cost_total(workbook: Path, month: date) -> float:
    book = load_workbook(workbook, data_only=True, read_only=True)
    total = sum(sum(value or 0 for value in row[2:7]) for row in list(book["MonthlyCosts"].values)[1:]
                if (row[0].date() if isinstance(row[0], datetime) else row[0]) == month)
    book.close()
    return round(total, 2)


def contract_due(workbook: Path, as_of: date, window_days=45) -> list[str]:
    book = load_workbook(workbook, data_only=True, read_only=True)
    due = []
    for row in list(book["Employees"].values)[1:]:
        end = row[5]
        end = end.date() if isinstance(end, datetime) else end
        if row[8] == "Active" and isinstance(end, date) and as_of <= end <= as_of.fromordinal(as_of.toordinal() + window_days):
            due.append(row[0])
    book.close()
    return due


def eligible_hiring(workbook: Path, request_id: str) -> tuple[bool, str]:
    book = load_workbook(workbook, data_only=True, read_only=True)
    request = next(row for row in list(book["HiringRequests"].values)[1:] if row[0] == request_id)
    policy = next(row for row in list(book["Policy"].values)[1:] if row[3] == request[1])
    employees = list(book["Employees"].values)[1:]
    requests = list(book["HiringRequests"].values)[1:]
    active = sum(row[2] == request[1] and row[8] == "Active" for row in employees)
    reserved = sum(row[1] == request[1] and row[7] in {"Pending", "Approved", "Issued"} for row in requests)
    slots = active + reserved + request[3]
    ok = slots <= policy[4] and request[4] <= min(request[5], policy[5])
    reason = "eligible" if ok else ("headcount cap exceeded" if slots > policy[4] else "salary cap exceeded")
    book.close()
    return ok, reason


def assert_event_count(workbook: Path, event_type: str, entity_id: str) -> int:
    book = load_workbook(workbook, data_only=True, read_only=True)
    count = sum(row[1] == event_type and row[2] == entity_id for row in list(book["Events"].values)[1:])
    book.close()
    return count
