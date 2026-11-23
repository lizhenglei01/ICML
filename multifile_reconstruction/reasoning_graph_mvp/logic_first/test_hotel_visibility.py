from fractions import Fraction

from .hotel_visibility import audit_layout, choose_layouts, closed_paths, solve_public


def fixture():
    def rule(identifier, output, operator, inputs, **kwargs):
        return {"id": identifier, "output": output, "op": operator, "inputs": inputs, **kwargs}

    graph = {
        "entities": ["A", "B", "C"],
        "targets": ["qualified", "count", "mean"],
        "productions": [
            rule("direct", "revpar", "DIV", ["revenue", "available"]),
            rule("rate", "occupancy", "DIV", ["sold", "available"]),
            rule("adr", "adr", "DIV", ["revenue", "sold"]),
            rule("indirect", "revpar", "MUL", ["occupancy", "adr"]),
            rule("invert", "adr", "DIV", ["revpar", "occupancy"]),
            rule("occupancy_threshold", "occupied", "GE", ["occupancy", "occupancy_floor"]),
            rule("cross_multiply", "occupancy_min_sold", "MUL", ["occupancy_floor", "available"]),
            rule("cross_compare", "occupied", "COMPARE", ["sold", "occupancy_min_sold"], comparison="GE"),
            rule("adr_threshold", "paid", "GE", ["adr", "adr_floor"]),
            rule("policy", "qualified", "AND", ["occupied", "paid"], requires=["policy_gate"]),
            rule("count", "count", "COUNT", ["qualified"]),
            rule("average", "mean", "AVG", ["qualified", "adr"]),
        ],
    }
    base = {"sold": {"A": 600, "B": 450, "C": 800}, "available": {"A": 1000, "B": 1000, "C": 1000},
            "revenue": {"A": 90000, "B": 40000, "C": 105000}, "occupancy_floor": "3/5", "adr_floor": 130}
    derived = {"occupancy": {"A": "3/5", "B": "9/20", "C": "4/5"},
               "adr": {"A": 150, "B": "800/9", "C": "525/4"}}
    expected = {"qualified": {"A", "C"}, "count": 2, "mean": Fraction(1125, 8)}
    return graph, base, derived, expected


def test_controlled_depth_and_minimum_files_separate():
    graph, base, derived, expected = fixture()
    catalog = {
        "sold": {"kind": "base", "cross_files": ["excel"]},
        "available": {"kind": "base", "cross_files": ["excel"]},
        "revenue": {"kind": "base", "cross_files": ["ppt"]},
        "occupancy": {"kind": "derived", "cross_files": ["excel"]},
        "adr": {"kind": "derived", "cross_files": ["ppt"]},
        "policy_gate": {"kind": "rule", "cross_files": ["word"]},
        "occupancy_floor": {"kind": "policy", "cross_files": ["word"]},
        "adr_floor": {"kind": "policy", "cross_files": ["word"]},
    }
    for entry in catalog.values():
        entry["central_files"] = ["excel"]
    variants = choose_layouts(graph, {**base, **derived}, catalog, expected, file_names=("excel", "word", "ppt"))
    assert {key: (form["audit"]["minimum_depth"], form["audit"]["minimum_files"]) for key, form in variants.items()} == {
        "shallow_concentrated": (3, 1), "shallow_cross_file": (3, 3),
        "deep_concentrated": (4, 1), "deep_cross_file": (4, 3),
    }
    for form in variants.values():
        report = audit_layout(graph, form["files"], expected)
        assert len(report["subsets"]) == 8
        assert report["passed"]
        assert report["closed_paths"]["mean"]["minimum_depth"] == report["minimum_depth"]


def test_revpar_shorter_path_detected_and_algebraic_loop_not_counted():
    graph, base, derived, _ = fixture()
    solution = solve_public(graph, base, targets=["revpar"])
    paths = closed_paths(solution, base, ["revpar"])["revpar"]
    assert solution["minimum_depth"] == paths["minimum_depth"] == 1
    assert {(route["depth"], route["operator_count"]) for route in paths["paths"]} == {(1, 1), (2, 3)}
    assert paths["minimal_supports"] == [
        {"observations": ["available", "revenue"], "rule_evidence": [], "minimum_depth": 1}
    ]
    shallow = solve_public(graph, derived, targets=["revpar"])
    assert shallow["proofs"]["revpar"]["observations"] == {"adr", "occupancy"}


def test_guarded_and_shortcut_remains_one_step_even_if_both_inputs_known():
    graph, base, _, _ = fixture()
    base.update({"occupied": frozenset()})
    del base["occupancy_floor"]
    solution = solve_public(graph, base, ["policy_gate"], ["qualified"])
    assert solution["proofs"]["qualified"]["value"] == set()
    assert solution["proofs"]["qualified"]["depth"] == 1
    assert solution["proofs"]["qualified"]["shortcut"] == "empty-set short-circuit"


def test_public_copy_of_target_invalidates_depth_even_if_answer_correct():
    graph, base, _, expected = fixture()
    files = {"excel": {"observations": {**base, "qualified": {"A", "C"}}, "rules": ["policy_gate"]}}
    report = audit_layout(graph, files, expected)
    assert not report["passed"]
    assert report["proof"]["direct_answer_leaks"] == ["qualified"]


def test_actual_counterworld_is_checked_against_every_available_fact():
    graph, base, derived, expected = fixture()
    files = {
        "excel": {"observations": {"occupancy": derived["occupancy"]}},
        "ppt": {"observations": {"adr": derived["adr"]}},
        "word": {"observations": {"occupancy_floor": base["occupancy_floor"], "adr_floor": base["adr_floor"]},
                 "rules": ["policy_gate"]},
    }
    world_observations = {**derived, "occupancy_floor": "7/10", "adr_floor": 130}
    world = {"id": "alternative_threshold", "observations": world_observations, "rules": ["policy_gate"],
             "answer": {"qualified": {"C"}, "count": 1, "mean": Fraction(525, 4)}}
    report = audit_layout(graph, files, expected, worlds=[world])
    without_word = next(subset for subset in report["subsets"] if subset["files"] == ["excel", "ppt"])
    assert without_word["counterworld"]["changed_answers"]["qualified"]["alternative"] == {"C"}
    without_ppt = next(subset for subset in report["subsets"] if subset["files"] == ["excel", "word"])
    assert without_ppt["counterworld"] is None
