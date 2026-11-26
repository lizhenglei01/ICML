"""Build input-matched individual-limit and shared-resource decision tasks."""
from __future__ import annotations

import random

from .hotel_evidence import native_bundles
from .hotel_graph import save_json
from .hotel_office import build_layout, native_controls
from .hotel_visibility import audit_layout
from .scenario_bench import delta, draw, instruction, office_slots, texts
from .scenario_graph import (compile_graph, domain_choices, execute_verified, guard, reference,
                             row_pool, thresholds, updated)


MODES = {"individual": "INDIVIDUAL_LIMIT", "shared_pool": "ALLOCATE"}


def find_pair(graphs, seed, stats):
    """Keep the correction local and the priority fixed. Require downstream displacement."""
    graph = graphs["shared_pool"]
    allocation = graph["policy"]["allocation"]
    demand = allocation["demand"]
    roster = graph["targets"][0]
    entities = graph["entities"]
    rng = random.Random(seed)
    buckets = row_pool(graph, rng)
    stats["row_strata"] = {"".join(str(int(bit)) for bit in mask): len(rows) for mask, rows in buckets.items()}
    rules = graph["policy"]

    def passes(mask):
        flags = dict(zip(rules["conditions"], mask, strict=True))
        for rule in (*rules.get("groups", {}).values(), rules["selection"]):
            logic = all if rule["op"] == "AND" else any
            flags[rule["id"]] = logic(flags[key] for key in rule["inputs"])
        return flags[roster]

    allowed, rejected = [], []
    for mask, rows in buckets.items():
        (allowed if passes(mask) else rejected).extend(rows)
    guard(allowed and rejected, "Screening needs both eligible and ineligible rows")
    guard(len({row[demand] for row in allowed}) >= 3, "Resource demand needs variation among screened candidates")
    for _ in range(200):
        rows = rng.choices(allowed, k=4) + rng.choices(rejected, k=len(entities) - 4)
        rng.shuffle(rows)
        basis = {key: {entity: row[key] for entity, row in zip(entities, rows, strict=True)} for key in graph["base"]}
        basis.update(thresholds(graph))
        stats["initial_data_candidates"] += 1
        try:
            old = reference(graph, basis)
        except (ValueError, ZeroDivisionError):
            continue
        if not 2 <= len(old[roster]) < len(old["screened_candidates"]):
            continue
        for entity in sorted(old[roster]):
            current = basis[demand][entity]
            anchors = [allocation["limit"], *basis[demand].values()]
            for candidate in domain_choices(graph["base"][demand], current, rng, anchors):
                stats["event_candidates"] += 1
                event = {"id": "review-correction", "node_id": demand, "entity": entity,
                         "before": current, "after": candidate, "period": graph["period"]}
                revised = updated(basis, event)
                try:
                    after = reference(graph, revised)
                except (ValueError, ZeroDivisionError):
                    continue
                gained = after[roster] - old[roster]
                lost = old[roster] - after[roster]
                if (not gained or not lost or entity in gained | lost
                        or old["candidate_order"] != after["candidate_order"]):
                    continue
                states = {mode: (execute_verified(model, basis), execute_verified(model, revised))
                          for mode, model in graphs.items()}
                x, y = states["individual"]
                guard(x[roster] == y[roster], "Local-only policy changed when all individual demands still fit")
                return event, states
    raise ValueError("No one-record correction both displaces and admits other candidates at fixed priority")


def render_variant(root, graph, old, new, event):
    """Use the same source files, public task and coverage for both decision policies."""
    demand = graph["policy"]["allocation"]["demand"]
    bundles = {
        "excel": {"observations": {key: new[key] for key in graph["base"] if key != demand}, "rules": []},
        "word": {"observations": thresholds(graph), "rules": ["policy_gate"]},
        "ppt": {"observations": {demand: new[demand]}, "rules": []},
    }
    slots, before, after = office_slots(graph, {"files": bundles}, old, new, event)
    native = build_layout(root, slots, before, after, instruction(graph, event), title=graph["title"], ppt_size="long")
    rule, definitions = texts(graph)
    readback = native_bundles(graph, native["before_observations"], include_prior=True,
                              gate_text=rule, formula_text=definitions)
    answers = {key: new[key] for key in graph["targets"]}
    proof = audit_layout(graph, readback, answers, common={"observations": {"event_new_value": event["after"]}, "rules": []})
    guard(proof["passed"] and proof["minimum_files"] == 3, "Readback does not establish a three-file proof")
    controls = native_controls(root, native["oracle"])
    expected_negative = {"unchanged_original", "wrong_corrected_value", "protected_formula_replaced",
                         "chart_cache_corrupted"}
    if old[graph["targets"][0]] != new[graph["targets"][0]]:
        expected_negative.add("unchanged_word")
    guard(expected_negative <= controls.keys() and controls["reference"]["pass"], "Native controls are incomplete")
    private = root / "private"
    save_json(private / "graph.json", graph)
    save_json(private / "truth.json", {"event": event, "nodes": delta(graph, old, new, event),
                                     "before": {key: old[key] for key in graph["targets"]}, "after": answers})
    save_json(private / "visibility.json", proof)
    draw(private, graph, "候选对象、观测和更正事件与另一版本相同。")
    return {"minimum_depth": proof["minimum_depth"], "minimum_files": proof["minimum_files"],
            "controls": {key: controls[key]["pass"] for key in sorted(expected_negative | {"reference"})},
            "reference_grade": native["reference_grade"]["groups"],
            "required_native_objects": len(native["required_native_objects"]),
            "roster_gains": sorted(new[graph["targets"][0]] - old[graph["targets"][0]]),
            "roster_losses": sorted(old[graph["targets"][0]] - new[graph["targets"][0]])}


def build_pair(root, base, metrics, meanings, backward, policy, seed, stats):
    graphs = {variant: compile_graph(base, metrics, meanings, backward, policy, allocation_mode=mode)
              for variant, mode in MODES.items()}
    event, states = find_pair(graphs, seed, stats)
    variants = {key: render_variant(root / key, graph, *states[key], event) for key, graph in graphs.items()}
    guard((root / "individual/public/instruction.md").read_bytes()
          == (root / "shared_pool/public/instruction.md").read_bytes(), "Paired task instructions differ")
    return {"passed": True, "event": event, "search": stats, "variants": variants,
            "controls_scope": "Identical raw inputs, correction, public file roles, screening and task wording. "
                              "Shared consumption makes priority consequential and changes the expected reports and edit count. "
                              "Registered proof only; does not measure observed Agent difficulty."}
