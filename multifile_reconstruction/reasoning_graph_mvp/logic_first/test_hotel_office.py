from fractions import Fraction
import json

from openpyxl import load_workbook
from pptx import Presentation

from .hotel_office import FILES, build_layout, grade, native_controls


def test_native_evidence_reference_and_negative_controls(tmp_path):
    """Exercise every native location, including two charts sharing hotel names."""
    slots = [
        {"node_id": "sold", "file": "excel", "label": "已售间夜 / Sold nights", "role": "evidence"},
        {"node_id": "posted", "file": "excel", "label": "已发布间夜 / Posted nights", "role": "output"},
        {"node_id": "excel_a", "semantic_node_id": "qualified", "file": "excel", "label": "甲状态 / Hotel A", "role": "output"},
        {"node_id": "excel_b", "semantic_node_id": "qualified", "file": "excel", "label": "乙状态 / Hotel B", "role": "output"},
        {"node_id": "limit", "file": "word", "label": "入住率门槛 / Occupancy floor", "role": "policy"},
        {"node_id": "summary", "file": "word", "label": "复核结论 / Review outcome", "role": "output"},
        {"node_id": "word_count", "semantic_node_id": "qualified_count", "file": "word", "label": "达标数量 / Qualified count", "role": "output"},
    ]
    for key, label, category, role, chart in [
        ("adr_a", "甲房价", "Hotel A", "evidence", "平均房价 / ADR"),
        ("adr_b", "乙房价", "Hotel B", "evidence", "平均房价 / ADR"),
        ("eligible_a", "甲复核", "Hotel A", "output", "复核结果 / Qualification"),
        ("eligible_b", "乙复核", "Hotel B", "output", "复核结果 / Qualification"),
    ]:
        slots.append({"node_id": key, "file": "ppt", "label": label, "category": category, "role": role, "chart": chart})
    before = {"sold": 7500, "posted": 5000, "limit": Fraction(3, 4), "summary": "复核 / Review",
              "excel_a": False, "excel_b": True, "word_count": 1,
              "adr_a": Fraction(890, 3), "adr_b": 150, "eligible_a": False, "eligible_b": True}
    after = {**before, "posted": 7500, "summary": "通过 / Pass", "eligible_a": True, "excel_a": True, "word_count": 2}
    result = build_layout(tmp_path, slots, before, after, "Update the operating review.")
    actual = result["after_observations"]
    assert {row["location"]["kind"] for row in actual if row["node_id"] == "eligible_a" and row["value"] is True} == {
        "table", "chart_cache", "chart_workbook"}
    workbook = load_workbook(tmp_path / "private/reference" / FILES["excel"])
    assert any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)
    assert workbook["复核计算"]["B2"].value.startswith("=COUNTIF(")
    for location, expected in [("public", 1), ("private/reference", 2)]:
        cached = load_workbook(tmp_path / location / FILES["excel"], data_only=True)
        assert cached["复核计算"]["B2"].value == expected
    deck = Presentation(tmp_path / "private/reference" / FILES["ppt"])
    assert sum(shape.has_chart for slide in deck.slides for shape in slide.shapes) == 2
    oracle = json.loads((tmp_path / "private/oracle.json").read_text())
    assert grade(tmp_path / "private/reference", oracle)["pass"]
    assert set(path.name for path in (tmp_path / "public").iterdir()) == {*FILES.values(), "instruction.md"}
    controls = native_controls(tmp_path, oracle)
    assert {name for name, result in controls.items() if result["pass"]} == {"reference"}
