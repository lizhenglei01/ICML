"""Render once and record locations during creation; patch reference copies locally."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

from docx import Document
from lxml import etree as ET
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from .facts import raw_id

NS_S = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def layout(rows, schemas, facts, evidence):
    """Build reusable bindings, with paragraph indexes obtained at insertion time."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    doc = Document()
    doc.add_heading("Hospital primary-affiliation review", 0)
    doc.add_paragraph("Only primary appointments count. Ties share first place. Patient appointments are unchanged by a transfer.")
    bindings = []
    names = {r["DepartmentID"]: r["Name"] for r in rows["Department"]}
    physicians = {r["EmployeeID"]: r["Name"] for r in rows["Physician"]}

    def cell(ws, address, fid, template="{value}"):
        value = facts[fid]["value"]
        ws[address] = value if template == "{value}" and not isinstance(value, list) else template.format(value=value)
        if value is None:
            # Keep an explicit native cell for nullable fields, so its location can be checked.
            ws[address].number_format = "@"
        bindings.append({"id": f"xlsx:{ws.title}:{address}", "file": "hospital.xlsx", "kind": "xlsx",
                         "sheet": ws.title, "cell": address, "fact_id": fid,
                         "presentation": "scalar" if template == "{value}" else "template", "template": template})

    def paragraph(fid, template, value_map=None):
        value = facts[fid]["value"]
        val = value_map[str(value)] if value_map is not None else value
        doc.add_paragraph(template.format(value=val))
        bindings.append({"id": f"docx:{len(doc.paragraphs)-1}", "file": "review.docx", "kind": "docx",
                         "paragraph": len(doc.paragraphs)-1, "fact_id": fid,
                         "presentation": "template", "template": template, "value_map": value_map})

    # Raw cell locations are bound at insertion, including every protected input record.
    for table in sorted(rows):
        ws = workbook.create_sheet(table)
        columns = [c["name"] for c in schemas[table]["columns"]]
        ws.append(columns)
        for i, row in enumerate(rows[table]):
            for j, column in enumerate(columns, 1):
                cell(ws, f"{__import__('openpyxl').utils.get_column_letter(j)}{i+2}", raw_id(table, i, column))

    ws = workbook.create_sheet("Primary")
    ws.append(["Physician", "Name", "Primary department ID", "Primary department"])
    for i, pid in enumerate(sorted(physicians), 2):
        ws.cell(i, 1, pid)
        ws.cell(i, 2, physicians[pid])
        fid = f"physician:{pid}:primary"
        cell(ws, f"C{i}", fid)
        cell(ws, f"D{i}", fid)
        bindings[-1].update(presentation="lookup", value_map={str(k): v for k, v in names.items()})
        ws[f"D{i}"] = names[facts[fid]["value"]]

    ws = workbook.create_sheet("Summary")
    ws.append(["Department ID", "Department", "Primary physician count", "Rank", "Leader"])
    for i, d in enumerate(sorted(names), 2):
        ws.cell(i, 1, d)
        ws.cell(i, 2, names[d])
        cell(ws, f"C{i}", f"department:{d}:count")
        cell(ws, f"D{i}", f"department:{d}:rank")
        cell(ws, f"E{i}", f"department:{d}:leader", "{value}")
    # Preserved native formula; all derived values are materialized from deterministic facts.
    ws["G1"] = "Total primary physicians"
    ws["G2"] = f"=SUM(C2:C{len(names)+1})"
    ws["G4"] = "Counts include primary affiliations only."

    # Use upstream evidence serialization order for all derived Word statements.
    by_fragment = evidence["fragment_to_fact"]
    for block in evidence["queue"]["blocks"]:
        for item in block["items"]:
            fid = by_fragment[item["fragment_id"]]
            if fid.startswith("physician:"):
                pid = int(fid.split(":")[1])
                paragraph(fid, physicians[pid] + " has primary department {value}.", {str(k): v for k, v in names.items()})
            elif fid.startswith("department:"):
                _, ds, metric = fid.split(":")
                name = names[int(ds)]
                if metric == "count":
                    paragraph(fid, name + " has {value} primary physicians.")
                elif metric == "rank":
                    paragraph(fid, name + " is ranked {value} by primary physician count.")
                else:
                    paragraph(fid, name + " {value}.", {"True": "is among the departments with the most primary physicians",
                                                          "False": "is not among the departments with the most primary physicians"})
            elif fid == "departments:leaders":
                # The mapping covers every possible nonempty leader set, without enumerating subsets.
                paragraph(fid, "Departments with the most primary physicians: {value}.")
                bindings[-1]["presentation"] = "leader_sentence"
    doc.add_paragraph("Source: Doc2DB-Bench medical/case1 (Spider hospital_1). This review is a new controlled experimental asset.")
    for ws in workbook:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = Font(bold=True)
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 29
    return workbook, doc, bindings


