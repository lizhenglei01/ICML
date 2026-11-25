"""Build the initial multi-file HR system with synthetic, non-identifying data."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from docx import Document
from docx.shared import Inches, Pt
from pptx import Presentation
from pptx.util import Inches as PptInches, Pt as PptPt


EMPLOYEES = [
    ("E001", "Avery", "People", "Analyst", date(2023, 2, 6), date(2027, 6, 30), "Bachelor", 62000, "Active"),
    ("E002", "Blake", "Finance", "Accountant", date(2022, 6, 13), date(2026, 12, 20), "Master", 71000, "Active"),
    ("E003", "Casey", "Operations", "Coordinator", date(2024, 1, 8), date(2027, 3, 31), "Bachelor", 59000, "Active"),
    ("E004", "Drew", "People", "Recruiter", date(2021, 9, 20), date(2026, 12, 5), "Master", 76000, "Active"),
    ("E005", "Emery", "Operations", "Manager", date(2020, 3, 2), date(2027, 12, 31), "Doctorate", 93000, "Active"),
    ("E006", "Finley", "Finance", "Analyst", date(2023, 11, 1), date(2027, 11, 30), "Bachelor", 68000, "Active"),
    ("E007", "Gray", "Technology", "Engineer", date(2022, 4, 4), date(2027, 4, 30), "Master", 84000, "Active"),
    ("E008", "Harper", "Technology", "Designer", date(2024, 8, 12), date(2026, 12, 31), "Bachelor", 72000, "Active"),
]
DEPARTMENTS = ["People", "Finance", "Operations", "Technology"]
MONTHS = [date(2026, month, 1) for month in range(1, 13)]


def build_workbook(path: Path) -> None:
    book = Workbook()
    readme = book.active
    readme.title = "StartHere"
    rows = [
        ["HR Operations Workspace", "Version", "1.0"],
        ["Business state", "HR_Operations.xlsx is the operational ledger"],
        ["Policy", "Use the effective date in HR_Policy.docx"],
        ["Testing", "Run baseline scenarios with oracle.py, not this workbook's own outputs"],
        ["Report", "Published PPT slides are immutable numbered snapshots"],
    ]
    for row in rows:
        readme.append(row)

    employees = book.create_sheet("Employees")
    employees.append(["EmployeeID", "DisplayName", "Department", "Role", "HireDate", "ContractEnd", "Education", "AnnualSalary", "Status", "ManagerID", "CostCenter"])
    for index, emp in enumerate(EMPLOYEES, 1):
        eid, name, dept, role, hired, contract, education, salary, status = emp
        employees.append([eid, name, dept, role, hired, contract, education, salary, status, "", dept])

    hiring = book.create_sheet("HiringRequests")
    hiring.append(["RequestID", "Department", "Role", "Headcount", "AnnualSalary", "Budget", "Submitted", "Status", "Reason", "IssueDate", "CandidateID"])
    hiring.append(["R001", "People", "Coordinator", 1, 60000, 70000, date(2026, 6, 2), "Draft", "", None, "E009"])
    hiring.append(["R002", "Finance", "Analyst", 1, 80000, 70000, date(2026, 6, 3), "Draft", "", None, "E010"])
    hiring.append(["R003", "Technology", "Engineer", 2, 75000, 180000, date(2026, 6, 4), "Draft", "", None, "E011"])

    departures = book.create_sheet("Departures")
    departures.append(["DepartureID", "EmployeeID", "DepartmentAtExit", "RequestDate", "LastDay", "Status", "Reason", "ArchivedAt"])
    departures.append(["D001", "E003", "Operations", date(2026, 6, 5), date(2026, 7, 31), "Draft", "", None])

    costs = book.create_sheet("MonthlyCosts")
    costs.append(["Month", "Department", "Salary", "Benefits", "Recruiting", "Training", "Other", "Total"])
    for month in MONTHS:
        for dept in DEPARTMENTS:
            salary = sum(emp[7] / 12 for emp in EMPLOYEES if emp[2] == dept and emp[8] == "Active")
            costs.append([month, dept, round(salary, 2), round(salary * 0.18, 2), 1200 if dept == "People" else 0, 500, 0, f"=SUM(C{costs.max_row + 1}:G{costs.max_row + 1})"])

    policy = book.create_sheet("Policy")
    policy.append(["PolicyID", "EffectiveFrom", "EffectiveTo", "Department", "HeadcountCap", "AnnualSalaryCap", "ContractNoticeDays"])
    for dept in DEPARTMENTS:
        policy.append(["P2026", date(2026, 1, 1), date(2026, 12, 31), dept, 4 if dept != "Technology" else 3, 80000, 45])

    events = book.create_sheet("Events")
    events.append(["EventID", "EventType", "EntityID", "EventDate", "FromState", "ToState", "Actor", "Details"])

    stats = book.create_sheet("MonthlyDashboard")
    stats.append(["Month", "Department", "OpeningHeadcount", "NewHires", "Departures", "ClosingHeadcount", "SalaryCost", "PeopleCost", "PendingRequests", "ContractsDue45Days"])
    for month in MONTHS:
        for dept in DEPARTMENTS:
            r = stats.max_row + 1
            stats.append([month, dept,
                          f'=COUNTIFS(Employees!$C:$C,B{r},Employees!$E:$E,"<"&A{r})-COUNTIFS(Departures!$C:$C,B{r},Departures!$E:$E,"<"&A{r},Departures!$F:$F,"Approved")',
                          f'=COUNTIFS(Employees!$C:$C,B{r},Employees!$E:$E,">="&A{r},Employees!$E:$E,"<"&EDATE(A{r},1))',
                          f'=COUNTIFS(Departures!$C:$C,B{r},Departures!$E:$E,">="&A{r},Departures!$E:$E,"<"&EDATE(A{r},1),Departures!$F:$F,"Approved")',
                          f'=C{r}+D{r}-E{r}',
                          f'=SUMIFS(MonthlyCosts!$C:$C,MonthlyCosts!$A:$A,A{r},MonthlyCosts!$B:$B,B{r})',
                          f'=SUMIFS(MonthlyCosts!$H:$H,MonthlyCosts!$A:$A,A{r},MonthlyCosts!$B:$B,B{r})',
                          f'=COUNTIFS(HiringRequests!$B:$B,B{r},HiringRequests!$H:$H,"Pending")',
                          f'=COUNTIFS(Employees!$C:$C,B{r},Employees!$F:$F,">="&A{r},Employees!$F:$F,"<="&A{r}+45,Employees!$I:$I,"Active")'])

    publish = book.create_sheet("PublishedSnapshots")
    publish.append(["SnapshotID", "Month", "PolicyID", "PublishedAt", "TotalHeadcount", "TotalPeopleCost", "PendingRequests", "PPTSlide"])

    for sheet in book:
        sheet.freeze_panes = "A2"
        if sheet.max_row:
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="244062")
        for column in sheet.columns:
            values = [len(str(cell.value)) for cell in list(column)[:100] if cell.value is not None]
            width = min(max(max(values, default=10) + 2, 12), 28)
            sheet.column_dimensions[get_column_letter(column[0].column)].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def build_policy(path: Path) -> None:
    doc = Document()
    doc.add_heading("Human Resources Operating Policy", 0)
    doc.add_paragraph("Policy P2026 · Effective 1 January 2026 through 31 December 2026")
    sections = [
        ("1. Hiring eligibility", "A request is eligible only when its department has remaining approved headcount and its annual salary does not exceed the request budget or the effective department salary cap. A checked request moves to Pending. Approval reserves one slot per requested position. Rejection reserves none."),
        ("2. Issuance and onboarding", "Only an Approved request may be Issued. Onboarding requires an issued request and a unique candidate ID. Onboarding adds an Active employee and closes the request. Repeating the same onboarding event must not create a duplicate employee."),
        ("3. Department and cost ownership", "Monthly operating summaries use the department recorded for the employee during the report month. Salary costs follow the employee's department and active period. Historical approved reports retain the values published at that time."),
        ("4. Departures and archive", "A departure enters the employee archive only after approval. The archive stores the employee's department at exit and the approved last day. Repeated approval must not create a second archive event."),
        ("5. Contract reminder", "An active employee is due for contract review when the contract end date is between the fixed report date and 45 calendar days after it, inclusive."),
        ("6. Reporting and record protection", "A monthly report must reconcile to the workbook's employee, cost, request, and departure states. Publishing creates a new numbered snapshot. Published snapshots and previously approved events are append-only."),
        ("7. Error handling", "An operation with an unknown employee or request, invalid status, duplicate event ID, or missing required date is rejected without changing workbook state."),
    ]
    for heading, body in sections:
        doc.add_heading(heading, level=1)
        doc.add_paragraph(body)
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            run.font.name = "Arial"
            run.font.size = Pt(10)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def build_deck(path: Path) -> None:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = "People Operations · June 2026"
    slide.placeholders[1].text = "Baseline snapshot · Workbook values are reconciled before publication"
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Baseline operating metrics"
    metrics = [
        ("Active employees", "8"), ("Departments", "4"),
        ("Open hiring requests", "3"), ("Approved departures", "0"),
        ("Monthly salary baseline", "computed from HR_Operations.xlsx"),
        ("Snapshot status", "Initial · immutable after publication"),
    ]
    for i, (label, value) in enumerate(metrics):
        box = slide.shapes.add_textbox(PptInches(0.8 + (i % 2) * 5.8), PptInches(1.5 + (i // 2) * 1.55), PptInches(5.1), PptInches(1.0))
        frame = box.text_frame
        frame.text = label
        frame.paragraphs[0].font.size = PptPt(18)
        p = frame.add_paragraph()
        p.text = value
        p.font.size = PptPt(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(path)


def build_workspace(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    build_workbook(directory / "HR_Operations.xlsx")
    build_policy(directory / "HR_Policy.docx")
    build_deck(directory / "HR_Operations.pptx")
