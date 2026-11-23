"""OR quantity nodes -> AND operator productions -> required input quantities."""
from itertools import product
import json
from .operators import OPERATORS, InvalidGraph, result_unit, unit, apply


def structure(graph, known, target, *, partial=False):
    if set(graph) != {"target", "quantities", "productions"}:
        raise InvalidGraph("GRAPH_SCHEMA", "Graph must contain target, quantities and productions only")
    if graph["target"] != target:
        raise InvalidGraph("TARGET_MISMATCH", "Graph target differs from task target")
    quantities = {}
    if len(graph["quantities"])>32 or len(graph["productions"])>48:
        raise InvalidGraph("GRAPH_LIMIT","Candidate exceeds the bounded verifier size")
    for q in graph["quantities"]:
        if set(q) != {"id", "concept", "unit"} or not isinstance(q["id"], str):
            raise InvalidGraph("QUANTITY_SCHEMA", "Each quantity needs id, concept and unit")
        if q["id"] in quantities:
            raise InvalidGraph("DUPLICATE_QUANTITY", "Duplicate quantity id")
        unit(q["unit"])
        if q["id"] in known and unit(q["unit"]) != 1:
            raise InvalidGraph("GIVEN_UNIT","Supplied money facts cannot be retyped")
        quantities[q["id"]] = q
    if target not in quantities:
        raise InvalidGraph("MISSING_TARGET", "Target quantity must be declared")
    productions, by_output = {}, {}
    available = set(known) | set(quantities)
    for p in graph["productions"]:
        if set(p) != {"id", "output", "op", "inputs"}:
            raise InvalidGraph("PRODUCTION_SCHEMA", "An operator needs id, output, op and ordered inputs")
        if p["id"] in productions:
            raise InvalidGraph("DUPLICATE_PRODUCTION", "Duplicate operator id")
        if p["op"] not in OPERATORS:
            raise InvalidGraph("UNKNOWN_OPERATOR", "Operator not permitted")
        if len(p["inputs"]) != OPERATORS[p["op"]].arity:
            raise InvalidGraph("ARITY", "Incorrect input count")
        if p["output"] not in available or any(i not in available for i in p["inputs"]):
            raise InvalidGraph("UNDECLARED_QUANTITY", "All referenced quantities must be declared or given facts")
        productions[p["id"]] = p
        by_output.setdefault(p["output"], []).append(p)
        units=[unit("money") if i in known else unit(quantities[i]["unit"]) for i in p["inputs"]]
        declared=unit("money") if p["output"] in known else unit(quantities[p["output"]]["unit"])
        if result_unit(p["op"],units)!=declared:
            raise InvalidGraph("UNIT_MISMATCH","Operator output unit disagrees with its declared quantity")
    visited, active = set(), set()
    def visit(q):
        if q in active: raise InvalidGraph("CYCLE", "Dependency graph contains a cycle")
        if q in visited: return
        active.add(q)
        for p in by_output.get(q, []):
            for i in p["inputs"]: visit(i)
        active.remove(q); visited.add(q)
    for q in available: visit(q)
    reachable = set()
    def mark(q):
        if q in reachable: return
        reachable.add(q)
        for p in by_output.get(q, []):
            for i in p["inputs"]: mark(i)
    mark(target)
    unused = sorted(set(quantities) - reachable)
    if unused: raise InvalidGraph("UNUSED_QUANTITY", "Graph contains unreachable quantity nodes")
    unresolved = sorted(q for q in reachable if q not in known and q not in by_output)
    if unresolved and not partial:
        raise InvalidGraph("MISSING_LEAF", "Some leaves are not supplied known facts and have no derivation")
    return quantities, by_output, unresolved


def paths(graph, known, *, limit=64):
    quantities, by_output, _ = structure(graph, known, graph["target"])
    def expand(q):
        choices = [{"quantity": q, "fact": q}] if q in known else []
        for p in by_output.get(q, []):
            for args in product(*(expand(i) for i in p["inputs"])):
                choices.append({"quantity": q, "production": p["id"], "op": p["op"], "args": list(args)})
                if len(choices) > limit:
                    raise InvalidGraph("PATH_LIMIT", "Too many derivation paths to certify")
        return choices
    return expand(graph["target"])


def evaluate_path(tree, quantities, facts, *, symbolic=False):
    if "fact" in tree:
        return facts[tree["fact"]], unit("money")
    evaluated = [evaluate_path(a, quantities, facts, symbolic=symbolic) for a in tree["args"]]
    out_unit = result_unit(tree["op"], [u for _, u in evaluated])
    declared = quantities.get(tree["quantity"], {"unit": "money"})
    if out_unit != unit(declared["unit"]):
        raise InvalidGraph("UNIT_MISMATCH", "Computed unit differs from declared quantity unit")
    return apply(tree["op"], [x for x, _ in evaluated], symbolic=symbolic), out_unit


def path_info(tree):
    if "fact" in tree:
        return {"inputs": {tree["fact"]}, "operators": set(), "depth": 0, "intermediates": set()}
    parts = [path_info(a) for a in tree["args"]]
    return {"inputs": set().union(*(p["inputs"] for p in parts)),
            "operators": {tree["production"]} | set().union(*(p["operators"] for p in parts)),
            "depth": 1 + max(p["depth"] for p in parts),
            "intermediates": {a["quantity"] for a in tree["args"] if "fact" not in a} |
                             set().union(*(p["intermediates"] for p in parts))}


def readable(graph, known):
    by_output = {}
    for p in graph["productions"]: by_output.setdefault(p["output"], []).append(p)
    lines = []
    def walk(q, indent="", active=()):
        lines.append(indent + q + " [OR]")
        if q in known: lines.append(indent + "  ├─ GIVEN " + str(known[q]))
        if q in active:
            lines.append(indent + "  └─ CYCLE"); return
        for p in by_output.get(q, []):
            lines.append(indent + "  └─ " + p["op"] + " [AND; " + p["id"] + "]")
            for i in p["inputs"]: walk(i, indent + "      ", active + (q,))
    walk(graph["target"])
    return "\n".join(lines) + "\n"