def displayed(binding, facts, names):
    value = facts[binding["fact_id"]]["value"]
    kind = binding["presentation"]
    if kind == "scalar":
        return value
    if kind == "leader_sentence":
        listed = ", ".join(names[int(d)] for d in value)
        status = " (tied)" if len(value) > 1 else " (sole leader)"
        return "Departments with the most primary physicians: " + listed + status + "."
    if binding.get("value_map") is not None:
        value = binding["value_map"][str(value)]
    return value if kind == "lookup" else binding["template"].format(value=value)


def write_initial(directory, rows, schemas, facts, evidence):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    wb, doc, bindings = layout(rows, schemas, facts, evidence)
    names = {r["DepartmentID"]: r["Name"] for r in rows["Department"]}
    for b in bindings:
        if b["kind"] == "docx":
            doc.paragraphs[b["paragraph"]].text = str(displayed(b, facts, names))
    wb.save(directory / "hospital.xlsx")
    doc.save(directory / "review.docx")
    return bindings


def rewrite_zip(path, replacements):
    with ZipFile(path) as z:
        entries = [(i, z.read(i.filename)) for i in z.infolist()]
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        for info, content in entries:
            z.writestr(info, replacements.get(info.filename, content))


def sheet_parts(path):
    with ZipFile(path) as z:
        tree = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        result = {}
        for s in tree.find("s:sheets", NS_S):
            target = targets[s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
            result[s.get("name")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
        return result


def patch(directory, changes, filenames=("hospital.xlsx", "review.docx")):
    """Only edit registered value nodes; preserve formulas, styles and other ZIP entries."""
    directory = Path(directory)
    paths = sheet_parts(directory / filenames[0])
    for filename in filenames:
        selected = [(b, v) for b, v in changes if b["file"] == filename]
        if not selected:
            continue
        target = directory / filename
        with ZipFile(target) as z:
            originals = {i.filename: z.read(i.filename) for i in z.infolist()}
        trees = {}
        for b, value in selected:
            part = paths[b["sheet"]] if b["kind"] == "xlsx" else "word/document.xml"
            root = trees.setdefault(part, ET.fromstring(originals[part]))
            if b["kind"] == "xlsx":
                c = root.find(f".//s:c[@r='{b['cell']}']", NS_S)
                if c is None:
                    raise ValueError("registered cell disappeared")
                if c.find("s:f", NS_S) is not None:
                    raise ValueError("will not overwrite a formula")
                for child in list(c):
                    c.remove(child)
                ns = NS_S["s"]
                if isinstance(value, bool):
                    c.set("t", "b")
                    ET.SubElement(c, f"{{{ns}}}v").text = "1" if value else "0"
                elif isinstance(value, (int, float)):
                    c.set("t", "n")
                    ET.SubElement(c, f"{{{ns}}}v").text = str(value)
                elif value is None:
                    c.attrib.pop("t", None)
                else:
                    c.set("t", "inlineStr")
                    ET.SubElement(ET.SubElement(c, f"{{{ns}}}is"), f"{{{ns}}}t").text = str(value)
            else:
                paragraphs = root.findall("w:body/w:p", NS_W)
                p = paragraphs[b["paragraph"]]
                texts = p.findall(".//w:t", NS_W)
                if not texts:
                    raise ValueError("registered paragraph has no text")
                texts[0].text = str(value)
                for t in texts[1:]:
                    t.text = ""
        rewrite_zip(target, {p: ET.tostring(t) for p, t in trees.items()})


def make_reference(source, target, bindings, before, after, names):
    Path(target).mkdir(parents=True, exist_ok=True)
    for filename in ("hospital.xlsx", "review.docx"):
        shutil.copy2(Path(source) / filename, Path(target) / filename)
    changes = [(b, displayed(b, after, names)) for b in bindings
               if displayed(b, before, names) != displayed(b, after, names)]
    patch(target, changes)
    return changes
