"""Recalculate selected Lite 38 baseline and transfer paths and score against the independent oracle."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import subprocess
from openpyxl import load_workbook
from .oracle import baseline_department, baseline_period_totals, baseline_as_of_headcount, expected_transfer_effects
from openpyxl.styles import PatternFill

ROOT = Path(__file__).parent
SOURCE = ROOT / "private" / "lite38_original.xlsx"
GOLD = ROOT / "private" / "gold.xlsx"
OUTPUT = ROOT / "private" / "m1_validation.json"
MONTH_COLS = {"Finance Dept.": "C", "R and D Dept.": "D", "Sales Dept.": "E",
              "Technology Dept.": "F", "Engineering Dept.": "G",
              "HR and Administration Dept.": "H", "Executive Office": "I"}


def recalc(src: Path, month: int, fix_today=False, day=None):
    temp = TemporaryDirectory(prefix="lite38_m1_")
    root = Path(temp.name)
    (root / "input").mkdir()
    (root / "output").mkdir()
    wb = load_workbook(src, data_only=False)
    start = datetime(2020, month, day or 1)
    end = datetime(2020, month, day) if day else datetime(2020, month + 1, 1) - datetime.resolution
    for name in ("MonthlyChangeQuery", "AnnualHeadcountHelper"):
        wb[name]["H4"] = start
        wb[name]["J4"] = end
    wb["EmployeeRecordQuery"]["C5"] = "Employee 1"
    contract_flag_formula = wb["ActiveEmployees"]["AA6"].value
    if fix_today:
        for sheet in wb:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "f" and "TODAY()" in cell.value:
                        cell.value = cell.value.replace("TODAY()", "DATE(2026,11,25)")
    workbook = root / "input" / "case.xlsx"
    wb.save(workbook)
    wb.close()
    profile = (root / "lo_profile").as_uri()
    run = subprocess.run([
        "libreoffice", f"-env:UserInstallation={profile}", "--headless", "--norestore",
        "--nodefault", "--nofirststartwizard", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
        "--outdir", str(root / "output"), str(workbook),
    ], capture_output=True, text=True, timeout=90)
    result = root / "output" / "case.xlsx"
    if run.returncode or not result.exists():
        temp.cleanup()
        raise RuntimeError(f"Calc failed: {run.stderr[-800:]}")
    values = load_workbook(result, data_only=True, read_only=True)
    data = {
        "month": month,
        "profile_department": values["EmployeeRecordQuery"]["E19"].value,
        "opening": {dept: values["MonthlyChangeQuery"][f"{col}8"].value for dept, col in MONTH_COLS.items()},
        "current": {dept: values["MonthlyChangeQuery"][f"{col}11"].value for dept, col in MONTH_COLS.items()},
        "hires": {dept: values["MonthlyChangeQuery"][f"{col}7"].value for dept, col in MONTH_COLS.items()},
        "separations": {dept: values["MonthlyChangeQuery"][f"{col}6"].value for dept, col in MONTH_COLS.items()},
        "salary_cost": values["HRCostAnalysis"]["C11"].value,
        "total_cost": values["HRCostAnalysis"]["K11"].value,
        "contract_flag": values["ActiveEmployees"]["AA6"].value,
        "contract_flag_formula": contract_flag_formula,
        "home_contract_due_count": values["Home"]["J12"].value,
        "formula_error_count_entire_book": sum(
            isinstance(cell.value, str) and cell.value.startswith("#")
            for sheet in values for row in sheet.iter_rows() for cell in row
        ),
    }
    values.close()
    temp.cleanup()
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, help="Workbook returned by the Agent")
    parser.add_argument("--label", default="gold")
    args = parser.parse_args()
    report = {"date_frozen_to": "2020-08/09 report periods; 2026-11-25 for TODAY() reminders",
              "runs": {}, "m1_checks": []}
    candidate = args.candidate or GOLD
    for label, source in (("baseline", SOURCE), (args.label, candidate)):
        for month in (8, 9):
            report["runs"][f"{label}_2020_{month:02d}"] = recalc(source, month, fix_today=True)
    baseline_aug = report["runs"]["baseline_2020_08"]
    gold_aug = report["runs"][f"{args.label}_2020_08"]
    baseline_sep = report["runs"]["baseline_2020_09"]
    gold_sep = report["runs"][f"{args.label}_2020_09"]
    for day in (14, 15):
        report["runs"][f"{args.label}_2020_08_{day:02d}"] = recalc(candidate, 8, fix_today=True, day=day)
    source_dept = baseline_department(SOURCE, "G01")
    expected_aug = expected_transfer_effects(source_dept, "Sales Dept.",
        baseline_period_totals(SOURCE, datetime(2020, 8, 1).date(), datetime(2020, 8, 31).date()))
    expected_sep = baseline_period_totals(SOURCE, datetime(2020, 9, 1).date(), datetime(2020, 9, 30).date())
    expected_sep_open = expected_sep["opening"].copy()
    expected_sep_open[source_dept] -= 1
    expected_sep_open["Sales Dept."] += 1
    departments = list(MONTH_COLS)
    def complete(values):
        return {department: values.get(department, 0) for department in departments}
    expected_aug_end = complete(baseline_as_of_headcount(
        SOURCE, datetime(2020, 8, 31).date(), employee_id="G01", from_department=source_dept,
        to_department="Sales Dept.", effective_date=datetime(2020, 8, 15).date()))
    expected_sep_end = complete(baseline_as_of_headcount(
        SOURCE, datetime(2020, 9, 30).date(), employee_id="G01", from_department=source_dept,
        to_department="Sales Dept.", effective_date=datetime(2020, 8, 15).date()))
    expected_pre_cutoff = complete(baseline_as_of_headcount(
        SOURCE, datetime(2020, 8, 14).date(), employee_id="G01", from_department=source_dept,
        to_department="Sales Dept.", effective_date=datetime(2020, 8, 15).date()))
    expected_post_cutoff = complete(baseline_as_of_headcount(
        SOURCE, datetime(2020, 8, 15).date(), employee_id="G01", from_department=source_dept,
        to_department="Sales Dept.", effective_date=datetime(2020, 8, 15).date()))
    checks = [
        ("profile points to current destination", gold_aug["profile_department"] == "Sales Dept."),
        ("August opening headcount matches independent as-of oracle", gold_aug["opening"] == complete(expected_aug["opening"])),
        ("September opening headcount matches independent as-of oracle", gold_sep["opening"] == complete(expected_sep_open)),
        ("August month-end department headcount matches independent as-of oracle", gold_aug["current"] == expected_aug_end),
        ("September month-end department headcount matches independent as-of oracle", gold_sep["current"] == expected_sep_end),
        ("August 14 source department count is retained", report["runs"][f"{args.label}_2020_08_14"]["current"] == expected_pre_cutoff),
        ("August 15 destination department count takes effect", report["runs"][f"{args.label}_2020_08_15"]["current"] == expected_post_cutoff),
        ("monthly hire totals match independent oracle", gold_aug["hires"] == complete(expected_aug["hires"])
         and gold_sep["hires"] == complete(expected_sep["hires"])),
        ("monthly separation totals match independent oracle", gold_aug["separations"] == complete(expected_aug["separations"])
         and gold_sep["separations"] == complete(expected_sep["separations"])),
        ("company headcount preserved", sum(gold_aug["current"].values()) == sum(expected_aug_end.values())
         and sum(gold_sep["current"].values()) == sum(expected_sep_end.values())),
        ("HR cost function preserved", gold_aug["salary_cost"] == baseline_aug["salary_cost"] and gold_aug["total_cost"] == baseline_aug["total_cost"]),
        ("contract reminder formula preserved", gold_aug["contract_flag_formula"] == baseline_aug["contract_flag_formula"]),
        ("contract reminder dashboard preserved", gold_aug["home_contract_due_count"] == baseline_aug["home_contract_due_count"]),
    ]
    report["m1_checks"] = [{"check": name, "pass": passed} for name, passed in checks]
    report["pass"] = all(passed for _, passed in checks)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
