"""Seeded tree sampling -> desired memberships -> constraint-satisfying tables."""
import random
from .engine import execute, depths, validate


RULE_SOURCE = "DocHop section 3.1 and appendix C; independently implemented subset"


def default_metadata():
    labels = [
        ("guest_score", "宾客平均评分 / Guest score", "points"),
        ("service_index", "服务指数 / Service index", "points"),
        ("complaints", "客诉次数 / Complaints", "cases"),
        ("cleaning_index", "清洁指数 / Cleaning index", "points"),
        ("staff_index", "人员保障指数 / Staffing index", "points"),
        ("night_service", "夜间服务次数 / Night services", "visits"),
        ("turnaround", "周转时长 / Turnaround", "minutes"),
        ("energy_index", "能耗指数 / Energy index", "points"),
        ("linen", "布草更换量 / Linen replacements", "items"),
        ("maintenance", "维护次数 / Maintenance", "visits"),
        ("safety_score", "安全评分 / Safety score", "points"),
        ("arrival_service", "抵达服务次数 / Arrival services", "visits"),
    ]
    return {"topic": "酒店运营评审 / Hotel operations review",
            "label": "优选运营组 / Preferred operations group",
            "entities": [f"Hotel {chr(65+i)}" for i in range(12)],
            "metrics": [{"id": k, "label": label, "unit": {unit: 1}, "min": 0, "max": 100}
                        for k, label, unit in labels]}


def sample_skeleton(rng, depth, budget):
    """Depth counts filter/Boolean operators; every leaf filter costs one hop."""
    if depth == 1:
        return {"op": rng.choice(["GE", "LE", "MAX", "MIN"])}
    if budget < depth:
        raise ValueError("Need at least depth distinct metrics")
    other_depth = rng.randint(1, min(depth - 1, budget - depth + 1))
    left_budget = budget - other_depth
    left = sample_skeleton(rng, depth - 1, left_budget)
    used = leaf_count(left)
    right = sample_skeleton(rng, other_depth, budget - used)
    if rng.random() < .5: left, right = right, left
    return {"op": rng.choice(["AND", "OR"]), "children": [left, right]}


def leaf_count(tree):
    return sum(leaf_count(c) for c in tree["children"]) if "children" in tree else 1


def all_tree_nodes(tree):
    yield tree
    for child in tree.get("children", []):
        yield from all_tree_nodes(child)


def assign_memberships(tree, entities, rng, max_attempts=20000):
    """Sample intended sets before generating numbers; require useful branches."""
    universe = set(entities)

    def trial(node):
        if "children" not in node:
            node["desired"] = {e for e in entities if rng.random() < .5}
            return 0 < len(node["desired"]) < len(entities)
        if not all(trial(c) for c in node["children"]): return False
        a, b = [c["desired"] for c in node["children"]]
        result = a & b if node["op"] == "AND" else a | b
        node["desired"] = result
        # Reject inert/redundant Boolean branches on this generated instance.
        return bool(result) and result != universe and result != a and result != b

    for attempt in range(1, max_attempts + 1):
        if trial(tree): return attempt
    raise ValueError("Could not sample discriminative memberships within bounded attempts")


def synthesize_chart(entities, requirements, metric, rng):
    """Finite per-chart CSP. Supports several compatible thresholds on one chart.

    Extremum predicates prescribe ties explicitly; all tied extrema are selected.
    """
    lo, hi = metric.get("min", 0), metric.get("max", 100)
    if isinstance(lo, bool) or not isinstance(lo, int) or not isinstance(hi, int) or hi - lo < 4:
        raise ValueError("This MVP requires bounded integer metric domains of width >= 4")
    extremum = {}
    for req in requirements:
        if req["op"] in {"MAX", "MIN"}:
            extremum[req["op"]] = hi if req["op"] == "MAX" else lo
    values = {}
    for entity in entities:
        candidates = []
        for value in range(lo, hi + 1):
            valid = True
            for req in requirements:
                expected = entity in req["members"]
                if req["op"] == "GE": actual = value >= req["threshold"]
                elif req["op"] == "LE": actual = value <= req["threshold"]
                else: actual = value == extremum[req["op"]]
                valid &= actual == expected
            if valid: candidates.append(value)
        if not candidates:
            raise ValueError("UNSAT_CHART_CONSTRAINTS")
        values[entity] = rng.choice(candidates)
    return values


