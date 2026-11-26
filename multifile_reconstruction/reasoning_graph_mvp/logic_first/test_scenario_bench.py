"""Regression tests for reusable policy compilation and evidence verification."""
from copy import deepcopy
from fractions import Fraction

import pytest

from .scenario_bench import build
from .scenario_graph import add_backward, compile_graph, execute_verified, observations, updated, verify_metrics


def proposals():
    labels = {
        "planned": "计划完成件数", "done": "实际完成的计划件数",
        "inspected": "抽检件数", "acceptable": "抽检合格件数",
    }
    raw = {
        "title": "合成质量复核", "entity_label": "批次", "period": "2025-Q4",
        "entities": ["甲", "乙", "丙", "丁", "戊", "己"],
        "observations": [
            {"id": key, "label": name, "concept": None, "unit": {"unit": 1},
             "min": 1 if key in {"planned", "inspected"} else 0, "max": 100, "step": 1}
            for key, name in labels.items()
        ],
        "constraints": [
            {"left": "done", "op": "LE", "right": "planned"},
            {"left": "acceptable", "op": "LE", "right": "inspected"},
        ],
    }
    forward = {"backward_target": "quality", "metrics": []}
    for key, title, inputs in (
        ("completion", "完成占比", ["done", "planned"]),
        ("quality", "合格占比", ["acceptable", "inspected"]),
    ):
        forward["metrics"].append({"id": key, "label": title, "concept": None, "unit": {},
                                   "semantics": "share", "operands": inputs, "formula": {"op": "DIV", "args": inputs}})
    backward = {"paths": [{"op": "DIV", "args": ["acceptable", "inspected"]}]}
    policy = {
        "conditions": [
            {"id": "completion_gate", "metric": "completion", "op": "GE", "threshold": "0.7"},
            {"id": "quality_gate", "metric": "quality", "op": "GE", "threshold": "0.8"},
        ],
        "selection": {"id": "selected", "label": "合格批次", "op": "AND",
                      "inputs": ["completion_gate", "quality_gate"]},
        "reports": [{"id": "selected_count", "op": "COUNT", "metric": None},
                    {"id": "mean_quality", "op": "AVG", "metric": "quality"}],
    }
    return {"observations": raw, "forward": forward, "backward": backward, "policy": policy}


def test_wrong_formula_and_pending_industry_definition_are_rejected():
    drafts = proposals()
    base = observations(drafts["observations"])
    wrong = deepcopy(drafts["forward"])
    wrong["metrics"][0]["formula"]["args"] = ["planned", "done"]
    with pytest.raises(ValueError, match="disagrees with share"):
        verify_metrics(base, wrong)
    unknown = deepcopy(drafts["forward"])
    unknown["metrics"][0]["concept"] = "unsupported_industry_metric"
    with pytest.raises(ValueError, match="Unconfirmed objective"):
        verify_metrics(base, unknown)


def test_backward_rejects_cyclic_path_and_keeps_valid_one():
    drafts = proposals()
    base = observations(drafts["observations"])
    metrics, _, expressions = verify_metrics(base, drafts["forward"])
    backward = drafts["backward"]
    backward["paths"].insert(0, {"op": "ADD", "args": ["quality", "completion"]})
    result = add_backward(base, metrics, expressions, "quality", backward)
    assert len(result["verified_paths"]) == len(result["rejected_paths"]) == 1


def test_same_builder_generates_witnessed_native_tasks(tmp_path, monkeypatch):
    drafts = proposals()
    calls = []

    def simulated_author(stage, topic, goal, context, directory, **options):
        calls.append(stage)
        if stage == "backward":
            assert "formula" not in context["target"]
        return drafts[stage]

    monkeypatch.setattr("multifile_reconstruction.reasoning_graph_mvp.logic_first.scenario_bench.propose",
                        simulated_author)
    result = build("测试业务", "季度质量复核", tmp_path, seed=8128, attempts=1)
    assert result["passed"], result
    assert calls == ["observations", "forward", "backward", "policy"]
    assert result["event"]["before"] != result["event"]["after"]
    assert {key: (item["minimum_files"], item["minimum_depth"]) for key, item in result["layouts"].items()} == {
        "shallow_concentrated": (1, 3), "shallow_cross_file": (3, 3),
        "deep_concentrated": (1, 4), "deep_cross_file": (3, 4),
    }
    for layout in result["layouts"].values():
        assert layout["insufficient_subsets"] == layout["witnessed_subsets"]
        assert layout["controls"] == {
            "reference": True, "unchanged_original": False, "unchanged_word": False,
            "wrong_corrected_value": False, "protected_formula_replaced": False, "chart_cache_corrupted": False,
        }


def test_shared_capacity_changes_two_other_entities_under_unchanged_ranking():
    drafts = proposals()
    raw = drafts["observations"]
    raw["observations"].append({"id": "amount", "label": "所需金额", "concept": None,
                                "unit": {"CNY": 1}, "min": 1, "max": 100, "step": 1})
    base = observations(raw)
    drafts["forward"]["metrics"].append({"id": "missing", "label": "计划未完成数", "concept": None,
                                          "unit": {"unit": 1}, "semantics": "difference",
                                          "operands": ["planned", "done"],
                                          "formula": {"op": "SUB", "args": ["planned", "done"]}})
    metrics, meanings, expressions = verify_metrics(base, drafts["forward"])
    backward = add_backward(base, metrics, expressions, "quality", drafts["backward"])
    policy = {**drafts["policy"], "allocation": {"priority": "quality", "order": "DESC", "demand": "amount",
                                               "limit": "100", "limit_label": "金额上限"}}
    policy["conditions"].append({"id": "missing_gate", "metric": "missing", "op": "LE", "threshold": 40})
    policy["groups"] = [{"id": "completion_alternatives", "label": "达到任一完成标准", "op": "OR",
                        "inputs": ["completion_gate", "missing_gate"]}]
    policy["selection"]["inputs"] = ["completion_alternatives", "quality_gate"]
    policy["reports"][0] = {"id": "completion_count", "op": "COUNT", "metric": None,
                            "scope": "completion_alternatives"}
    a, b, c, *_ = raw["entities"]
    facts = {key: dict(zip(raw["entities"], values, strict=True)) for key, values in {
        "planned": [100] * 6, "done": [90, 90, 60, 0, 0, 0], "inspected": [100] * 6,
        "acceptable": [95, 90, 85, 0, 0, 0], "amount": [50, 50, 40, 1, 1, 1],
    }.items()}
    facts.update(threshold_completion_gate=Fraction(7, 10), threshold_quality_gate=Fraction(4, 5),
                 threshold_missing_gate=40, resource_limit=100)
    changed = updated(facts, {"node_id": "amount", "entity": a, "before": 50, "after": 60})
    for mode, prior, later in (("INDIVIDUAL_LIMIT", {a, b, c}, {a, b, c}),
                               ("ALLOCATE", {a, b}, {a, c})):
        graph = compile_graph(base, metrics, meanings, backward, policy, allocation_mode=mode)
        assert execute_verified(graph, facts)["selected"] == prior
        new = execute_verified(graph, changed)
        assert new["selected"] == later
        assert new["completion_count"] == 3
