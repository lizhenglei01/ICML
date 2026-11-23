"""Reproduce the L3-016 empty-exception oracle boundary without editing DocOps."""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "三方依赖代码/DocOps"
TASK_ID = "docops_v2_l3_016_xlsx_forecast_driver_cascade"
TASK = REPO / "tasks" / TASK_ID
PIN = "ccf7a751bc5c8a69c1cdd0400125d53455b33be3"
ORDER = ["Dashboard", "Forecast Model", "Exception Review", "Assumptions", "Demand Raw", "Archive"]
NEEDLE = '    assert "capacity" in normalize_text(issue_text) or "breach" in normalize_text(issue_text)'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_records():
    commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    assert commit == PIN, commit
    paths = [TASK / "instruction.md", TASK / "tests/test_outputs.py",
             TASK / "tests/verifier_utils.py", TASK / "tests/task_metadata.json",
             TASK / "environment" / f"{TASK_ID}_input.xlsx"]
    result = {}
    for path in paths:
        rel = str(path.relative_to(REPO))
        original = subprocess.check_output(["git", "-C", str(REPO), "show", f"{PIN}:{rel}"])
        assert original == path.read_bytes(), f"Upstream file differs from pinned commit: {path}"
        result[rel] = digest(path)
    return result


def expected_from_input(path):
    """Independent exact arithmetic, not DocOps' _expected_breaches()."""
    book = load_workbook(path, data_only=True)
    assumptions = book["Assumptions"]
    scenario = assumptions["B5"].value
    col = next(c for c in range(2, assumptions.max_column + 1)
               if assumptions.cell(1, c).value == scenario)
    growth = Fraction(str(assumptions.cell(2, col).value))
    price = Fraction(str(assumptions.cell(3, col).value))
    capacity = Fraction(str(assumptions.cell(4, col).value))
    records = []
    for month, base, actual in book["Demand Raw"].iter_rows(min_row=2, values_only=True):
        forecast = Fraction(str(base)) * (1 + growth)
        records.append({"month": month, "base": base, "actual": actual,
                        "forecast": float(forecast), "price": float(price),
                        "revenue": float(forecast * price), "capacity": float(capacity),
                        "breach": forecast > capacity})
    return records


def build_submission(input_path, output_path, expected):
    """Produce a controlled correct candidate from the actual upstream workbook."""
    book = load_workbook(input_path)
    assumptions = book["Assumptions"]
    validation = DataValidation(type="list", formula1='"Base,Stress"')
    assumptions.add_data_validation(validation)
    validation.add(assumptions["B5"])
    model = book.create_sheet("Forecast Model")
    model.append(["Month", "Base Units", "Actual Units", "Scenario Growth", "Forecast Units",
                  "Price", "Revenue", "Capacity", "Capacity Flag"])
    for row, record in enumerate(expected, 2):
        def scenario_lookup(driver_row):
            return (f'=INDEX(Assumptions!$B${driver_row}:$C${driver_row},1,'
                    'MATCH(Assumptions!$B$5,Assumptions!$B$1:$C$1,0))')
        model.append([record["month"], f"='Demand Raw'!B{row}", f"='Demand Raw'!C{row}",
                      scenario_lookup(2), f"=B{row}*(1+D{row})", scenario_lookup(3),
                      f"=E{row}*F{row}", scenario_lookup(4),
                      f'=IF(E{row}>H{row},"BREACH","OK")'])
    table = Table(displayName="ForecastModel", ref="A1:I5")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    model.add_table(table)
    model.conditional_formatting.add("A2:I5", FormulaRule(
        formula=['$I2="BREACH"'], fill=PatternFill("solid", fgColor="FFF2CC")))
    model.freeze_panes = "A2"
    exceptions = book.create_sheet("Exception Review")
    exceptions.append(["Month", "Issue", "Forecast Units", "Capacity", "Overage"])
    # Formula-backed blank rows keep exception review responsive to later driver
    # changes. At zero breaches the visible table has no exception records.
    for model_row, record in enumerate(expected, 2):
        ex_row = exceptions.max_row + 1
        exceptions.append([
            f'=IF(\'Forecast Model\'!I{model_row}="BREACH",\'Forecast Model\'!A{model_row},"")',
            f'=IF(A{ex_row}="","","Capacity breach")',
            f'=IF(A{ex_row}="","",\'Forecast Model\'!E{model_row})',
            f'=IF(A{ex_row}="","",\'Forecast Model\'!H{model_row})',
            f'=IF(A{ex_row}="","",C{ex_row}-D{ex_row})'])
    dashboard = book["Dashboard"]
    dashboard["A1"] = '=CONCATENATE("Forecast Dashboard - ",Assumptions!B5," scenario")'
    dashboard["A3"] = "Total forecast revenue"
    dashboard["B3"] = "=SUM('Forecast Model'!G2:G5)"
    dashboard["A4"] = "Capacity breach count"
    dashboard["B4"] = '=COUNTIF(\'Forecast Model\'!I2:I5,"BREACH")'
    book["Demand Raw"].sheet_state = "hidden"
    book["Archive"].sheet_state = "hidden"
    book._sheets = [book[name] for name in ORDER]
    book.save(output_path)


