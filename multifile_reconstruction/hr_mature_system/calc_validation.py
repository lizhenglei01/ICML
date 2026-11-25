"""Recalculate real workbook formulas under controlled baseline input probes."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.workbook.properties import CalcProperties

from .oracle import cost_total, headcount_by_department
from .workspace import build_workspace


TESTS = {
    "department_move": {
        "input": ("Employees", "C2", "Finance"),
        "fix_today": False,
        "outputs": {"MonthlyDashboard!F2": 1, "MonthlyDashboard!F6": 1},
        "expected_delta": {"MonthlyDashboard!F2": -1, "MonthlyDashboard!F6": 1},
    },
    "monthly_hire": {
        "input": ("Employees", "E2", datetime(2026, 1, 15)),
        "outputs": {"MonthlyDashboard!D2": 1},
        "expected_delta": {"MonthlyDashboard!D2": 1},
    },
    "approved_departure": {
        "input": ("Departures", "F2", "Approved"),
        "second_input": ("Departures", "E2", datetime(2026, 1, 15)),
        "outputs": {"MonthlyDashboard!E4": 1},
        "expected_delta": {"MonthlyDashboard!E4": 1},
    },
    "contract_reminder": {
        "input": ("Employees", "F2", datetime(2027, 1, 9)),
        "fix_today": True,
        "outputs": {"Employees!AA2": "Yes", "Home!J12": 1},
        "expected_delta": {"Home!J12": 1},
    },
    "monthly_cost": {
        "input": ("MonthlyCosts", "C2", 1000),
        "numeric_delta": True,
        "outputs": {"MonthlyDashboard!G2": 1000, "MonthlyDashboard!H2": 1000},
        "expected_delta": {"MonthlyDashboard!G2": 1000, "MonthlyDashboard!H2": 1000},
    },
}


def _recalculate(source: Path, output: Path) -> dict:
    output.mkdir(parents=True)
    profile = output.parent / "lo-profile"
    command = [
        "libreoffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
        "--nodefault", "--nofirststartwizard", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
        "--outdir", str(output), str(source),
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    result = output / source.name
    if run.returncode or not result.is_file():
        raise RuntimeError(f"LibreOffice did not produce recalculated workbook: {run.stderr[-1000:]}")
    return {"stderr": run.stderr.strip()[-500:], "path": result}


def validate(workspace: Path) -> dict:
    source = workspace / "HR_Operations.xlsx"
    report = {"method": "LibreOffice Calc; independent expected deltas from isolated test inputs", "tests": []}
    for test_name, spec in TESTS.items():
        case = {"name": test_name, "pass": False, "outputs": []}
        with TemporaryDirectory(prefix=f"hr_{test_name}_") as temporary:
            root = Path(temporary)
            scenario_results = []
            for label in ("baseline", "changed"):
                input_dir = root / label
                output_dir = root / f"{label}_out"
                input_dir.mkdir()
                dest = input_dir / "HR_Operations.xlsx"
                book = load_workbook(source, data_only=False)
                if spec.get("fix_today"):
                    for sheet in book:
                        for row in sheet.iter_rows():
                            for cell in row:
                                if cell.data_type == "f" and "TODAY()" in cell.value:
                                    cell.value = cell.value.replace("TODAY()", "DATE(2026,11,25)")
                if label == "changed":
                    for key in ("input", "second_input"):
                        if key in spec:
                            sheet, address, value = spec[key]
                            cell = book[sheet][address]
                            cell.value = cell.value + value if spec.get("numeric_delta") else value
                book.calculation = CalcProperties(calcMode="auto", fullCalcOnLoad=True, forceFullCalc=True)
                book.save(dest)
                book.close()
                recalc = _recalculate(dest, output_dir)
                values_book = load_workbook(recalc["path"], data_only=True, read_only=True)
                values = {}
                for target in spec["outputs"]:
                    sheet, address = target.split("!")
                    values[target] = values_book[sheet][address].value
                errors = [(sheet.title, cell.coordinate, cell.value) for sheet in values_book
                          for row in sheet.iter_rows() for cell in row
                          if isinstance(cell.value, str) and cell.value.startswith("#")]
                values_book.close()
                scenario_results.append({"label": label, "values": values, "error_count": len(errors), "errors": errors[:8]})
            before, after = scenario_results
            for target, expected in spec["outputs"].items():
                if isinstance(expected, str) and after["values"][target] != expected:
                    case["pass"] = False
                    break
            else:
                case["pass"] = True
            for target, delta in spec["expected_delta"].items():
                left, right = before["values"][target], after["values"][target]
                if not isinstance(left, (int, float)) or not isinstance(right, (int, float)) or right - left != delta:
                    case["pass"] = False
                    break
            case["scenarios"] = scenario_results
        report["tests"].append(case)
    report["pass"] = all(test["pass"] for test in report["tests"])
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    workspace = args.workspace
    if workspace is None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            build_workspace(workspace)
            report = validate(workspace)
    else:
        report = validate(workspace)
    rendered = json.dumps(report, default=str, indent=2)
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered)
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
