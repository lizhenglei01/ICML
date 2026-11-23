from copy import deepcopy
import json
from pathlib import Path
import shutil

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
import pytest

from .build import build, save
from .indirect import add_indirect_evidence, read_indirect, temporal_forms
from .revision import build_revision, controls, grade, reviewed_truth
from .run import audit


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    path = tmp_path_factory.mktemp("native_revision")
    build(path, author="literal")
    add_indirect_evidence(path, model=False)
    build_revision(path)
    return path


def test_indirect_evidence_requires_native_operands_and_reconstructs_exact_time(package):
    assert audit(package)["pass"]
    bindings = json.loads((package / "private/bindings.json").read_text())
    b = next(b for b in bindings if b.get("rule"))
    paragraph = Document(package / "public/visitor_notes.docx").paragraphs[b["paragraph"]]
    assert b["value"] not in paragraph.text[b["start"]:b["end"]]
    assert read_indirect(b, package / "public") == "15:15"
    # After cancellation the same sentence remains valid, with new operands.
    revised = Document(package / "private/revision/reference/visitor_notes.docx")
    assert b["evidence_sentence"] in revised.paragraphs[b["paragraph"]].text
    assert read_indirect(b, package / "private/revision/reference") == "13:45"


def test_bad_refiner_evidence_is_rejected_before_it_can_become_truth(tmp_path):
    build(tmp_path, author="literal")
    save(tmp_path / "private/refiner/evidence_pool.json", {"fragments": [{"text": "The break ends whenever convenient."}]})
    with pytest.raises(ValueError, match="verified temporal grammar"):
        add_indirect_evidence(tmp_path, model=False)


def test_independent_time_route_catches_corrupted_gold_replacements(package, tmp_path):
    (tmp_path / "private").mkdir()
    sample = json.loads((package / "private/source_sample.json").read_text())
    op = next(p for p in sample["expected_patch"] if p["path"] == "/summary/day_end_time")
    op["value"] = "15:00"
    save(tmp_path / "private/source_sample.json", sample)
    with pytest.raises(AssertionError, match="Independent recalculation"):
        reviewed_truth(tmp_path)


def test_required_protected_and_consistency_controls(package):
    oracle = json.loads((package / "private/revision/oracle.json").read_text())
    results = controls(package, oracle)
    assert results["correct"]["pass"]
    assert all(not r["pass"] for k, r in results.items() if k != "correct")
    assert any(f["group"] == "protected" for f in results["unrelated_content_changed"]["failures"])
    assert any(f["group"] == "required" for f in results["omitted_semantic_cleanup"]["failures"])
    assert any(f["group"] == "consistency" for f in results["wrong_time"]["failures"])
    assert any(f["group"] == "structure" for f in results["broken_native_structure"]["failures"])


def test_unaffected_facts_and_changed_result_with_unchanged_evidence_are_explicit(package):
    changes = json.loads((package / "private/revision/fact_changes.json").read_text())
    late_finish = next(c for c in changes if c["old_path"] == "/stops/5/end_time")
    assert late_finish["state_changed"] and not late_finish["render_changed"]
    assert late_finish["before"] == "15:15" and late_finish["after"] == "13:45"
    unaffected = next(c for c in changes if c["old_path"] == "/stops/0/start_time")
    assert not unaffected["state_changed"] and not unaffected["render_changed"]
    assert all(c["before"] == c["after"] for c in changes if not c["state_changed"])


def test_oracle_data_cannot_be_self_modified_by_submission(package):
    oracle_path = package / "private/revision/oracle.json"
    before = oracle_path.read_bytes()
    oracle = json.loads(before)
    report = grade(package / "private/revision/reference", oracle)
    assert report["pass"]
    assert oracle_path.read_bytes() == before
    assert len(report["checks"]) == len(oracle["checks"]) + len(oracle["layout_bounds"]) + 5


def test_valid_local_edit_variants_pass_without_weakening_layout_protection(package, tmp_path):
    reference = package / "private/revision/reference"
    shutil.copytree(reference, tmp_path, dirs_exist_ok=True)
    oracle = json.loads((package / "private/revision/oracle.json").read_text())
    # Clearing/compacting a table instead of deleting a worksheet row retains
    # the footer's original coordinate. Both have the same data and ordering.
    wb = load_workbook(tmp_path / "day_schedule.xlsx")
    footer = next(c for c in wb.active["A"] if isinstance(c.value, str) and c.value.startswith("Listed in visit order"))
    wb.active.insert_rows(footer.row)
    wb.save(tmp_path / "day_schedule.xlsx")
    deck = Presentation(tmp_path / "travel_brief.pptx")
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_table:
                shape.height = sum(r.height for r in shape.table.rows)
    deck.save(tmp_path / "travel_brief.pptx")
    doc = Document(tmp_path / "visitor_notes.docx")
    for p in doc.paragraphs:
        for run in p.runs:
            run.text = " ".join(run.text.split())
    doc.save(tmp_path / "visitor_notes.docx")
    assert grade(tmp_path, oracle)["pass"]
    # Permitting harmless one-row compaction must not permit arbitrary spacing.
    wb.active.insert_rows(footer.row, 20)
    wb.save(tmp_path / "day_schedule.xlsx")
    report = grade(tmp_path, oracle)
    assert not report["pass"]
    assert any(c["group"] == "structure" and not c["pass"] for c in report["checks"])


def test_reference_preserves_chart_number_format_and_accepts_equivalent_cache_spelling(package, tmp_path):
    from zipfile import ZipFile
    from lxml import etree
    reference = package / "private/revision/reference"
    shutil.copytree(reference, tmp_path, dirs_exist_ok=True)
    oracle = json.loads((package / "private/revision/oracle.json").read_text())
    for directory in (package / "public", reference):
        deck = Presentation(directory / "travel_brief.pptx")
        chart = next(s.chart for sl in deck.slides for s in sl.shapes if s.has_chart)
        assert chart._chartSpace.xpath(".//c:val/c:numRef/c:numCache/c:formatCode")[0].text == "0"
    path = tmp_path / "travel_brief.pptx"
    with ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    key = next(n for n in parts if n.startswith("ppt/charts/chart") and n.endswith(".xml"))
    tree = etree.fromstring(parts[key])
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
    for value in tree.xpath("//c:numCache/c:pt/c:v", namespaces=ns):
        value.text += ".0"
    parts[key] = etree.tostring(tree)
    with ZipFile(path, "w") as z:
        for n, data in parts.items(): z.writestr(n, data)
    assert grade(tmp_path, oracle)["pass"]
    # A real format change is still an error, unlike 45 versus 45.0.
    tree.xpath("//c:val/c:numRef/c:numCache/c:formatCode", namespaces=ns)[0].text = "0.00%"
    parts[key] = etree.tostring(tree)
    with ZipFile(path, "w") as z:
        for n, data in parts.items(): z.writestr(n, data)
    assert not grade(tmp_path, oracle)["pass"]


def test_schema_free_json_is_not_mistaken_for_a_failed_fixed_schema_submission(package):
    from .staged import evaluate_revision
    working = package / "model/revision/working"
    shutil.copytree(package / "private/revision/reference", working, dirs_exist_ok=True)
    save(working / "updated_state.json", {"我自己组织的信息": {"说明": "任意结构，语义仍需核对"}})
    result = evaluate_revision(package)
    assert result["native_pass"] is True
    assert result["schema_provided"] is False
    assert result["state_pass"] is None
    assert result["state_status"] == "NOT_SCORED"
    assert result["state_correct"] is None and result["state_total"] is None
