"""Local native-file revisions and a frozen, data-driven minimal-edit oracle.

Gold comes from the upstream reviewed patch, independently checked against time
arithmetic. This is a registered scenario, not automatic business-rule discovery.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

from docx import Document
from lxml import etree
from openpyxl import load_workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from pptx import Presentation
from pptx.chart.data import CategoryChartData

from .build import digest, save, surface
from .evaluate import leaves
from .indirect import replace_span, read_indirect
from .upstreams import THIRD

FILES = ("day_schedule.xlsx", "visitor_notes.docx", "travel_brief.pptx")


def reviewed_truth(package):
    """Execute upstream RFC6902 utility; independently calculate a second answer."""
    private = Path(package) / "private"
    sample = json.loads((private / "source_sample.json").read_text())
    source = THIRD / "llm-revision-propagation/benchmark/jsonptr.py"
    spec = importlib.util.spec_from_file_location("multifile_revprop_jsonptr", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = sample["current_json"]
    patch = sample["expected_patch"]
    new = module.apply_patch(old, patch)
    # Independent route: no patch values or generator-derived time strings.
    removed = [int(p["path"].split("/")[2]) for p in patch
               if p["op"] == "remove" and p["path"].count("/") == 2 and p["path"].startswith("/stops/")]
    if len(removed) != 1:
        raise ValueError("Registered scenario requires exactly one removed activity")
    index = removed[0]
    independent = deepcopy(old)
    independent["stops"].pop(index)
    clock = datetime.strptime(old["day_start_time"], "%H:%M")
    assertions = []
    for i, stop in enumerate(independent["stops"]):
        stop["start_time"] = clock.strftime("%H:%M")
        clock += timedelta(minutes=stop["duration_minutes"])
        stop["end_time"] = clock.strftime("%H:%M")
        clock += timedelta(minutes=old["transition_gap_minutes"])
        assertions.append({"path": f"/stops/{i}", "name": stop["name"],
                           "start": stop["start_time"], "finish": stop["end_time"],
                           "pass": stop == new["stops"][i]})
    independent["summary"]["scheduled_stop_count"] = len(independent["stops"])
    independent["summary"]["day_end_time"] = independent["stops"][-1]["end_time"]
    # Registered semantic policy, confirmed by upstream annotation_notes REVIEW EDIT.
    # Only dedicated ferry reminders are removed, not harbor photography preferences.
    cleanup = ("/contact_plan/ferry_contact", "/rain_plan/ferry_flexibility",
               "/transportation_notes/ferry_ticket_handling", "/device_preparation/offline_reference")
    for path in cleanup:
        parent, key = path.strip("/").split("/")
        if "ferry" not in (key + " " + str(independent[parent][key])).lower():
            raise AssertionError("Unconfirmed semantic cleanup")
        del independent[parent][key]
    if independent != new or not all(x["pass"] for x in assertions):
        raise AssertionError("Independent recalculation disagrees with reviewed gold")
    if "expected_json" in sample and sample["expected_json"] != new:
        raise AssertionError("Upstream reference JSON disagrees with its patch")
    rules = [
        {"id": "temporal_sequence", "source": "source_sample.conversation_pattern / cascade_pattern; current_json day_start_time, transition_gap_minutes, stops[*].duration_minutes",
         "condition": "One activity removed; remaining order, duration and transition gap retained",
         "calculation": "first_start=day_start; end=start+duration; next_start=end+gap",
         "dependents": [x["path"] for x in assertions]},
        {"id": "summary", "source": "source_sample.cascade_pattern and current_json.summary",
         "condition": "Remaining ordered stops", "calculation": "count=len(stops); day_end=last stop end"},
        {"id": "ferry_only_preparation", "source": "source_sample.annotation_notes REVIEW EDIT (with author)",
         "condition": "Ferry ride removed; these reminders concern only that ride", "calculation": "delete the four confirmed dedicated reminder leaves",
         "dependents": list(cleanup), "manual_policy": True},
        {"id": "preservation", "source": "source_sample.expected_patch and annotation_notes",
         "condition": "No registered dependency on the removed activity", "calculation": "retain value and existing native content"},
    ]
    report = {"pass": independent == new, "checks": assertions,
              "summary": independent["summary"], "semantic_removals": list(cleanup),
              "gold_origin": "Upstream human-reviewed patch, executed with benchmark.jsonptr.apply_patch",
              "independence": "datetime sequential recomputation does not read gold time/count replacement values; semantic cleanup policy remains manually registered."}
    save(private / "revision/truth.json", new)
    save(private / "revision/dependencies.json", rules)
    save(private / "revision/independent_assertions.json", report)
    save(private / "revision/gold_patch.json", patch)
    return old, new, index


def at(value, path):
    for part in path.strip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def changes_by_identity(old, new, removed, bindings):
    changes = []
    for b in bindings:
        path = b["path"]
        parts = path.split("/")
        new_path = path
        deleted = False
        if parts[1] == "stops":
            index = int(parts[2])
            deleted = index == removed
            if index > removed:
                parts[2] = str(index - 1)
                new_path = "/".join(parts)
        try:
            value = None if deleted else at(new, new_path)
        except KeyError:
            deleted, value = True, None
        old_value = at(old, path)
        state_changed = deleted or type(old_value) is not type(value) or old_value != value
        # A general derivation sentence can stay even when its computed result changes.
        render_changed = deleted or (state_changed and not b.get("rule"))
        changes.append({"binding": b, "old_path": path, "new_path": None if deleted else new_path,
                        "deleted": deleted, "before": old_value, "after": value,
                        "state_changed": state_changed, "render_changed": render_changed})
    return changes


def patch_text_frame(shape, replacements):
    offsets = []
    offset = 0
    for paragraph in shape.text_frame.paragraphs:
        offsets.append((offset, paragraph))
        offset += len(paragraph.text) + 1
    for start, end, text in sorted(replacements, reverse=True):
        matches = [(o, p) for o, p in offsets if o <= start and end <= o + len(p.text)]
        if len(matches) != 1:
            raise ValueError("PPT replacement crosses paragraph boundary")
        o, p = matches[0]
        replace_span(p, start-o, end-o, text)


def patch_reference(package, destination, old, new, removed, changes):
    """Copy original bytes and edit only native objects; never rerun the writer."""
    package, destination = Path(package), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(package / "public" / name, destination / name)
    wb = load_workbook(destination / FILES[0])
    for change in changes:
        b = change["binding"]
        if b["file"] == FILES[0] and change["render_changed"] and not change["deleted"]:
            wb[b["sheet"]][b["cell"]] = change["after"]
    stop_name = old["stops"][removed]["name"]
    rows = [c.row for c in wb.active["A"] if c.value == stop_name]
    if len(rows) != 1:
        raise ValueError("Activity row not unique")
    wb.active.delete_rows(rows[0])
    wb.save(destination / FILES[0])
    document = Document(destination / FILES[1])
    operations = defaultdict(list)
    for change in changes:
        b = change["binding"]
        if b["file"] != FILES[1] or not change["render_changed"]:
            continue
        if change["deleted"]:
            operation = (b["claim_start"], b["claim_end"], "")
        else:
            operation = (b["start"], b["end"], surface(change["after"]))
        operations[b["paragraph"]].append(operation)
    for index, replacements in operations.items():
        for start, end, value in sorted(replacements, reverse=True):
            replace_span(document.paragraphs[index], start, end, value)
    document.save(destination / FILES[1])
    deck = Presentation(destination / FILES[2])
    operations = defaultdict(list)
    for change in changes:
        b = change["binding"]
        if b["file"] == FILES[2] and "point" not in b and change["render_changed"]:
            if change["deleted"]:
                raise ValueError("Unexpected global PPT fact deletion")
            operations[b["slide"], b["shape"]].append((b["start"], b["end"], surface(change["after"])))
    for (slide, shape), replacements in operations.items():
        patch_text_frame(deck.slides[slide].shapes[shape], replacements)
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_chart:
                codes = shape.chart._chartSpace.xpath(".//c:val/c:numRef/c:numCache/c:formatCode")
                data = CategoryChartData(number_format=codes[0].text if codes else "General")
                data.categories = [s["name"] for s in new["stops"]]
                data.add_series(shape.chart.series[0].name, [s["duration_minutes"] for s in new["stops"]])
                shape.chart.replace_data(data)
            if shape.has_table:
                for row in list(shape.table.rows):
                    if any(cell.text == stop_name for cell in row.cells):
                        shape.table._tbl.remove(row._tr)
    deck.save(destination / FILES[2])


def xml_text(element):
    return etree.tostring(element, method="c14n").decode() if element is not None else None


def chart_definition(chart):
    """XML structure/format, canonicalizing equivalent numeric cache spellings."""
    from decimal import Decimal
    tree = deepcopy(chart._chartSpace)
    for value in tree.xpath(".//c:numCache/c:pt/c:v"):
        value.text = format(Decimal(value.text).normalize(), "f")
    return xml_text(tree)


def snapshot(directory, files=FILES):
    """Canonical, strict minimal-edit contract; no model inference during grading."""
    directory = Path(directory)
    result = {}
    for name in files:
        with ZipFile(directory / name) as archive:
            if archive.testzip():
                raise ValueError("Corrupt ZIP member")
            result[name + ":package_parts"] = sorted(archive.namelist())
            # Protect styles/media/relationships etc. Mutable content has native
            # object checks below, avoiding byte comparisons of changed XML.
            for part in archive.namelist():
                mutable = (name.endswith("xlsx") and part.startswith("xl/worksheets/")) or (
                    name.endswith("docx") and part == "word/document.xml") or (
                    name.endswith("pptx") and (part.startswith("ppt/slides/slide") and part.endswith(".xml")
                                               or part.startswith("ppt/charts/") and part.endswith(".xml")
                                               or part.startswith("ppt/embeddings/")))
                if mutable or part.startswith("docProps/"):
                    continue
                import hashlib
                raw = archive.read(part)
                if part.endswith((".xml", ".rels")):
                    raw = etree.tostring(etree.fromstring(raw), method="c14n")
                result[name + ":protected_part:" + part] = hashlib.sha256(raw).hexdigest()
    wb = load_workbook(directory / files[0], data_only=False)
    result[files[0] + ":sheets"] = wb.sheetnames
    for ws in wb:
        prefix = files[0] + ":sheet:" + ws.title
        result[prefix + ":structure"] = {"state": ws.sheet_state, "merged": str(ws.merged_cells),
            "freeze": ws.freeze_panes, "protection": str(ws.protection),
            "auto_filter": xml_text(ws.auto_filter.to_tree()), "columns": ws.max_column,
            "row_dimensions": {str(k): str(v) for k, v in ws.row_dimensions.items()},
            "column_dimensions": {str(k): str(v) for k, v in ws.column_dimensions.items()}}
        # Ignore wholly blank grid rows when comparing logical content. Deleting
        # the activity row or clearing/compacting its cells are both valid. A
        # separate bounded physical-row check prevents arbitrary layout changes.
        logical_rows = [row for row in ws.iter_rows() if any(c.value is not None for c in row)]
        for logical_index, row in enumerate(logical_rows):
            for cell in row:
                value = cell.value
                if isinstance(value, (ArrayFormula, DataTableFormula)):
                    value = {"formula_type": type(value).__name__, **vars(value)}
                if value is not None or cell.has_style:
                    result[prefix + f":logical_row:{logical_index}:column:{cell.column}"] = {"value": value, "type": cell.data_type,
                        "style": list(cell._style) if cell.has_style else None,
                        "number_format": cell.number_format, "comment": None if cell.comment is None else cell.comment.text,
                        "hyperlink": None if cell.hyperlink is None else cell.hyperlink.target}
    doc = Document(directory / files[1])
    result[files[1] + ":body_structure"] = [el.tag for el in doc._element.body]
    result[files[1] + ":sections"] = [xml_text(s._sectPr) for s in doc.sections]
    for i, p in enumerate(doc.paragraphs):
        result[files[1] + f":paragraph:{i}"] = {"text": " ".join(p.text.split()), "style": p.style.name,
                                               "properties": xml_text(p._p.pPr)}
        # Visible run formatting is protected separately from text. Deduplicate
        # adjacent equal formats so harmless run splits are accepted.
        formats = []
        for run in p.runs:
            if run.text:
                props = xml_text(run._r.rPr)
                if not formats or formats[-1] != props:
                    formats.append(props)
        result[files[1] + f":format:{i}"] = formats
    deck = Presentation(directory / files[2])
    result[files[2] + ":size"] = [deck.slide_width, deck.slide_height, len(deck.slides)]
    for i, slide in enumerate(deck.slides):
        result[files[2] + f":slide:{i}:shape_count"] = len(slide.shapes)
        for j, s in enumerate(slide.shapes):
            key = files[2] + f":slide:{i}:shape:{j}"
            result[key + ":geometry"] = [s.shape_id, s.name, s.shape_type.value, s.left, s.top, s.width,
                                         None if s.has_table else s.height]
            if s.has_text_frame:
                result[key + ":text"] = s.text
                result[key + ":text_properties"] = [xml_text(p._p.pPr) for p in s.text_frame.paragraphs]
            if s.has_table:
                result[key + ":table"] = [[c.text for c in r.cells] for r in s.table.rows]
                result[key + ":row_heights"] = [r.height for r in s.table.rows]
                result[key + ":cell_properties"] = [[xml_text(c._tc.tcPr) for c in r.cells] for r in s.table.rows]
            if s.has_chart:
                chart = s.chart
                result[key + ":chart_definition"] = chart_definition(chart)
                result[key + ":chart"] = {"type": chart.chart_type.value, "style": chart.chart_style,
                    "categories": [c.label for c in chart.plots[0].categories],
                    "series": [{"name": se.name, "values": list(se.values)} for se in chart.series]}
                # The embedded workbook is a second representation of the plot.
                from io import BytesIO
                embedded = load_workbook(BytesIO(chart.part.chart_workbook.xlsx_part.blob), data_only=False)
                result[key + ":embedded_data"] = [[c.value for c in r] for r in embedded.active.iter_rows()]
    return result


def consistency(directory, truth, bindings):
    checks = []
    wb = load_workbook(Path(directory) / FILES[0])
    actual_rows = [(r[0].value, r[1].value) for r in wb.active.iter_rows() if r[0].value in {s["name"] for s in truth["stops"]}]
    checks.append({"id": "schedule_order_and_times", "expected": [(s["name"], s["start_time"]) for s in truth["stops"]],
                   "observed": actual_rows, "pass": actual_rows == [(s["name"], s["start_time"]) for s in truth["stops"]]})
    for b in bindings:
        if b.get("rule"):
            expected = next(s["end_time"] for s in truth["stops"] if s["name"] == b["stop_name"])
            observed = read_indirect(b, directory)
            checks.append({"id": b["rule"], "expected": expected, "observed": observed, "pass": expected == observed})
    deck = Presentation(Path(directory) / FILES[2])
    chart = next(s.chart for sl in deck.slides for s in sl.shapes if s.has_chart)
    actual = list(zip([c.label for c in chart.plots[0].categories], chart.series[0].values))
    expected = [(s["name"], s["duration_minutes"]) for s in truth["stops"]]
    checks.append({"id": "chart_matches_remaining_activities", "expected": expected, "observed": actual, "pass": actual == expected})
    # Independent extraction from the fixed briefing wording, not generated
    # object snapshots: count and day-end must agree with the actual schedule.
    import re
    text = "\n".join(s.text for sl in deck.slides for s in sl.shapes if s.has_text_frame)
    for key, pattern in (("scheduled_stop_count", r"scheduled stop count is (\d+)"),
                         ("day_end_time", r"day end time is (\d\d:\d\d)")):
        found = re.findall(pattern, text)
        observed = (int(found[0]) if key.endswith("count") else found[0]) if len(found) == 1 else None
        expected = truth["summary"][key]
        checks.append({"id": "summary_" + key, "expected": expected, "observed": observed, "pass": expected == observed})
    return checks


def grade(directory, oracle):
    """The same interpreter works with a different generated oracle JSON."""
    checks = []
    try:
        observed = snapshot(directory)
        for item in oracle["checks"]:
            actual = observed.get(item["object"])
            ok = item["object"] in observed and actual == item["expected"] if item["present"] else item["object"] not in observed
            checks.append({**item, "observed": actual, "pass": ok})
        extras = sorted(set(observed) - {c["object"] for c in oracle["checks"]})
        for extra in extras:
            checks.append({"group": "protected", "object": extra, "expected": "absent", "observed": observed[extra], "pass": False})
        for bound in oracle.get("layout_bounds", []):
            if bound["kind"] == "worksheet_rows":
                actual = load_workbook(Path(directory) / bound["file"])[bound["sheet"]].max_row
            else:
                deck = Presentation(Path(directory) / bound["file"])
                actual = deck.slides[bound["slide"]].shapes[bound["shape"]].height
            checks.append({"group": "structure", "object": bound["id"], "expected": [bound["minimum"], bound["maximum"]],
                           "observed": actual, "pass": bound["minimum"] <= actual <= bound["maximum"]})
        semantic = consistency(directory, oracle["truth"], oracle["bindings"])
    except Exception as exc:
        checks.append({"group": "structure", "object": "native_file_parse", "pass": False,
                       "error": type(exc).__name__ + ": " + str(exc)})
        semantic = []
    all_checks = checks + [{"group": "consistency", **s} for s in semantic]
    groups = {}
    for group in ("required", "protected", "consistency", "structure"):
        subset = [c for c in all_checks if c["group"] == group]
        groups[group] = {"passed": sum(c["pass"] for c in subset), "total": len(subset)}
    return {"pass": bool(all_checks) and all(c["pass"] for c in all_checks), "groups": groups, "checks": all_checks}


def build_revision(package):
    package = Path(package)
    old, new, removed = reviewed_truth(package)
    bindings = json.loads((package / "private/bindings.json").read_text())
    changes = changes_by_identity(old, new, removed, bindings)
    reference = package / "private/revision/reference"
    before_hashes = {name: digest(package / "public" / name) for name in FILES}
    patch_reference(package, reference, old, new, removed, changes)
    if before_hashes != {name: digest(package / "public" / name) for name in FILES}:
        raise AssertionError("Original public assets were changed")
    before, after = snapshot(package / "public"), snapshot(reference)
    checks = [{"object": key, "group": "required" if before.get(key) != after.get(key) or (key in before) != (key in after) else "protected",
               "present": key in after, "before": before.get(key), "expected": after.get(key)} for key in sorted(set(before) | set(after))]
    layout_bounds = []
    original_wb, reference_wb = load_workbook(package / "public" / FILES[0]), load_workbook(reference / FILES[0])
    for ws in original_wb:
        layout_bounds.append({"kind": "worksheet_rows", "id": "grid_extent:" + ws.title,
            "file": FILES[0], "sheet": ws.title, "minimum": reference_wb[ws.title].max_row, "maximum": ws.max_row})
    old_deck, new_deck = Presentation(package / "public" / FILES[2]), Presentation(reference / FILES[2])
    for i, slide in enumerate(new_deck.slides):
        for j, shape in enumerate(slide.shapes):
            if shape.has_table:
                layout_bounds.append({"kind": "table_height", "id": f"table_height:{i}:{j}", "file": FILES[2],
                    "slide": i, "shape": j, "minimum": sum(r.height for r in shape.table.rows),
                    "maximum": old_deck.slides[i].shapes[j].height})
    oracle = {"version": 3, "contract": "Minimal edits: preserve unaffected wording, native chart, styles and package parts. Whitespace, equivalent numeric cache spellings, one removed grid row and the native height decrease for the removed table row are accepted. Free paraphrases are outside this first oracle's contract.",
              "checks": checks, "truth": new, "bindings": bindings,
              "layout_bounds": layout_bounds,
              "gold_origin": "RevProp reviewed gold plus independent temporal assertions; this is not a new gold discovery algorithm."}
    save(package / "private/revision/oracle.json", oracle)
    save(package / "private/revision/fact_changes.json", changes)
    instruction = (
        "# Revise the existing day trip\n\n"
        "Continue from your reconstructed state and the existing three Office files. "
        "Remove the Casco Bay harbor ferry ride. Keep the remaining visits in order, with their original activity lengths and the same transition gap. "
        "Update all affected start/finish times, the day summary, and any preparations dedicated solely to the cancelled ride. "
        "Update both the briefing table and its editable native chart. Preserve every unrelated preference and practical arrangement. "
        "Make minimal local edits: retain unrelated wording, formatting, styles, file/slide/paragraph structure and original native objects. "
        "Where a general calculation statement is still valid, retain it. Do not replace the files with flattened images. "
        "Save the same three filenames and updated_state.json in the working directory. Return a short completion message.\n")
    phase2 = package / "phase2"
    phase2.mkdir(exist_ok=True)
    (phase2 / "instruction.md").write_text(instruction)
    sources = json.loads((package / "private/sources.json").read_text())
    if "RevProp.benchmark.jsonptr.apply_patch" not in sources["actual_calls"]:
        sources["actual_calls"].append("RevProp.benchmark.jsonptr.apply_patch")
    save(package / "private/sources.json", sources)
    correct = grade(reference, oracle)
    save(package / "private/revision/reference_evaluation.json", correct)
    if not correct["pass"]:
        raise AssertionError("Native reference failed generated oracle")
    return oracle


def controls(package, oracle):
    package = Path(package)
    root = package / "private/revision/controls"
    reference = package / "private/revision/reference"
    names = ("correct", "excel_only", "omitted_semantic_cleanup", "unrelated_content_changed",
             "wrong_time", "chart_cache_only", "formula_injected", "broken_native_structure")
    reports = {}
    for name in names:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in FILES:
            shutil.copy2(reference / file, directory / file)
        if name == "excel_only":
            for file in FILES[1:]:
                shutil.copy2(package / "public" / file, directory / file)
        elif name in {"omitted_semantic_cleanup", "unrelated_content_changed"}:
            doc = Document(directory / FILES[1])
            if name == "omitted_semantic_cleanup":
                old_doc = Document(package / "public" / FILES[1])
                b = next(b for b in oracle["bindings"] if b["path"] == "/contact_plan/ferry_contact")
                doc.paragraphs[b["paragraph"]].text = old_doc.paragraphs[b["paragraph"]].text
            else:
                b = next(b for b in oracle["bindings"] if b["path"] == "/trip_mood")
                p = doc.paragraphs[b["paragraph"]]
                p.text = p.text.replace("relaxed", "rushed")
            doc.save(directory / FILES[1])
        elif name in {"wrong_time", "formula_injected"}:
            wb = load_workbook(directory / FILES[0])
            row = next(r for r in wb.active.iter_rows() if r[0].value == "Tandem Coffee Roasters break")
            row[1].value = "13:20" if name == "wrong_time" else '=TEXT(TIME(13,15,0),"hh:mm")'
            wb.save(directory / FILES[0])
        elif name == "chart_cache_only":
            # A renderer-visible cache change with an unchanged editable data workbook.
            from io import BytesIO
            with ZipFile(directory / FILES[2]) as z:
                parts = {n: z.read(n) for n in z.namelist()}
            key = next(n for n in parts if n.startswith("ppt/charts/chart") and n.endswith(".xml"))
            tree = etree.fromstring(parts[key])
            value = tree.xpath("//c:val/c:numRef/c:numCache/c:pt/c:v", namespaces={"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"})[0]
            value.text = "999"
            parts[key] = etree.tostring(tree)
            with ZipFile(directory / FILES[2], "w") as z:
                for n, data in parts.items():
                    z.writestr(n, data)
        elif name == "broken_native_structure":
            (directory / FILES[2]).write_bytes(b"not an Office package")
        report = grade(directory, oracle)
        save(root / (name + ".json"), report)
        if report["pass"] != (name == "correct"):
            raise AssertionError("Revision oracle control mismatch: " + name)
        reports[name] = {"pass": report["pass"], "groups": report["groups"],
                         "failures": [c for c in report["checks"] if not c["pass"]]}
    save(root / "summary.json", reports)
    return reports


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Grade native Office edits against a generated private oracle")
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = grade(args.submission, json.loads(args.oracle.read_text()))
    save(args.out, report)
    print(json.dumps({"pass": report["pass"], "groups": report["groups"], "report": str(args.out)}, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)
