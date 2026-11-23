"""Independent row-wise oracle; does not call the production graph executor."""
import math
from .engine import validate, depths, jsonable


def independent_answers(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    entities = graph["entities"]

    def numeric(key, entity):
        n = nodes[key]
        if n["op"] == "FACT": return graph["facts"][key][entity]
        x, y = [numeric(i, entity) for i in n["inputs"]]
        if n["op"] == "ADD": return x + y
        if n["op"] == "SUB": return x - y
        if n["op"] == "MUL": return x * y
        if n["op"] == "DIV":
            if not y: raise ValueError("zero denominator")
            return x / y
        raise ValueError("not a numeric node")

    def predicate(key, entity):
        n = nodes[key]
        op = n["op"]
        if op == "AND": return all(predicate(i, entity) for i in n["inputs"])
        if op == "OR": return any(predicate(i, entity) for i in n["inputs"])
        values = [numeric(n["inputs"][0], e) for e in entities]
        value = numeric(n["inputs"][0], entity)
        if op == "GE": return value >= n["threshold"]
        if op == "LE": return value <= n["threshold"]
        # Pairwise extremum comparisons deliberately differ from engine max/min.
        if op == "MAX": return all(value >= x for x in values)
        if op == "MIN": return all(value <= x for x in values)
        raise ValueError("not a selection node")

    selected = [e for e in entities if predicate(graph["selection"], e)]
    output = {graph["selection"]: sorted(selected)}
    for target in graph["outputs"]:
        n = nodes[target]
        if n["op"] == "COUNT": output[target] = len(selected)
        elif n["op"] == "AVG":
            if not selected: raise ValueError("empty mean")
            output[target] = sum(numeric(n["inputs"][1], e) for e in selected) / len(selected)
    return output


def compare_answers(actual, expected):
    checks = []
    for key, value in expected.items():
        got = actual.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            passed = isinstance(got, (int, float)) and not isinstance(got, bool) and math.isclose(got, value, rel_tol=1e-10, abs_tol=1e-10)
        else: passed = jsonable(got) == value
        checks.append({"target": key, "passed": passed, "expected": value, "actual": jsonable(got)})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def audit_graph(graph, state):
    validate(graph)
    checks = [{"name": "independent_rowwise_answers", **compare_answers(state, independent_answers(graph))}]
    levels = depths(graph)
    checks.append({"name": "requested_filter_depth", "passed": levels[graph["selection"]] == graph["requested_depth"],
                   "actual": levels[graph["selection"]], "expected": graph["requested_depth"]})
    universe = set(graph["entities"])
    selected = state[graph["selection"]]
    checks.append({"name": "nonempty_proper_target_set", "passed": bool(selected) and selected != universe})
    for req in graph["constraints"]:
        checks.append({"name": "constraint:" + req["node_id"],
                       "passed": state[req["node_id"]] == set(req["members"]),
                       "expected_members": req["members"], "actual_members": sorted(state[req["node_id"]])})
    for n in graph["nodes"]:
        if n["op"] in {"AND", "OR"}:
            value = state[n["id"]]
            checks.append({"name": "nonredundant_branch:" + n["id"],
                           "passed": all(value != state[i] for i in n["inputs"])})
    return {"passed": all(c["passed"] for c in checks), "checks": checks,
            "scope": "Independent row-wise answer calculation; common declared operator semantics remain human-defined. No claim of natural-language semantic certification."}
