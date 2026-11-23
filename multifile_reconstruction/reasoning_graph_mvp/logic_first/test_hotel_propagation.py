"""Trace regressions against copies of the already verified native hotel files."""
from shutil import copytree

from .hotel_graph import ARTIFACTS, read_json
from .hotel_propagation import write_propagation


def test_trace_distinguishes_the_approved_source_the_issued_report_and_alternatives(tmp_path):
    root = ARTIFACTS / "hotel_evidence"
    layout = root / "layouts/deep_cross_file"
    copytree(layout / "public", tmp_path / "public")
    copytree(layout / "private/reference", tmp_path / "private/reference")
    graph = read_json(root / "private/graph.json")
    case = read_json(root / "private/case.json")
    oracle = read_json(layout / "private/oracle.json")
    result = write_propagation(tmp_path, graph, case, oracle)
    nodes = {item["id"]: item for item in result["nodes"]}
    event_entity = case["event"]["entity"]
    sold = nodes["sold_room_nights"]
    issued, source = ({row["role"]: row for row in sold["locations"] if row.get("entity") == event_entity}[key]
                      for key in ("output", "evidence"))
    assert issued["observed_initial"] == 6000 and issued["native_action"] == "required_edit"
    assert source["observed_initial"] == 8000 and source["business_before"] == 6000 and source["native_action"] == "preserve"
    revpar = nodes["revpar"]
    assert revpar["state"] == "related_unchanged" and revpar["materialization"] == "internal_only"
    assert revpar["chosen_proof_event_path"] == {"before": False, "after": False}
    routes = [rule for rule in result["productions"] if rule["output"] == "revpar"]
    assert len(routes) == 2
    assert all(rule["execution"]["after"]["evaluated_steps"] for rule in routes)
    assert sum(rule["execution"]["after"]["selected_in_canonical_proof"] for rule in routes) == 1
    edited_chart = [row for row in nodes["qualified"]["locations"] if row.get("entity") == event_entity
                    and row["file_location"]["kind"] in {"chart_cache", "chart_workbook"}]
    assert len(edited_chart) == 2 and all(row["native_action"] == "required_edit" for row in edited_chart)
    count = next(row for row in nodes["qualified_count"]["locations"] if row["role"] == "output_formula")
    assert count["native_action"] == "recalculates" and count["formula_disposition"] == "preserve"
    assert count["native_initial"]["formula"] == count["native_reference"]["formula"]
    assert (tmp_path / "private/propagation.md").exists()
