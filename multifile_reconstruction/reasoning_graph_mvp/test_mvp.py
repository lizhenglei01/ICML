from copy import deepcopy
import json
from pathlib import Path
import pytest

from .graph import paths
from .operators import InvalidGraph, apply
from .verifier import verify, safe_feedback
from .run import negative_candidates
from .generate import direct_request, backward_request


def task(profit=False):
    return {"known_facts":{"revenue":120,"cost":90,**({"profit":30} if profit else {})},"target":"gross_profit_margin"}


def graph():
    return {"target":"gross_profit_margin","quantities":[
        {"id":"gross_profit_margin","concept":"gross_profit_margin","unit":"ratio"},
        {"id":"profit","concept":"gross_profit","unit":"money"}],
        "productions":[{"id":"subtract","output":"profit","op":"SUB","inputs":["revenue","cost"]},
                       {"id":"divide","output":"gross_profit_margin","op":"DIV","inputs":["profit","revenue"]}]}


def test_intermediate_is_required_when_not_given_and_all_perturbations_pass():
    r=verify(graph(),task())
    assert r["accepted"] and r["minimal_input_sets"]==[["cost","revenue"]]
    p=r["valid_paths"][0]
    assert p["result"]==0.25 and p["operator_count"]==p["dependency_depth"]==2
    assert len(p["perturbations"])==31 and all(t["pass"] for t in p["perturbations"])
    assert any(t["facts"]["cost"]>t["facts"]["revenue"] for t in p["perturbations"])


def test_or_includes_given_and_computed_paths_and_reduces_input_sets():
    r=verify(graph(),task(True))
    assert r["accepted"]
    assert r["minimal_input_sets"]==[["cost","revenue"],["profit","revenue"]]
    assert sorted(p["operator_count"] for p in r["valid_paths"])==[1,2]


@pytest.mark.parametrize("name",["revenue_div_cost","sum_div_revenue","sqrt_difference"])
def test_required_wrong_expressions_are_rejected(name):
    assert not verify(negative_candidates(task())[name],task())["accepted"]


def test_unknown_intermediate_cannot_be_treated_as_a_given_leaf():
    g=graph();g["productions"]=g["productions"][1:]
    r=verify(g,task());assert r["errors"][0]["code"]=="MISSING_LEAF"
    assert verify(g,task(),partial=True)["status"]=="INCOMPLETE"


def test_cycles_and_bad_arity_are_rejected():
    g=graph();g["productions"][0]["inputs"]=["revenue","profit"]
    assert verify(g,task())["errors"][0]["code"]=="CYCLE"
    g=graph();g["productions"][1]["inputs"]=["profit"]
    assert verify(g,task())["errors"][0]["code"]=="ARITY"


def test_domains_and_units_are_enforced():
    with pytest.raises(InvalidGraph,match="zero"):apply("DIV",[1,0])
    with pytest.raises(InvalidGraph,match="negative"):apply("SQRT",[-1])
    g=graph();g["quantities"][1]["unit"]="ratio"
    assert not verify(g,task())["accepted"]


def test_mathematical_match_does_not_validate_wrong_business_annotation():
    g=graph();g["quantities"][1]["concept"]="cost"
    r=verify(g,task())
    assert not r["accepted"] and r["rejected_paths"][0]["error_code"]=="INTERMEDIATE_SEMANTICS"


def test_expression_valid_at_one_point_but_undefined_for_negative_margin_is_rejected():
    g=graph();g["quantities"] += [{"id":"margin","concept":"gross_profit_margin","unit":"ratio"},
                                 {"id":"root","concept":"auxiliary","unit":"ratio"}]
    g["productions"][1]["output"]="margin"
    g["productions"] += [{"id":"sqrt","output":"root","op":"SQRT","inputs":["margin"]},
                         {"id":"square","output":"gross_profit_margin","op":"MUL","inputs":["root","root"]}]
    r=verify(g,task())
    assert not r["accepted"] and r["rejected_paths"][0]["error_code"]=="UNPROVEN_SQRT_DOMAIN"


def test_inconsistent_given_profit_is_not_silently_used():
    t=task(True);t["known_facts"]["profit"]=31
    r=verify(graph(),t)
    assert not r["accepted"] and r["errors"][0]["code"]=="INCONSISTENT_FACTS"


def test_initial_prompt_has_no_intermediate_or_target_formula_and_feedback_is_sanitized():
    p=direct_request(task())["messages"][1]["content"]
    data=json.loads(p)
    assert "profit" not in data["task"]["known_facts"]
    assert "gross_profit" not in data["field_meanings"]
    assert "gross_profit" not in data["output_contract"]["quantity"].values()
    feedback=safe_feedback(verify(graph(),task()))
    assert set(feedback)=={"status","valid_path_count","error_codes","unresolved"}
    assert "expected" not in json.dumps(feedback) and "0.25" not in json.dumps(feedback)


def test_deterministic_fallback_is_explicitly_not_llm_generation():
    from .baseline import search
    r=search(task(True))
    assert not r["llm_generated"] and r["origin"]=="deterministic_program_search"
    assert ["profit","revenue"] in r["minimal_input_sets"] and ["cost","revenue"] in r["minimal_input_sets"]