def generate(seed=0, depth=3, metadata=None):
    if depth not in {2, 3, 4, 5}: raise ValueError("Supported filter-tree depths: 2..5")
    import copy
    metadata = copy.deepcopy(default_metadata() if metadata is None else metadata)
    if len(set(metadata["entities"])) != len(metadata["entities"]) or len(metadata["entities"]) < 6:
        raise ValueError("At least six unique entities are required")
    metrics = list(metadata["metrics"])
    if len({m["id"] for m in metrics}) != len(metrics) or len(metrics) < depth + 1:
        raise ValueError("Need unique metric IDs and at least depth+1 metrics")
    rng = random.Random(seed)
    rng.shuffle(metrics)
    tree = sample_skeleton(rng, depth, min(depth + 2, len(metrics) - 1))
    attempts = assign_memberships(tree, metadata["entities"], rng)
    nodes, facts, constraints = [], {}, []
    metric_index = 0

    def materialize(part):
        nonlocal metric_index
        if "children" in part:
            inputs = [materialize(c) for c in part["children"]]
            key = f"logic_{len(nodes):03d}"
            nodes.append({"id": key, "op": part["op"], "inputs": inputs, "rule_source": RULE_SOURCE})
        else:
            metric = metrics[metric_index]; metric_index += 1
            key = "select_" + metric["id"]
            fact = "metric_" + metric["id"]
            req = {"op": part["op"], "members": set(part["desired"])}
            n = {"id": key, "op": part["op"], "inputs": [fact], "rule_source": RULE_SOURCE}
            if part["op"] in {"GE", "LE"}:
                lo, hi = metric.get("min", 0), metric.get("max", 100)
                req["threshold"] = rng.randint(lo + 1, hi - 1)
                n.update(threshold=req["threshold"], threshold_unit=metric["unit"])
            nodes.append({"id": fact, "op": "FACT", "inputs": [], "unit": metric["unit"], "metric": metric["id"]})
            facts[fact] = synthesize_chart(metadata["entities"], [req], metric, rng)
            nodes.append(n)
            constraints.append({"node_id": key, "fact_id": fact, "op": part["op"],
                                "members": sorted(part["desired"]), **({"threshold": req["threshold"]} if "threshold" in req else {})})
        part["node_id"] = key
        return key

    selection = materialize(tree)
    average_metric = metrics[metric_index]
    average_fact = "metric_" + average_metric["id"]
    nodes.append({"id": average_fact, "op": "FACT", "inputs": [], "unit": average_metric["unit"], "metric": average_metric["id"]})
    facts[average_fact] = {e: rng.randint(average_metric.get("min", 0), average_metric.get("max", 100)) for e in metadata["entities"]}
    nodes += [{"id": "answer_count", "op": "COUNT", "inputs": [selection]},
              {"id": "answer_average", "op": "AVG", "inputs": [selection, average_fact]}]
    graph = {"schema_version": "logic-first/1", "seed": seed, "requested_depth": depth,
             "entities": metadata["entities"], "metadata": metadata, "nodes": nodes, "facts": facts,
             "selection": selection, "outputs": [selection, "answer_count", "answer_average"],
             "constraints": constraints, "membership_sampling_attempts": attempts,
             "provenance": {"tree": "program_sampling", "numbers": "deterministic_constraint_solver",
                            "narrative": "deterministic_templates", "llm_calls": 0}}
    validate(graph)
    state = execute(graph)
    assert depths(graph)[selection] == depth
    assert state[selection] == tree["desired"]
    return graph
