"""Typed, deterministic graph execution. No dataset construction in this module."""
import math


CONTRACTS = {
    "FACT": ((), "vector"),
    "GE": (("vector",), "set"), "LE": (("vector",), "set"),
    "MAX": (("vector",), "set"), "MIN": (("vector",), "set"),
    "AND": (("set", "set"), "set"), "OR": (("set", "set"), "set"),
    "COUNT": (("set",), "scalar"), "AVG": (("set", "vector"), "scalar"),
    "ADD": (("vector", "vector"), "vector"),
    "SUB": (("vector", "vector"), "vector"),
    "MUL": (("vector", "vector"), "vector"),
    "DIV": (("vector", "vector"), "vector"),
}


class GraphError(ValueError):
    pass


def units_after(op, units):
    if op in {"ADD", "SUB"}:
        if units[0] != units[1]:
            raise GraphError("UNIT_MISMATCH")
        return units[0]
    out = dict(units[0])
    for key, power in units[1].items():
        out[key] = out.get(key, 0) + power * (-1 if op == "DIV" else 1)
    return {key: power for key, power in out.items() if power}


def validate(graph):
    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise GraphError("DUPLICATE_NODE")
    entities = graph["entities"]
    if len(set(entities)) != len(entities) or not entities:
        raise GraphError("INVALID_ENTITIES")
    done, active = {}, set()

    def visit(key):
        if key in active:
            raise GraphError("CYCLE")
        if key in done:
            return done[key]
        if key not in nodes:
            raise GraphError("MISSING_INPUT")
        node = nodes[key]
        op = node["op"]
        if op not in CONTRACTS:
            raise GraphError("UNKNOWN_OPERATOR")
        expected, dtype = CONTRACTS[op]
        if len(node["inputs"]) != len(expected):
            raise GraphError("ARITY")
        active.add(key)
        incoming = [visit(i) for i in node["inputs"]]
        if tuple(x[0] for x in incoming) != expected:
            raise GraphError("TYPE_MISMATCH")
        unit = {}
        if op == "FACT":
            unit = node["unit"]
            values = graph["facts"].get(key, {})
            if set(values) != set(entities) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in values.values()
            ):
                raise GraphError("INVALID_FACT_VALUES")
        elif op in {"ADD", "SUB", "MUL", "DIV"}:
            unit = units_after(op, [x[1] for x in incoming])
        elif op in {"GE", "LE"}:
            if incoming[0][1] != node["threshold_unit"]:
                raise GraphError("THRESHOLD_UNIT_MISMATCH")
            if not math.isfinite(node["threshold"]):
                raise GraphError("INVALID_THRESHOLD")
        elif op == "COUNT":
            unit = {"entity_count": 1}
        elif op == "AVG":
            unit = incoming[1][1]
        if "unit" in node and node["unit"] != unit:
            raise GraphError("DECLARED_UNIT_MISMATCH")
        done[key] = (dtype, unit)
        active.remove(key)
        return done[key]

    for key in nodes:
        visit(key)
    for target in graph["outputs"]:
        if target not in nodes:
            raise GraphError("MISSING_TARGET")
    return done


def apply(op, args, entities, node):
    if op in {"GE", "LE"}:
        return {e for e in entities if (args[0][e] >= node["threshold"]
                if op == "GE" else args[0][e] <= node["threshold"])}
    if op in {"MAX", "MIN"}:
        extreme = (max if op == "MAX" else min)(args[0].values())
        return {e for e in entities if args[0][e] == extreme}
    if op == "AND":
        return args[0] & args[1]
    if op == "OR":
        return args[0] | args[1]
    if op == "COUNT":
        return len(args[0])
    if op == "AVG":
        if not args[0]:
            raise GraphError("EMPTY_AVERAGE")
        return math.fsum(args[1][e] for e in sorted(args[0])) / len(args[0])
    out = {}
    for e in entities:
        a, b = args[0][e], args[1][e]
        if op == "ADD": out[e] = a + b
        elif op == "SUB": out[e] = a - b
        elif op == "MUL": out[e] = a * b
        elif op == "DIV":
            if b == 0: raise GraphError("ZERO_DIVISOR")
            out[e] = a / b
        else: raise GraphError("UNKNOWN_OPERATOR")
        if not math.isfinite(out[e]):
            raise GraphError("NONFINITE_RESULT")
    return out


def execute(graph, visible=None, targets=None):
    """With visible=None use private base facts; otherwise ONLY visible observations."""
    validate(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    cache = dict(visible or {})

    def evaluate(key):
        if key in cache:
            return cache[key]
        n = nodes[key]
        if n["op"] == "FACT":
            if visible is not None:
                raise GraphError("INSUFFICIENT_EVIDENCE:" + key)
            value = graph["facts"][key]
        else:
            value = apply(n["op"], [evaluate(i) for i in n["inputs"]], graph["entities"], n)
        cache[key] = value
        return value

    for key in (graph["outputs"] if targets is None else targets):
        evaluate(key)
    return cache


def depths(graph, visible=()):
    nodes = {n["id"]: n for n in graph["nodes"]}
    out = dict.fromkeys(visible, 0)

    def depth(key):
        if key in out: return out[key]
        node = nodes[key]
        if node["op"] == "FACT":
            out[key] = 0 if not visible else math.inf
        else:
            out[key] = 1 + max(depth(i) for i in node["inputs"])
        return out[key]
    for key in nodes: depth(key)
    return out


def jsonable(value):
    if isinstance(value, set): return sorted(value)
    if isinstance(value, dict): return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list): return [jsonable(v) for v in value]
    return value
