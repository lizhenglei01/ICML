"""Reference implementation for the effective-dated transfer task."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from shutil import copy2
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile
from lxml import etree as ET

from openpyxl import load_workbook


def _rewrite_opening_formula(formula: str, department_cell: str) -> str:
    employee_id = "DepartmentTransfers!$A$2"
    as_of_department = f'IF($H$4<DepartmentTransfers!$B$2,DepartmentTransfers!$C$2,DepartmentTransfers!$D$2)={department_cell}'
    active = (
        f'COUNTIFS(ActiveEmployees!$T$6:$T$69,"<"&$H$4,ActiveEmployees!$V$6:$V$69,{department_cell},'
        f'ActiveEmployees!$C$6:$C$69,"<>"&{employee_id})'
        f'+IF({as_of_department},COUNTIFS(ActiveEmployees!$T$6:$T$69,"<"&$H$4,'
        f'ActiveEmployees!$C$6:$C$69,{employee_id}),0)'
    )
    separated = (
        f'COUNTIFS(SeparatedEmployeeArchive!$M$5:$M$126,"<"&$H$4,'
        f'SeparatedEmployeeArchive!$Q$5:$Q$126,">="&$J$4,'
        f'SeparatedEmployeeArchive!$O$5:$O$126,{department_cell},'
        f'SeparatedEmployeeArchive!$C$5:$C$126,"<>"&{employee_id})'
        f'+IF({as_of_department},COUNTIFS(SeparatedEmployeeArchive!$M$5:$M$126,"<"&$H$4,'
        f'SeparatedEmployeeArchive!$Q$5:$Q$126,">="&$J$4,'
        f'SeparatedEmployeeArchive!$C$5:$C$126,{employee_id}),0)'
    )
    return f"={active}+{separated}"


def _month_end_formula(department_cell: str) -> str:
    """Count active and not-yet-separated employees by department as of the report end date."""
    employee_id = "DepartmentTransfers!$A$2"
    assignment = "IF($J$4<DepartmentTransfers!$B$2,DepartmentTransfers!$C$2,DepartmentTransfers!$D$2)=" + department_cell
    active = (
        f'COUNTIFS(ActiveEmployees!$T$6:$T$69,"<="&$J$4,ActiveEmployees!$V$6:$V$69,{department_cell},'
        f'ActiveEmployees!$C$6:$C$69,"<>"&{employee_id})'
        f'+IF({assignment},COUNTIFS(ActiveEmployees!$T$6:$T$69,"<="&$J$4,'
        f'ActiveEmployees!$C$6:$C$69,{employee_id}),0)'
    )
    separated = (
        f'COUNTIFS(SeparatedEmployeeArchive!$M$6:$M$126,"<="&$J$4,'
        f'SeparatedEmployeeArchive!$Q$6:$Q$126,">"&$J$4,'
        f'SeparatedEmployeeArchive!$O$6:$O$126,{department_cell},'
        f'SeparatedEmployeeArchive!$C$6:$C$126,"<>"&{employee_id})'
        f'+IF({assignment},COUNTIFS(SeparatedEmployeeArchive!$M$6:$M$126,"<="&$J$4,'
        f'SeparatedEmployeeArchive!$Q$6:$Q$126,">"&$J$4,'
        f'SeparatedEmployeeArchive!$C$6:$C$126,{employee_id}),0)'
    )
    return f"={active}+{separated}"


def _rename_native_chart_titles(path: Path) -> None:
    with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temporary:
        temp_path = Path(temporary.name)
    with ZipFile(path) as source, ZipFile(temp_path, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename in {f"xl/charts/chart{i}.xml" for i in (15, 25, 29, 33)}:
                root = ET.fromstring(content)
                for text in root.xpath('//*[local-name()="title"]//*[local-name()="t"]'):
                    if text.text == "Current Headcount by Department":
                        text.text = "Month-end Headcount by Department"
                content = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(item, content)
    temp_path.replace(path)


def apply(source: Path, destination: Path) -> None:
    copy2(source, destination)
    book = load_workbook(destination)
    employees = book["ActiveEmployees"]
    employee_row = next(row for row in range(6, 70) if employees.cell(row, 3).value == "G01")
    source_department = employees.cell(employee_row, 22).value
    transfers = book.create_sheet("DepartmentTransfers")
    transfers.append(["Employee ID", "Effective Date", "From Department", "To Department"])
    transfers.append(["G01", date(2020, 8, 15), source_department, "Sales Dept."])
    employees.cell(employee_row, 22).value = "Sales Dept."
    for sheet_name in ("MonthlyChangeQuery", "AnnualHeadcountHelper"):
        sheet = book[sheet_name]
        for col in range(3, 10):
            letter = chr(64 + col)
            cell = sheet.cell(8, col)
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.value = _rewrite_opening_formula(cell.value, f"{letter}$5")
        if sheet_name == "MonthlyChangeQuery":
            output_row = 11
        else:
            output_row = 9
        sheet.cell(output_row, 2).value = "Month-end Headcount"
        for col in range(3, 10):
            letter = chr(64 + col)
            sheet.cell(output_row, col).value = _month_end_formula(f"{letter}$5")
    book.calculation.fullCalcOnLoad = True
    book.calculation.forceFullCalc = True
    book.calculation.calcMode = "auto"
    book.save(destination)
    book.close()
    _rename_native_chart_titles(destination)


if __name__ == "__main__":
    root = Path(__file__).parent
    apply(root / "private" / "lite38_original.xlsx", root / "private" / "gold.xlsx")
