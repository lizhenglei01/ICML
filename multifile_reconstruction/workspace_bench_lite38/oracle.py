"""Private independent oracle for the effective-dated transfer case."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


def baseline_department(workbook: Path, employee_id: str) -> str:
    book = load_workbook(workbook, data_only=True, read_only=True)
    sheet = book["ActiveEmployees"]
    dept = next(row[21] for row in list(sheet.values)[5:] if row[2] == employee_id)
    book.close()
    return dept


def baseline_period_totals(workbook: Path, start: date, end: date) -> dict:
    book = load_workbook(workbook, data_only=True, read_only=True)
    hires = Counter()
    separations = Counter()
    opening = Counter()
    current = Counter()
    for row in list(book["ActiveEmployees"].values)[5:]:
        hired, dept = row[19], row[21]
        hired = hired.date() if isinstance(hired, datetime) else hired
        if hired and start <= hired <= end:
            hires[dept] += 1
        if hired and hired < start:
            opening[dept] += 1
        if dept:
            current[dept] += 1
    for row in list(book["SeparatedEmployeeArchive"].values)[5:]:
        hired, last_day, dept = row[12], row[16], row[14]
        hired = hired.date() if isinstance(hired, datetime) else hired
        last_day = last_day.date() if isinstance(last_day, datetime) else last_day
        if hired and start <= hired <= end:
            hires[dept] += 1
        if last_day and start <= last_day <= end:
            separations[dept] += 1
        if hired and hired < start and last_day and last_day >= end:
            opening[dept] += 1
    book.close()
    return {"hires": hires, "separations": separations, "opening": opening, "current": current}


def expected_transfer_effects(source: str, destination: str, baseline: dict) -> dict:
    current = baseline["current"].copy()
    current[source] -= 1
    current[destination] += 1
    return {"current": current, "opening": baseline["opening"], "hires": baseline["hires"],
            "separations": baseline["separations"], "company_total_delta": 0}


def baseline_as_of_headcount(workbook: Path, as_of: date, *, employee_id: str,
                             from_department: str, to_department: str,
                             effective_date: date) -> dict:
    """Count people employed on a date using source records, independently of workbook formulas."""
    book = load_workbook(workbook, data_only=True, read_only=True)
    counts = Counter()

    def as_date(value):
        return value.date() if isinstance(value, datetime) else value

    for row in list(book["ActiveEmployees"].values)[5:]:
        hired, department, eid = as_date(row[19]), row[21], row[2]
        if not hired or hired > as_of or not department:
            continue
        if eid == employee_id:
            department = to_department if as_of >= effective_date else from_department
        counts[department] += 1

    for row in list(book["SeparatedEmployeeArchive"].values)[5:]:
        hired, last_day, department, eid = as_date(row[12]), as_date(row[16]), row[14], row[2]
        if not hired or hired > as_of or not last_day or last_day <= as_of or not department:
            continue
        if eid == employee_id:
            department = to_department if as_of >= effective_date else from_department
        counts[department] += 1

    book.close()
    return dict(counts)


def score(path: Path, original: Path) -> dict:
    expected_source = baseline_department(original, "G01")
    baseline = baseline_period_totals(original, date(2020, 8, 1), date(2020, 8, 31))
    expected = expected_transfer_effects(expected_source, "Sales Dept.", baseline)
    book = load_workbook(path, data_only=False)
    transfer = book["DepartmentTransfers"] if "DepartmentTransfers" in book else None
    ledger = []
    if transfer:
        for row in range(2, transfer.max_row + 1):
            values = [transfer.cell(row, col).value for col in range(1, 7)]
            if values[1] == "G01":
                ledger.append(values)
    profile_rows = [row for row in range(6, 70) if book["ActiveEmployees"].cell(row, 3).value == "G01"]
    actual_department = book["ActiveEmployees"].cell(profile_rows[0], 22).value if profile_rows else None
    formulas = {f"{sheet}!{cell}": book[sheet][cell].value for sheet, cell in (
        ("MonthlyChangeQuery", "E8"), ("MonthlyChangeQuery", "I8"),
        ("AnnualHeadcountHelper", "E8"), ("AnnualHeadcountHelper", "I8"))}
    book.close()
    return {"pass": actual_department == "Sales Dept." and len(ledger) == 1 and ledger[0][2] == expected_source
            and ledger[0][3] == "Sales Dept." and ledger[0][4] in (date(2020, 8, 15), datetime(2020, 8, 15)),
            "expected_source": expected_source, "expected_effects": expected,
            "actual_current_department": actual_department, "transfer_records_for_G01": ledger,
            "relevant_formula_cells": formulas}
