"""Scoring guards, not model performance tests; no mocked results reported as inference."""
import json
from pathlib import Path

import pytest

from alignment_probe.probe import audit_source, judge, parse_response, prepare, selected_formula_trace, summarize_coverage

GOLD = json.loads(Path(__file__).with_name("gold.json").read_text())


def test_direct_and_recomputed_evidence_are_both_accepted():
    assert judge(["Review!B5"], GOLD)["passed"]
    assert judge(["Review!B3", "Review!B4"], GOLD)["passed"]


def test_one_amount_or_a_label_is_not_a_growth_judgement():
    for wrong in [["Review!B2"], ["Review!B3"], ["Review!B4"], ["Review!A5"], ["Review!B2", "Review!B3"]]:
        assert not judge(wrong, GOLD)["passed"]


def test_table_to_native_coordinate_mapping_and_no_gold_in_prompt(tmp_path):
    requests, mapping = prepare(tmp_path)
    assert parse_response("<ans>[(4, 1)]</ans>", mapping) == ["Review!B5"]
    assert parse_response("<ans>[(2, 1), (3, 1)]</ans>", mapping) == ["Review!B3", "Review!B4"]
    assert "7.2%" in requests[0]["messages"][1]["content"]
    # The second claim removes its percent, not the numeric evidence in the table.
    claim = json.loads((tmp_path / "public/input.json").read_text())["cases"][1]["claim"]
    assert "%" not in claim and "增长" in claim
    for request in requests:
        prompt = json.dumps(request, ensure_ascii=False)
        assert "sufficient_numeric_sets" not in prompt
        assert "expected_direct_formula_precedents" not in prompt


@pytest.mark.parametrize("response", ["[(4,1)]", "<ans>[]</ans>", "<ans>[(99,1)]</ans>",
                                     "<ans>[(True,1)]</ans>", "<ans>__import__('os').getcwd()</ans>"])
def test_invalid_or_executable_model_output_is_rejected(response):
    with pytest.raises((ValueError, SyntaxError)):
        parse_response(response, {"4,1": "Review!B5"})


def test_trace_is_rooted_in_the_selection_not_the_gold():
    graph = {"nodes": [{"id": "i", "sheet": "S", "addr": "A1"},
                        {"id": "f", "sheet": "S", "addr": "A2", "formula": "=A1+1"}],
             "edges": [{"id": "e", "source": "i", "target": "f"}], "meta": {}}
    assert selected_formula_trace(["S!A2"], graph)["edges"] == graph["edges"]
    assert selected_formula_trace(["S!A1"], graph)["edges"] == []


def test_original_numbers_and_claim_are_independently_consistent():
    audit = audit_source()
    assert audit["passed"]
    assert audit["exact_growth"] == "83/1150"


def test_selecting_every_number_is_not_reported_as_precise_alignment():
    summary = {"status": "PASS", "model_alignment_verified": True,
               "cases": [judge(["Review!B2", "Review!B3", "Review!B4", "Review!B5"], GOLD)]}
    summarize_coverage(summary)
    assert summary["status"] == "PASS_WITH_EXTRA_EVIDENCE"
    assert summary["evidence_coverage_passed"] == 1
    assert summary["numeric_minimality_passed"] == 0
    assert not summary["distractor_rejection_verified"]
    assert "model_alignment_verified" not in summary