def independent_audit(input_path, output_path, expected, destination):
    """Recalculate a disposable copy using LibreOffice, then inspect actual values."""
    original = load_workbook(input_path)
    candidate = load_workbook(output_path)
    checks = []

    def check(name, passed, detail=""):
        checks.append({"id": name, "passed": bool(passed), "detail": detail})

    check("sheet_order", candidate.sheetnames == ORDER)
    for name in ["Assumptions", "Demand Raw", "Archive"]:
        source_values = [[c.value for c in row] for row in original[name]]
        output_values = [[c.value for c in row] for row in candidate[name]]
        check(f"preserved_values_{name}", source_values == output_values)
    check("native_table", candidate["Forecast Model"].tables["ForecastModel"].ref == "A1:I5")
    check("scenario_validation", any("B5" in v for v in candidate["Assumptions"].data_validations.dataValidation))
    check("raw_hidden", candidate["Demand Raw"].sheet_state == "hidden")
    check("archive_hidden", candidate["Archive"].sheet_state == "hidden")
    check("formula_cells", all(candidate["Forecast Model"].cell(r, c).data_type == "f"
                               for r in range(2, 6) for c in range(2, 10)))
    with tempfile.TemporaryDirectory(prefix="docops-oracle-recalc-") as tmp:
        directory = Path(tmp)
        recalculated = directory / "recalculated"
        recalculated.mkdir()
        command = ["soffice", "--headless", f"-env:UserInstallation={(directory / 'profile').as_uri()}",
                   "--convert-to", "xlsx", "--outdir", str(recalculated), str(output_path)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (destination / "recalculation.log").write_text(process.stdout + process.stderr)
        check("recalculation_available", process.returncode == 0 and (recalculated / output_path.name).is_file())
        assert checks[-1]["passed"], process.stderr
        shutil.copy2(recalculated / output_path.name, destination / "independently_recalculated.xlsx")
        calculated = load_workbook(recalculated / output_path.name, data_only=True)
        model = calculated["Forecast Model"]
        for row, record in enumerate(expected, 2):
            for col, key in [(2, "base"), (3, "actual"), (5, "forecast"), (6, "price"), (7, "revenue"), (8, "capacity")]:
                value = model.cell(row, col).value
                check(f"{record['month']}_{key}", isinstance(value, (int, float)) and
                      math.isclose(value, record[key], rel_tol=1e-10, abs_tol=1e-8), str(value))
            check(f"{record['month']}_flag", model.cell(row, 9).value == ("BREACH" if record["breach"] else "OK"))
        breaches = [r for r in expected if r["breach"]]
        actual_months = [calculated["Exception Review"].cell(r, 1).value
                         for r in range(2, calculated["Exception Review"].max_row + 1)
                         if calculated["Exception Review"].cell(r, 1).value]
        check("exact_exception_months", actual_months == [r["month"] for r in breaches], str(actual_months))
        for row, record in enumerate(expected, 2):
            if not record["breach"]:
                continue
            ex = calculated["Exception Review"]
            check(f"exception_{record['month']}_numbers",
                  math.isclose(ex.cell(row, 3).value, record["forecast"], abs_tol=1e-8)
                  and math.isclose(ex.cell(row, 4).value, record["capacity"], abs_tol=1e-8)
                  and math.isclose(ex.cell(row, 5).value, record["forecast"] - record["capacity"], abs_tol=1e-8))
        count = calculated["Dashboard"]["B4"].value
        check("dashboard_breach_count", count == len(breaches), str(count))
        check("dashboard_revenue", math.isclose(calculated["Dashboard"]["B3"].value,
              sum(r["revenue"] for r in expected), abs_tol=1e-6))
        check("dashboard_title", calculated["Dashboard"]["A1"].value ==
              "Forecast Dashboard - " + str(original["Assumptions"]["B5"].value) + " scenario")
    result = {"passed": all(c["passed"] for c in checks),
              "method": "Exact Fraction arithmetic + independent LibreOffice recalculation + source-state checks",
              "expected_months": [r["month"] for r in expected if r["breach"]], "checks": checks}
    dump(destination / "independent_audit.json", result)
    assert result["passed"], result
    return result


def evaluate(test_file, metadata_path, log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TASK_METADATA_PATH=str(metadata_path), PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    command = [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=long",
               "-p", "no:cacheprovider", f"--junitxml={log_dir / 'junit.xml'}"]
    process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    (log_dir / "pytest.log").write_text(process.stdout + process.stderr)
    dump(log_dir / "command.json", {"argv": command, "metadata_path": str(metadata_path), "returncode": process.returncode})
    assert process.returncode in (0, 1), process.stdout + process.stderr
    tree = ET.parse(log_dir / "junit.xml")
    failures = [{"test": c.get("name"), "message": f.get("message"), "traceback": f.text}
                for c in tree.findall(".//testcase") for f in c.findall("failure")]
    result = {"passed": process.returncode == 0, "returncode": process.returncode, "failures": failures}
    dump(log_dir / "result.json", result)
    return result


def main():
    before = source_records()
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    source_metadata = json.loads((TASK / "tests/task_metadata.json").read_text())
    source_input = TASK / "environment" / Path(source_metadata["input_path"]).name
    original_test = TASK / "tests/test_outputs.py"
    text = original_test.read_text()
    assert text.count(NEEDLE) == 1
    diagnostic = results / "diagnostic_guard"
    diagnostic.mkdir(exist_ok=True)
    (diagnostic / "test_outputs.py").write_text(text.replace(NEEDLE,
        '    assert not expected_breach_months or "capacity" in normalize_text(issue_text) or "breach" in normalize_text(issue_text)'))
    shutil.copy2(TASK / "tests/verifier_utils.py", diagnostic / "verifier_utils.py")
    summary = {}
    for capacity in [980, 1000, 1100]:
        folder = results / str(capacity)
        folder.mkdir(exist_ok=True)
        input_path, output_path = folder / "input.xlsx", folder / "submission.xlsx"
        book = load_workbook(source_input)
        assert book["Assumptions"]["C1"].value == book["Assumptions"]["B5"].value == "Stress"
        book["Assumptions"]["C4"] = capacity
        book.save(input_path)
        expected = expected_from_input(input_path)
        dump(folder / "parameters_and_truth.json", {"intervention": {"Assumptions!C4": capacity}, "expected": expected})
        build_submission(input_path, output_path, expected)
        audit = independent_audit(input_path, output_path, expected, folder)
        metadata = dict(source_metadata, input_path=str(input_path), output_path=str(output_path))
        dump(folder / "metadata.json", metadata)
        original = evaluate(original_test, folder / "metadata.json", folder / "original_verifier")
        guarded = evaluate(diagnostic / "test_outputs.py", folder / "metadata.json", folder / "diagnostic_guard")
        assert original["passed"] == (capacity != 1100), original
        assert guarded["passed"], guarded
        summary[str(capacity)] = {"expected_months": audit["expected_months"], "independent_audit_passed": audit["passed"],
                                  "original_verifier": original, "diagnostic_guard": guarded}
        print(f"capacity={capacity}: correct months={audit['expected_months']}; independent audit PASS; "
              f"original={'PASS' if original['passed'] else 'FAIL'}; guarded copy={'PASS' if guarded['passed'] else 'FAIL'}", flush=True)
    # The diagnostic guard must still reject an extra exception at zero breaches.
    folder = results / "1100"
    # Also test the physically empty (headers-only) representation in addition
    # to formula-backed, visibly empty exception rows.
    empty = load_workbook(folder / "submission.xlsx")
    empty["Exception Review"].delete_rows(2, empty["Exception Review"].max_row)
    empty_path = folder / "headers_only_submission.xlsx"
    empty.save(empty_path)
    empty_metadata = dict(source_metadata, input_path=str(folder / "input.xlsx"), output_path=str(empty_path))
    dump(folder / "headers_only_metadata.json", empty_metadata)
    empty_result = evaluate(original_test, folder / "headers_only_metadata.json", folder / "headers_only_original")
    assert not empty_result["passed"]
    summary["headers_only_zero_breaches"] = empty_result
    book = load_workbook(folder / "submission.xlsx")
    book["Exception Review"].append(["Sep", "Capacity breach", 1057.3, 1100, -42.7])
    wrong = folder / "wrong_extra_exception.xlsx"
    book.save(wrong)
    metadata = dict(source_metadata, input_path=str(folder / "input.xlsx"), output_path=str(wrong))
    dump(folder / "negative_metadata.json", metadata)
    negative = evaluate(diagnostic / "test_outputs.py", folder / "negative_metadata.json", folder / "negative_control")
    assert not negative["passed"]
    summary["negative_control"] = negative
    after = source_records()
    assert before == after
    dump(results / "source_manifest.json", {"repository": "https://github.com/icip-cas/DocOps", "commit": PIN,
         "before": before, "after": after, "upstream_unchanged": True,
         "original_verifier": str(original_test), "diagnostic_copy": str(diagnostic / "test_outputs.py"),
         "diagnostic_change": "Guard the issue-keyword assertion when the computed breach set is empty; a causal probe, not a general verifier repair"})
    dump(results / "summary.json", summary)
    print("All probe assertions passed. Original upstream files unchanged.", flush=True)


if __name__ == "__main__":
    main()
