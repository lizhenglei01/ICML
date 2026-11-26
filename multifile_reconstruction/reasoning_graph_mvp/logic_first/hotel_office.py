"""Compile hotel evidence into native Office objects and verify minimal revisions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from ...build import digest, save
from ...evidence_control import table
from ...indirect import replace_span
from ...revision import snapshot

from docx import Document
from openpyxl import load_workbook
from openpyxl.utils import quote_sheetname
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from worldloom.models import ArtifactIR, ArtifactSection, Chart, ChartKind
from worldloom.presentation import READER
from worldloom.render import docx, pptx, xlsx


FILES = {"excel": "hotel_review.xlsx", "word": "hotel_policy.docx", "ppt": "hotel_briefing.pptx"}
FORMAT_NAMES = {name: kind for kind, name in FILES.items()}
BUSINESS_FILES = {"excel": "review.xlsx", "word": "policy.docx", "ppt": "briefing.pptx"}
FORMAT_NAMES.update({name: kind for kind, name in BUSINESS_FILES.items()})
TEXT_SEPARATOR = " — "
DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
ADMIN_ROWS = (
    ("录入复核表份数 / Entry review sheets", 5),
    ("归档复核表份数 / Archive review sheets", 7),
    ("审核表总份数 / Total audit sheets", 12),
)
STABLE_NOTE = "本次审查仅涉及已登记的季度客房记录。档案保留期为三年。 / This review covers the recorded quarterly room operations. Retain the archive for three years."
REVIEW_NOTE = (
    "使用已更正的来源记录与审查口径。修订前的报表及复核状态图记录上次发布的结果。复核覆盖全部列出的酒店。 / "
    "Use the corrected source records and review terms. Before revision, the issued report and qualification chart contain the prior results. Review all listed hotels."
)
CALC_SHEET = "复核计算"
CALC_CELL = "B2"
STAMP = "2026-01-01T00:00:00+00:00"


def serial(value):
    if isinstance(value, Fraction):
        return int(value) if value.denominator == 1 else float(value)
    return value


def display(value):
    value = serial(value)
    if isinstance(value, bool):
        return "通过 / Pass" if value else "复核 / Review"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def equal_value(actual, expected):
    if isinstance(expected, bool):
        return type(actual) is bool and actual == expected
    if isinstance(expected, (int, float, Fraction)):
        return type(actual) in {int, float, Fraction} and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
    return actual == expected


def decode(raw, kind):
    if kind == "number":
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ValueError("Numeric evidence is missing")
        text = str(raw).strip().replace(",", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        result = Fraction(text)
        return serial(result)
    if kind == "bool":
        if raw in (1, "1", display(True)):
            return True
        if raw in (0, "0", display(False)):
            return False
        raise ValueError("Unexpected decision text or chart value")
    if not isinstance(raw, str):
        raise ValueError("Text evidence is missing")
    return raw.strip()


def value_kind(value):
    if isinstance(value, bool):
        return "bool"
    return "number" if isinstance(value, (int, float, Fraction)) else "text"


def chart_groups(slots):
    groups = defaultdict(list)
    for slot in slots:
        if slot.get("chart"):
            groups[slot["chart"]].append(slot)
    return groups


def category(slot):
    return slot.get("category", slot["label"])


def validate_slots(slots, before, after):
    labels = set()
    for slot in slots:
        key = slot["node_id"]
        file = slot["file"]
        if file not in FILES or slot["role"] not in {"evidence", "policy", "output", "context"}:
            raise ValueError("Invalid evidence placement")
        label = slot["label"]
        if not isinstance(label, str) or not label.strip() or "\n" in label or (file, label) in labels:
            raise ValueError("Evidence labels must be distinct within each file")
        labels.add((file, label))
        if key not in before or key not in after or value_kind(before[key]) != value_kind(after[key]):
            raise ValueError(f"Missing or inconsistently typed revision values for {key}")
        if value_kind(before[key]) == "text" and any("\n" in value for value in (before[key], after[key])):
            raise ValueError("Surface values are single paragraphs")
        if slot.get("chart") and (file != "ppt" or value_kind(before[key]) == "text"):
            raise ValueError("Only numeric or Boolean evidence supports native chart points")
    for group in chart_groups(slots).values():
        if len({category(slot) for slot in group}) != len(group):
            raise ValueError("Each native chart requires distinct category names")


def office_ir(title, identifier, sections, *, generic=False):
    return ArtifactIR(
        id=identifier, intent_id="business-period-review" if generic else "hotel-quarterly-review",
        title=title, sections=sections,
        metadata={"worldloom_created": STAMP, "note": "Synthetic business review / 合成业务复核" if generic
                  else "Synthetic hotel operations / 合成酒店经营记录"},
    )


def render_original(directory, slots, values, *, ppt_size='small', files=FILES, title=None):
    """Use the existing Worldloom renderers for the native editable templates."""
    directory.mkdir(parents=True, exist_ok=True)
    active = {kind: [slot for slot in slots if slot["file"] == kind] for kind in files}
    review_note = ("依据更正后数据及复核口径更新上一版已发布结果。覆盖本期所列全部对象。" if title else REVIEW_NOTE)
    retention_note = ("留存记录仅用于档案核对。" if title else STABLE_NOTE)

    def rows(selected, native=False):
        return [(slot["label"], serial(values[slot["node_id"]]) if native and value_kind(values[slot["node_id"]]) == "number"
                 else display(values[slot["node_id"]])) for slot in selected]

    workbook_sections = []
    for role, heading in [("evidence", "已更正记录 / Corrected records"), ("policy", "审查口径 / Review terms"),
                          ("output", "复核报表 / Issued review"), ("context", "备查记录 / General records")]:
        entries = rows([slot for slot in active["excel"] if slot["role"] == role], native=True)
        if entries:
            workbook_sections.append(ArtifactSection(heading=heading, table=table(role, heading, entries)))
    workbook_sections.append(ArtifactSection(heading="档案核对 / Archive check",
        table=table("archive", "档案核对 / Archive check", ADMIN_ROWS).model_copy(update={"note": review_note})))
    workbook = office_ir(f"{title}｜复核工作簿" if title else "客房季度核验底稿 / Quarterly hotel workbook",
                         "business-review" if title else "hotel-review", workbook_sections, generic=bool(title))
    workbook_path = directory / files["excel"]
    workbook_path.write_bytes(xlsx.render(workbook))
    native = load_workbook(workbook_path)
    for sheet in native:
        archive = {cell.value: cell.row for cell in sheet["A"] if cell.value in {row[0] for row in ADMIN_ROWS}}
        if len(archive) == len(ADMIN_ROWS):
            first, last, total = [archive[label] for label, _ in ADMIN_ROWS]
            sheet[f"B{total}"] = f"=SUM(B{first}:B{last})"
            break
    else:
        raise ValueError("Missing native archive formula location")
    native.save(workbook_path)

    word_sections = [ArtifactSection(heading="复核范围 / Review scope", body=review_note)]
    for role, heading in [("policy", "经营审查口径 / Operating review terms"), ("evidence", "已更正记录 / Corrected records"),
                          ("output", "复核结论 / Issued conclusions"), ("context", "备查记录 / General records")]:
        lines = [label + TEXT_SEPARATOR + str(value) for label, value in rows(
            [slot for slot in active["word"] if slot["role"] == role])]
        if lines:
            word_sections.append(ArtifactSection(heading=heading, body="\n\n".join(lines)))
    word_sections.append(ArtifactSection(heading="档案管理 / Retention", body=retention_note))
    note = office_ir(f"{title}｜政策和结论" if title else "客房经营审查说明 / Hotel operations review note",
                     "business-policy" if title else "hotel-policy", word_sections, generic=bool(title))
    (directory / files["word"]).write_bytes(docx.render(note, presentation=READER))

    presentation_sections = [ArtifactSection(heading="复核范围 / Review scope", body=review_note)]
    entries = rows([slot for slot in active["ppt"] if not slot.get("chart")])
    if entries:
        presentation_sections.append(ArtifactSection(heading="审核摘要 / Review summary",
            table=table("review", "审核摘要 / Review summary", entries)))
    for index, (title, group) in enumerate(chart_groups(active["ppt"]).items()):
        key = "evidence_chart_" + str(index)
        source_rows = [(category(slot), int(values[slot["node_id"]]) if isinstance(values[slot["node_id"]], bool)
                       else serial(values[slot["node_id"]])) for slot in group]
        column = group[0].get("series", "季度记录 / Quarter record")
        source = table(key, title, source_rows, column)
        chart_title = chart_heading(title, group[0]["role"])
        presentation_sections.append(ArtifactSection(heading=chart_title, table=source,
            charts=[Chart(key=key, title=chart_title, kind=ChartKind.BAR, table=key, series=["value"], value_axis=column)]))
    if not chart_groups(active["ppt"]):
        source = table("archive_chart", "档案复核工作量 / Archive review workload", ADMIN_ROWS[:2])
        presentation_sections.append(ArtifactSection(heading="独立档案核验 / Archive review", table=source,
            charts=[Chart(key="archive_chart", title="档案复核工作量 / Archive review workload", kind=ChartKind.BAR,
                          table="archive_chart", series=["value"], value_axis="审核表份数 / Review sheets")]))
    presentation_sections.append(ArtifactSection(heading="档案管理 / Retention", body=retention_note))
    presentation = office_ir(f"{title}｜复核简报" if title else "季度客房经营复核简报 / Quarterly hotel review briefing",
                             "business-briefing" if title else "hotel-briefing", presentation_sections, generic=bool(title))
    (directory / files["ppt"]).write_bytes(pptx.render(presentation, profile=READER,
        artifact_type="business_review" if title else "hotel_review", size_class=ppt_size))


def chart_heading(title, role):
    prefix = "复核报表 / Issued review" if role == "output" else "已更正来源 / Corrected source"
    return prefix + " · " + title


def native_locations(directory, slots, values, *, files=FILES):
    """Bind visible labels to addresses in the actual rendered native files."""
    workbook = load_workbook(directory / files["excel"], data_only=False)
    document = Document(directory / files["word"])
    deck = Presentation(directory / files["ppt"])
    locators = defaultdict(list)
    for sheet in workbook:
        for row in sheet.iter_rows(min_col=1, max_col=2):
            label, cell = row
            if isinstance(label.value, str):
                locators["excel", label.value].append({"kind": "cell", "sheet": sheet.title, "cell": cell.coordinate})
    for index, paragraph in enumerate(document.paragraphs):
        if TEXT_SEPARATOR in paragraph.text:
            label, _, _ = paragraph.text.partition(TEXT_SEPARATOR)
            locators["word", label].append({"kind": "paragraph", "paragraph": index})
    for si, slide in enumerate(deck.slides):
        headings = {shape.text for shape in slide.shapes if shape.has_text_frame}
        for sh, shape in enumerate(slide.shapes):
            if shape.has_table and len(shape.table.columns) == 2:
                title = shape.table.cell(0, 0).text
                for row, cells in enumerate(shape.table.rows):
                    location = {"kind": "table", "slide": si, "shape": sh, "row": row, "column": 1}
                    locators["ppt", cells.cells[0].text, title].append(location)
            if shape.has_chart:
                chart = shape.chart
                embedding = load_workbook(BytesIO(chart.part.chart_workbook.xlsx_part.blob), data_only=False).active
                for i, entry in enumerate(chart.plots[0].categories):
                    for heading in headings:
                        key = "chart", entry.label, heading
                        locators[key].append({"kind": "chart_cache", "slide": si, "shape": sh, "series": 0, "point": i})
                        locators[key].append({"kind": "chart_workbook", "slide": si, "shape": sh,
                            "sheet": embedding.title, "cell": f"B{i + 2}",
                            "part": str(chart.part.chart_workbook.xlsx_part.partname)})
    bindings = []
    for slot in slots:
        file = slot["file"]
        if file == "ppt":
            title = slot.get("chart") or "审核摘要 / Review summary"
            locations = locators["ppt", category(slot), title.replace(" / ", " · ")]
            if slot.get("chart"):
                locations = locations + locators["chart", category(slot), chart_heading(title, slot["role"])]
        else:
            locations = locators[file, slot["label"]]
        wanted = {"excel": {"cell"}, "word": {"paragraph"}, "ppt": {"table", "chart_cache", "chart_workbook"} if slot.get("chart") else {"table"}}[file]
        if len(locations) != len(wanted) or {loc["kind"] for loc in locations} != wanted:
            raise ValueError(f"Evidence {slot['label']!r} in {file} rendered at unexpected locations {locations}")
        for location in locations:
            evidence_id = f"evidence:{file}:{slot['node_id']}:{location['kind']}"
            bindings.append({**slot, "file": files[file], "evidence_id": evidence_id, "location": location,
                             "value_type": value_kind(values[slot["node_id"]])})
    if len({b["evidence_id"] for b in bindings}) != len(bindings):
        raise ValueError("Repeated node in the same evidence channel")
    return bindings


def read_observations(directory, bindings, files=None, *, excel_data_only=False, computed_cells=None):
    """Read source data and results, checking that each identifying label still agrees."""
    directory = Path(directory)
    computed_cells = computed_cells or {}
    selected = {binding["file"] for binding in bindings} if files is None else set(files)
    bindings = [binding for binding in bindings if binding["file"] in selected]
    readers = {"excel": lambda path: load_workbook(path, data_only=excel_data_only), "word": Document, "ppt": Presentation}
    docs = {file: readers[FORMAT_NAMES[file]](directory / file) for file in {binding["file"] for binding in bindings}}
    embeddings = {}
    result = []
    for binding in bindings:
        file = binding["file"]
        doc = docs[file]
        location = binding["location"]
        kind = location["kind"]
        if kind == "cell":
            sheet = doc[location["sheet"]]
            cell = sheet[location["cell"]]
            label, raw = sheet.cell(cell.row, cell.column - 1).value, cell.value
            if binding['evidence_id'] in computed_cells:
                raw = computed_cells[binding['evidence_id']]['value']
        elif kind == "paragraph":
            label, separator, raw = doc.paragraphs[location["paragraph"]].text.partition(TEXT_SEPARATOR)
            if not separator:
                raise ValueError("Unrecognized Word paragraph rendering")
        else:
            shape = doc.slides[location["slide"]].shapes[location["shape"]]
            if kind == "table":
                row = shape.table.rows[location["row"]]
                col = location["column"]
                label, raw = row.cells[col - 1].text, row.cells[col].text
            elif kind == "chart_cache":
                chart = shape.chart
                point = location["point"]
                label, raw = chart.plots[0].categories[point].label, chart.series[location["series"]].values[point]
            else:
                cache_key = location["slide"], location["shape"]
                if cache_key not in embeddings:
                    embeddings[cache_key] = load_workbook(BytesIO(shape.chart.part.chart_workbook.xlsx_part.blob), data_only=False)
                sheet = embeddings[cache_key][location["sheet"]]
                cell = sheet[location["cell"]]
                label, raw = sheet.cell(cell.row, cell.column - 1).value, cell.value
        expected_label = category(binding) if FORMAT_NAMES[file] == "ppt" else binding["label"]
        if label != expected_label:
            raise ValueError(f"Evidence label changed at {binding['evidence_id']}")
        result.append({**binding, "value": decode(raw, binding["value_type"])})
    return result


def workbook_count_formula(directory, bindings, state):
    """Give Excel an editable calculation backed solely by its issued flags."""
    flags = [binding for binding in bindings if binding["file"] == FILES["excel"]
             and binding.get("semantic_node_id") == "qualified" and binding["role"] == "output"
             and binding["value_type"] == "bool"]
    if not flags:
        return None
    path = directory / FILES["excel"]
    workbook = load_workbook(path)
    sheet = workbook.create_sheet(CALC_SHEET)
    sheet.column_dimensions["A"].width = 42
    sheet.column_dimensions["B"].width = 22
    sheet["A1"] = "复核报表 / Issued review"
    sheet["A2"] = "通过酒店数 / Qualified hotel count"
    references = [f"{quote_sheetname(binding['location']['sheet'])}!{binding['location']['cell']}" for binding in flags]
    formula = "=" + "+".join(f'COUNTIF({ref},"{display(True)}")' for ref in references)
    sheet[CALC_CELL] = formula
    workbook.save(path)
    return {"file": FILES["excel"], "sheet": CALC_SHEET, "cell": CALC_CELL, "formula": formula,
            "node_id": "qualified_count", "evidence_id": "evidence:excel:qualified_count:formula",
            "location": {"kind": "formula", "sheet": CALC_SHEET, "cell": CALC_CELL},
            "inputs": [binding["evidence_id"] for binding in flags],
            "before": sum(state[binding["node_id"]] for binding in flags),
            "role": "output_formula", "semantic_node_id": "qualified_count",
            "note": "Recalculates from visible issued-review flags. The formula structure is preserved during revision."}


def populate_count_cache(directory, count):
    """Set a native initial cache without replacing the formula or recalculation settings."""
    from lxml import etree

    path = directory / FILES["excel"]
    workbook = load_workbook(path, read_only=True)
    worksheet_index = workbook.sheetnames.index(CALC_SHEET) + 1
    workbook.close()
    with ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    part = f"xl/worksheets/sheet{worksheet_index}.xml"
    root = etree.fromstring(parts[part])
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell = next(cell for cell in root.iter(namespace + "c") if cell.get("r") == CALC_CELL)
    cache = cell.find(namespace + "v")
    if cache is None:
        cache = etree.SubElement(cell, namespace + "v")
    cache.text = str(count)
    parts[part] = etree.tostring(root)
    with ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def set_presentation_value(cell, binding, value):
    text = str(int(value)) if binding.get("chart") and isinstance(value, bool) else display(value)
    paragraph = cell.text_frame.paragraphs[0]
    replace_span(paragraph, 0, len(paragraph.text), text)


def preserve_display_precision(directory, bindings, values, *, files=FILES):
    """Keep native chart tables faithful when the general renderer rounds floats."""
    path = directory / files["ppt"]
    reports = [binding for binding in bindings if binding["location"]["kind"] == "table"
               and binding["value_type"] == "number"]
    if not reports:
        return
    deck = Presentation(path)
    changed = False
    for binding in reports:
        loc = binding["location"]
        cell = deck.slides[loc["slide"]].shapes[loc["shape"]].table.cell(loc["row"], loc["column"])
        expected = values[binding["node_id"]]
        if not equal_value(decode(cell.text, "number"), expected):
            set_presentation_value(cell, binding, expected)
            changed = True
    if changed:
        deck.save(path)


def patch_reference(original, reference, bindings, before, after, *, files=FILES):
    """Copy the package and update only changed native objects."""
    reference.mkdir(parents=True, exist_ok=True)
    for file in files.values():
        shutil.copy2(original / file, reference / file)
    changed = defaultdict(list)
    for binding in bindings:
        key = binding["node_id"]
        if before[key] != after[key]:
            changed[binding["file"]].append(binding)
    if changed[files["excel"]]:
        path = reference / files["excel"]
        workbook = load_workbook(path)
        for binding in changed[files["excel"]]:
            location = binding["location"]
            value = after[binding["node_id"]]
            workbook[location["sheet"]][location["cell"]] = serial(value) if binding["value_type"] == "number" else display(value)
        workbook.save(path)
    if changed[files["word"]]:
        path = reference / files["word"]
        document = Document(path)
        for binding in changed[files["word"]]:
            paragraph = document.paragraphs[binding["location"]["paragraph"]]
            start = len(binding["label"]) + len(TEXT_SEPARATOR)
            replace_span(paragraph, start, len(paragraph.text), display(after[binding["node_id"]]))
        document.save(path)
    if changed[files["ppt"]]:
        path = reference / files["ppt"]
        deck = Presentation(path)
        charts = defaultdict(dict)
        for binding in changed[files["ppt"]]:
            location = binding["location"]
            si, sh = location["slide"], location["shape"]
            value = after[binding["node_id"]]
            if location["kind"] == "table":
                cell = deck.slides[si].shapes[sh].table.cell(location["row"], location["column"])
                set_presentation_value(cell, binding, value)
            elif location["kind"] == "chart_cache":
                charts[si, sh][location["point"]] = int(value) if isinstance(value, bool) else serial(value)
        for (si, sh), overrides in charts.items():
            chart = deck.slides[si].shapes[sh].chart
            codes = chart._chartSpace.xpath(".//c:val/c:numRef/c:numCache/c:formatCode")
            data = CategoryChartData(number_format=codes[0].text if codes else "General")
            data.categories = [category.label for category in chart.plots[0].categories]
            series = chart.series[0]
            data.add_series(series.name, [overrides.get(i, value) for i, value in enumerate(series.values)])
            chart.replace_data(data)
        deck.save(path)


def structural_key(directory, binding):
    location = binding["location"]
    file = binding["file"]
    if location["kind"] == "cell":
        sheet = load_workbook(directory / file, data_only=False)[location["sheet"]]
        cell = sheet[location["cell"]]
        logical_rows = [row[0].row for row in sheet.iter_rows() if any(c.value is not None for c in row)]
        return [file + f":sheet:{sheet.title}:logical_row:{logical_rows.index(cell.row)}:column:{cell.column}"]
    if location["kind"] == "paragraph":
        return [file + f":paragraph:{location['paragraph']}"]
    prefix = file + f":slide:{location['slide']}:shape:{location['shape']}:"
    suffixes = {"table": ["table"], "chart_cache": ["chart", "chart_definition"], "chart_workbook": ["embedded_data"]}
    return [prefix + suffix for suffix in suffixes[location["kind"]]]


def with_unordered_qualification_list(oracle):
    """Use membership equality for unordered business rosters."""
    result = deepcopy(oracle)
    result["version"] = 2
    result["unordered_lists"] = {}
    for binding in result["bindings"]:
        if (binding["file"] == FILES["word"] and binding["location"]["kind"] == "paragraph"
                and binding["role"] == "output"
                and binding.get("history_id") in {"prior_qualified_names", "prior_peer_cohort"}):
            paragraph = binding["location"]["paragraph"]
            result["unordered_lists"][binding["evidence_id"]] = binding["file"] + f":paragraph:{paragraph}"
    return result


def same_names(actual, expected):
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    return Counter(actual.split(", ")) == Counter(expected.split(", "))


def same_named_paragraph(actual, expected):
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    if {k: v for k, v in actual.items() if k != "text"} != {k: v for k, v in expected.items() if k != "text"}:
        return False
    left_label, left_sep, left_value = actual["text"].partition(TEXT_SEPARATOR)
    right_label, right_sep, right_value = expected["text"].partition(TEXT_SEPARATOR)
    return bool(left_sep and right_sep and left_label == right_label and same_names(left_value, right_value))


def same_numeric_paragraph(actual, expected):
    """Compare a numeric output while preserving its label and other attributes."""
    if not isinstance(actual, dict) or not isinstance(expected, dict) or actual.keys() != expected.keys():
        return False
    if any(actual[key] != expected[key] for key in expected if key != "text"):
        return False
    if not isinstance(actual.get("text"), str) or not isinstance(expected.get("text"), str):
        return False
    left_label, left_sep, left = actual["text"].rpartition(TEXT_SEPARATOR)
    right_label, right_sep, right = expected["text"].rpartition(TEXT_SEPARATOR)
    if not (left_sep and right_sep and left_label == right_label
            and DECIMAL_TEXT.fullmatch(left) and DECIMAL_TEXT.fullmatch(right)):
        return False
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return False


def active_oracle_path(private):
    """Resolve an explicit rescore sidecar only for the exact frozen original."""
    private = Path(private)
    original = private / "oracle.json"
    revised = private / "oracle_v2.json"
    if revised.exists() and json.loads(revised.read_text(encoding="utf-8")).get("based_on_sha256") == digest(original):
        return revised
    return original


def _formula_function_checks(directory, spec):
    from .hotel_milestone_formulas import verify_formula_function

    def points(entries):
        return {(row['sheet'], row['cell']): row['value'] if row.get('value_type') == 'text' else Fraction(str(row['value']))
                for row in entries}

    scenarios = [{'name': case['name'], 'inputs': points(case['inputs']), 'expected': points(case['expected'])}
                 for case in spec['scenarios']]
    result = verify_formula_function(
        Path(directory) / spec['file'], scenarios, allow_text_inputs=spec.get('allow_text_inputs', False),
        require_sensitivity=spec.get('require_sensitivity', True), timeout=spec.get('timeout_seconds', 30),
    )
    if result['status'] != 'checked':
        return [{'object': 'functional_excel:calculator', 'group': 'structure', 'pass': False,
                 'reason': result.get('error', result['status'])}]
    checks = []
    for scenario in result['scenarios']:
        mutations = scenario['mutations']
        for row in scenario['checks']:
            address = f"{row['sheet']}!{row['cell']}"
            checks.append({**row, 'object': f"functional_excel:{scenario['name']}:{address}",
                           'group': 'semantic', 'pass': row['pass'] and all(m['pass'] for m in mutations)})
    return checks


def grade(directory, oracle, *, equivalent_numeric_outputs=True):
    """Grade native structure, expected values and all protected objects."""
    checks = []
    functional = oracle.get('functional_excel')
    try:
        expected_files = tuple(oracle["files"])
        observed = snapshot(directory, expected_files)
        publication = oracle.get('excel_publication')
        computed_cells, calculated_count = {}, None
        approved_representation_changes = set()
        if publication:
            from .hotel_publication import publication_readback, workbook_signature

            workbook_path = Path(directory) / publication['file']
            computed_cells, calculated_count = publication_readback(workbook_path, publication['cells'], oracle.get('count_formula'))
            for binding in publication['cells']:
                if binding['evidence_id'] in computed_cells and binding['object'] in observed:
                    observed[binding['object']] = {**observed[binding['object']], **computed_cells[binding['evidence_id']]}
                    approved_representation_changes.add(binding['object'])
            workbook_object = publication['file'] + ':protected_part:xl/workbook.xml'
            if (workbook_object in observed and
                    observed[workbook_object] != publication['reference_workbook_part_hash'] and
                    workbook_signature(workbook_path) == publication['default_calc_workbook_signature']):
                approved_representation_changes.add(workbook_object)
                observed[workbook_object] = publication['reference_workbook_part_hash']
            for key, allowed in publication.get('alternate_labels', {}).items():
                if key in observed and observed[key]['value'] in allowed['alternatives']:
                    observed[key] = {**observed[key], 'value': allowed['expected']}
                    approved_representation_changes.add(key)
        unordered = oracle.get("unordered_lists", {})
        formula_objects = functional['objects'] if functional else {}
        editable_areas = tuple(oracle.get('allow_helpers_in', ()))
        expected_objects = {item["object"] for item in oracle["checks"]}
        numeric_paragraphs = {
            binding["file"] + f":paragraph:{binding['location']['paragraph']}"
            for binding in oracle["bindings"]
            if equivalent_numeric_outputs and binding["role"] == "output"
            and binding["location"]["kind"] == "paragraph" and binding["value_type"] == "number"
        }
        for item in oracle["checks"]:
            key = item["object"]
            if key in formula_objects and key in observed:
                actual = observed[key]
                expected = item['expected']
                passed = actual['type'] == 'f' and (functional.get('flexible_formula_cells') or
                    {k: v for k, v in actual.items() if k != 'value'} == {k: v for k, v in expected.items() if k != 'value'})
            elif key in unordered.values():
                passed = same_named_paragraph(observed.get(key), item["expected"])
            else:
                passed = key in observed and observed[key] == item["expected"]
                if not passed and item["group"] == "required" and key in numeric_paragraphs:
                    passed = same_numeric_paragraph(observed.get(key), item["expected"])
                    if passed:
                        approved_representation_changes.add(key)
            check = {**item, "pass": passed}
            if key in approved_representation_changes:
                check['comparison'] = ('equivalent_numeric_paragraph' if key in numeric_paragraphs
                                       else 'publication_value_or_equivalent_metadata')
            if not passed:
                check["observed"] = observed.get(key)
            checks.append(check)
        for key in sorted(set(observed) - expected_objects):
            if not key.startswith(editable_areas):
                checks.append({"object": key, "group": "protected", "pass": False})
        by_id = {item["evidence_id"]: item for item in read_observations(
            directory, oracle["bindings"], computed_cells=computed_cells)}
        for item in oracle["evidence_checks"]:
            actual = by_id[item["evidence_id"]]["value"]
            compare = same_names if item["evidence_id"] in unordered else equal_value
            checks.append({"object": item["evidence_id"], "group": "semantic", "expected": item["after"],
                           "observed": actual, "pass": compare(actual, item["after"])})
        formula = oracle.get("count_formula")
        if formula:
            computed = sum(by_id[key]["value"] for key in formula["inputs"])
            if computed_cells:
                cached = calculated_count
            else:
                workbook = load_workbook(Path(directory) / formula["file"], data_only=True, read_only=True)
                try:
                    cached = workbook[formula["sheet"]][formula["cell"]].value
                finally:
                    workbook.close()
            word_counts = [record["value"] for record in by_id.values()
                           if record["file"] == FILES["word"] and record.get("semantic_node_id") == "qualified_count"
                           and record["role"] == "output"]
            checks.append({"object": "excel_native_count_formula_consistency", "group": "semantic",
                           "expected": formula["after"], "observed": computed, "cached": cached,
                           "other_file_counts": word_counts,
                           "pass": computed == formula["after"] and (cached == computed or (cached is None and not computed_cells))
                           and all(value == computed for value in word_counts)})
    except (ValueError, TypeError, OSError, KeyError, IndexError, AttributeError) as exc:
        checks.append({"object": "native_parse", "group": "structure", "pass": False, "error": str(exc)})
    except Exception as exc:
        # Native parser exceptions are diverse, including ZIP or XML failures.
        checks.append({"object": "native_parse", "group": "structure", "pass": False, "error": type(exc).__name__ + ": " + str(exc)})
    if functional:
        try:
            checks.extend(_formula_function_checks(directory, functional))
        except (ValueError, TypeError, OSError, KeyError) as exc:
            checks.append({'object': 'functional_excel:calculator', 'group': 'structure', 'pass': False,
                           'error': f'{type(exc).__name__}: {exc}'})
    groups = {}
    for group in ("required", "protected", "semantic", "structure"):
        members = [check for check in checks if check["group"] == group]
        groups[group] = {"passed": sum(check["pass"] for check in members), "total": len(members)}
    return {"pass": bool(checks) and all(check["pass"] for check in checks), "groups": groups, "checks": checks}


def native_controls(package, oracle):
    """Check no-op, partial, unrelated-formula and chart-cache-only submissions."""
    package = Path(package)
    public, private = package / "public", package / "private"
    reference = private / "reference"
    files = {FORMAT_NAMES[name]: name for name in oracle["files"]}
    results = {}

    def record(case, directory):
        report = grade(directory, oracle)
        expected = case == "reference"
        results[case] = {"pass": report["pass"], "expected": expected, "groups": report["groups"],
                         "failed_objects": [check["object"] for check in report["checks"] if not check["pass"]]}
        if report["pass"] != expected:
            raise ValueError(f"Oracle returned an unexpected grade for {case}")

    record("reference", reference)
    record("unchanged_original", public)
    for case in ("unchanged_word", "wrong_corrected_value", "protected_formula_replaced", "chart_cache_corrupted"):
        with TemporaryDirectory(prefix="hotel_oracle_") as tmp:
            probe = Path(tmp)
            for file in oracle["files"]:
                shutil.copy2(reference / file, probe / file)
            if case == "unchanged_word":
                if not any(check["group"] == "required" and check["object"].startswith(files["word"] + ":")
                           for check in oracle["checks"]):
                    continue
                shutil.copy2(public / files["word"], probe / files["word"])
            elif case == "wrong_corrected_value":
                updated = {item["evidence_id"] for item in oracle["evidence_checks"] if item["group"] == "required"}
                binding = next((item for item in oracle["bindings"] if item["evidence_id"] in updated
                                and item["file"] == files["excel"] and item["value_type"] == "number"), None)
                if binding is None:
                    continue
                path = probe / files["excel"]
                workbook = load_workbook(path)
                location = binding["location"]
                workbook[location["sheet"]][location["cell"]].value += 1
                workbook.save(path)
            elif case == "protected_formula_replaced":
                path = probe / files["excel"]
                workbook = load_workbook(path)
                count_formula = oracle.get("count_formula")
                if count_formula:
                    workbook[count_formula["sheet"]][count_formula["cell"]] = count_formula["after"]
                else:
                    formula = next(cell for sheet in workbook for row in sheet for cell in row if cell.data_type == "f")
                    formula.value = 12
                workbook.save(path)
            else:
                from lxml import etree

                path = probe / files["ppt"]
                with ZipFile(path) as archive:
                    parts = {name: archive.read(name) for name in archive.namelist()}
                chart_part = next(name for name in parts if name.startswith("ppt/charts/chart") and name.endswith(".xml"))
                root = etree.fromstring(parts[chart_part])
                namespace = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
                number = root.xpath("//c:val/c:numRef/c:numCache/c:pt/c:v", namespaces=namespace)[0]
                number.text = str(float(number.text) + 1)
                parts[chart_part] = etree.tostring(root)
                with ZipFile(path, "w") as archive:
                    for name, data in parts.items():
                        archive.writestr(name, data)
            record(case, probe)
    save(private / "native_controls.json", results)
    return results


def build_layout(package, slots, before, after, instruction, *, ppt_size='small', title=None):
    """Bind evidence, compile the expected edits and independently read assets back."""
    package = Path(package)
    public, private = package / "public", package / "private"
    reference = private / "reference"
    files = BUSINESS_FILES if title else FILES
    validate_slots(slots, before, after)
    render_original(public, slots, before, ppt_size=ppt_size, files=files, title=title)
    (public / "instruction.md").write_text(instruction, encoding="utf-8")
    if set(path.name for path in public.iterdir()) != {*files.values(), "instruction.md"}:
        raise ValueError("Public package contains files outside the declared artifact list")
    bindings = native_locations(public, slots, before, files=files)
    preserve_display_precision(public, bindings, before, files=files)
    count = None if title else workbook_count_formula(public, bindings, before)
    if count:
        flag_ids = {binding["evidence_id"]: binding["node_id"] for binding in bindings}
        count["after"] = sum(after[flag_ids[key]] for key in count["inputs"])
        populate_count_cache(public, count["before"])
    patch_reference(public, reference, bindings, before, after, files=files)
    if count:
        populate_count_cache(reference, count["after"])
    old_state = snapshot(public, tuple(files.values()))
    ref_state = snapshot(reference, tuple(files.values()))
    if set(old_state) != set(ref_state):
        raise ValueError("Native reference changed the object inventory")
    expected_diff = set()
    evidence_checks = []
    for binding in bindings:
        node_id = binding["node_id"]
        old, new = before[node_id], after[node_id]
        changed = old != new
        evidence_checks.append({"evidence_id": binding["evidence_id"], "node_id": node_id,
            "before": serial(old), "after": serial(new), "group": "required" if changed else "protected"})
        if changed:
            expected_diff.update(structural_key(public, binding))
    actual_diff = {key for key in old_state if old_state[key] != ref_state[key]}
    if actual_diff != expected_diff:
        raise ValueError(f"Native reference diff disagrees with declared edits. Extra {sorted(actual_diff - expected_diff)}. Missing {sorted(expected_diff - actual_diff)}.")
    oracle = {"contract": "Registered native objects and answer values. Preserve protected cells, prose, chart structure and embedded data.",
              "files": list(files.values()), "bindings": bindings, "evidence_checks": evidence_checks,
              "checks": [{"object": key, "group": "required" if key in actual_diff else "protected", "expected": ref_state[key]}
                         for key in sorted(ref_state)]}
    if count:
        oracle["count_formula"] = count
    if not title:
        oracle = with_unordered_qualification_list(oracle)
    before_observations = read_observations(public, bindings)
    after_observations = read_observations(reference, bindings)
    for observed, wanted in ((before_observations, before), (after_observations, after)):
        if errors := [item["evidence_id"] for item in observed if not equal_value(item["value"], wanted[item["node_id"]])]:
            raise ValueError(f"Native evidence readback differs from graph values at {errors}")
    result = grade(reference, oracle)
    if not result["pass"]:
        raise ValueError("Native reference failed Oracle")
    save(private / "evidence_map.json", bindings + ([count] if count else []))
    save(private / "oracle.json", oracle)
    save(private / "native_reference_evaluation.json", result)
    save(private / "native_manifest.json", {"public_sha256": {file: digest(public / file) for file in files.values()},
         "reference_sha256": {file: digest(reference / file) for file in files.values()},
         "native_formula": count or "Archive-check sum in the original workbook is independently protected in the Oracle.",
         "chart": "Every editable chart point is backed by its native PPT chart cache and embedded Excel worksheet."})
    return {"before_observations": before_observations, "after_observations": after_observations,
            "count_formula": count,
            "oracle": oracle, "reference_grade": result, "required_native_objects": sorted(actual_diff)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = grade(args.submission, json.loads(args.oracle.read_text()))
    if args.report:
        save(args.report, result)
    print(json.dumps({key: result[key] for key in ("pass", "groups")}, ensure_ascii=False))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
