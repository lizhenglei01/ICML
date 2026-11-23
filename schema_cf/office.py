"""Domain-independent XLSX/DOCX rendering with bindings recorded on insertion."""
from __future__ import annotations

from pathlib import Path
import shutil

from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from medical_cf.office import patch, displayed
from medical_cf.oracle import generate, evaluate

from .engine import raw_id
from .input import token
from . import upstream_office

FILES = ("data.xlsx", "report.docx")


def label(fid, node, schemas):
    if node["rule"] == "source_cell":
        s = node["sources"][0]
        return f"Source {s['table']}, row key {token(s['primary_key']) if s['primary_key'] else s['row_index']}, field {s['column']}"
    view = node["query_plan"]
    route = view["source"] + "".join(f".{e['column']} -> {e['target']}" for e in view["path"])
    grouping = (view["path"][-1]["target"] + "." + view["path"][-1]["target_column"]) if view["path"] else view["source"] + "." + view["group_column"]
    predicate = f", filter {view['filter']['column']}={token(view['filter']['equals'])}" if view["filter"] else ""
    aggregate = "row count" if view["aggregate"] == "count" else "distinct " + view["distinct_column"] + " count"
    metric = {"count": aggregate, "rank": "descending competition rank", "leader": "is a maximum-count group",
              "present": "has at least one matching record", "leaders": "all maximum-count group values"}[node["rule"]]
    group = fid.split("/group/", 1)[1].rsplit("/", 1)[0] if "/group/" in fid else "all groups"
    return f"{route}{predicate}; aggregate {aggregate}; grouped by {grouping}; group {group}; {metric}"


def render(directory, rows, schemas, computed, mutable_fields):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    doc = Document()
    doc.add_heading("Schema-derived reference and aggregate review", 0)
    doc.add_paragraph("Counts refer to the explicit query plans below, not unstated domain-specific rules. Equal counts share competition rank. Zero-count groups are included.")
    bindings = []
    sheet_map = {}

    def bind_cell(ws, cell, fid):
        node = computed[fid]
        complex_value = isinstance(node["value"], list)
        binding = {"id": "xlsx:"+ws.title+":"+cell, "kind": "xlsx", "file": FILES[0], "sheet": ws.title,
                   "cell": cell, "fact_id": fid, "presentation": "template" if complex_value else "scalar", "template": "{value}"}
        # JSON list representation is kept in an explicit template, compatible with the shared evaluator.
        if complex_value:
            ws[cell] = str(node["value"])
        else:
            ws[cell] = node["value"]
        if node["value"] is None:
            ws[cell].number_format = "@"
        bindings.append(binding)

    for i, table in enumerate(sorted(rows), 1):
        ws = wb.create_sheet(f"T{i:03}")
        sheet_map[table] = ws.title
        columns = [c["name"] for c in schemas[table]["columns"]]
        ws.append(columns)
        for ri, row in enumerate(rows[table]):
            for ci, col in enumerate(columns, 1):
                bind_cell(ws, f"{get_column_letter(ci)}{ri+2}", raw_id(table, ri, col))
    metrics = wb.create_sheet("Metrics")
    metrics.append(["Query/field definition", "Value"])
    mutable = {(f["table"], f["column"]) for f in mutable_fields}
    for fid, node in computed.items():
        raw = node["rule"] == "source_cell"
        if raw:
            source = node["sources"][0]
            if (source["table"], source["column"]) not in mutable:
                continue
        definition = label(fid, node, schemas)
        if not raw:
            ri = metrics.max_row + 1
            metrics.cell(ri, 1, definition)
            bind_cell(metrics, f"B{ri}", fid)
        template = definition.replace("{", "{{").replace("}", "}}") + ": {value}."
        doc.add_paragraph(template.format(value=node["value"]))
        bindings.append({"id": f"docx:{len(doc.paragraphs)-1}", "kind": "docx", "file": FILES[1],
                         "paragraph": len(doc.paragraphs)-1, "fact_id": fid, "presentation": "template", "template": template})
    metrics["E1"] = "Fixed source row total"
    metrics["E2"] = "=" + "+".join(str(len(r)) for r in rows.values())
    for ws in wb:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = Font(bold=True)
    wb.save(directory / FILES[0])
    upstream_office.normalize_generated_workbook(directory / FILES[0])
    doc.save(directory / FILES[1])
    return {"bindings": bindings, "sheet_map": sheet_map}


def clone(source, target):
    Path(target).mkdir(parents=True, exist_ok=True)
    for f in FILES:
        shutil.copy2(Path(source) / f, Path(target) / f)


def reference(source, target, bindings, before, after):
    clone(source, target)
    changes = [(b, displayed(b, after, {})) for b in bindings if displayed(b, before, {}) != displayed(b, after, {})]
    old_values = {b["id"]: displayed(b, before, {}) for b, _ in changes}
    calls = upstream_office.edit(target, changes, old_values)
    diff = upstream_office.verify_diff(Path(source)/FILES[0], Path(target)/FILES[0], changes)
    return changes, {"calls": calls, "diff": diff}


def oracle(source, bindings, before, after):
    spec = generate(source, bindings, before, after, {}, FILES)
    spec["numeric_value_equality"] = True
    return spec


def independent_native(directory, rows, schemas, metadata, computed):
    """Read raw native rows and fact values; SQL computation was separately verified."""
    wb = load_workbook(Path(directory) / FILES[0], data_only=False)
    paragraphs = Document(Path(directory) / FILES[1]).paragraphs
    checks = []
    for table, records in rows.items():
        cols = [c["name"] for c in schemas[table]["columns"]]
        ws = wb[metadata["sheet_map"][table]]
        actual = [{c: ws.cell(i+2, j+1).value for j, c in enumerate(cols)} for i in range(len(records))]
        checks.append({"id": "native_raw:" + table, "pass": actual == records})
    for binding in metadata["bindings"]:
        value = computed[binding["fact_id"]]["value"]
        if binding["kind"] == "xlsx":
            actual = wb[binding["sheet"]][binding["cell"]].value
            expected = str(value) if isinstance(value, list) else value
        else:
            actual = paragraphs[binding["paragraph"]].text
            expected = binding["template"].format(value=value)
        checks.append({"id": "native_fact:" + binding["id"], "pass": actual == expected})
    if not all(c["pass"] for c in checks):
        raise AssertionError([c for c in checks if not c["pass"]])
    return {"pass": True, "checks": checks, "scope": "Raw tables vs source snapshots; native fact values vs independently SQL-verified computation; uses generated location metadata."}
