"""Typed, deterministic operators for the relational Office program builder."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import operator
from typing import Callable


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Column:
    kind: str
    unit: str = ""


@dataclass(frozen=True)
class Schema:
    kind: str
    columns: dict[str, Column] | None = None
    keys: tuple[str, ...] = ()
    field: Column | None = None


@dataclass(frozen=True)
class Operator:
    inputs: tuple[str, ...]
    output: str
    parameters: dict[str, str]
    infer: Callable
    execute: Callable
    describe: Callable
    constraints: str
    variadic: bool = False


COMPARISONS = {"GE": operator.ge, "LE": operator.le, "GT": operator.gt, "LT": operator.lt, "EQ": operator.eq}
SIGNS = {"GE": "≥", "LE": "≤", "GT": ">", "LT": "<", "EQ": "="}


def table(columns, keys):
    return Schema("table", columns, tuple(keys))


def column(schema, key, kind=None):
    require(isinstance(key, str) and key in schema.columns, f"Unknown column {key!r}")
    result = schema.columns[key]
    require(kind is None or result.kind == kind, f"Column {key} must be {kind}")
    return result


def link_schema(inputs, params):
    left, right = inputs
    key = params["on"]
    require(right.keys == (key,) and column(left, key) == column(right, key), "Lookup needs a unique, compatible right key")
    fields = params["fields"]
    require(isinstance(fields, list) and fields and len(fields) == len(set(fields))
            and not set(fields) & left.columns.keys(), "Linked fields must be distinct new columns")
    return table({**left.columns, **{field: column(right, field) for field in fields}}, left.keys)


def join(values, p):
    left, right = values
    key = p["on"]
    index = {row[key]: row for row in right}
    require(len(index) == len(right), "Duplicate lookup key")
    require(all(row[key] in index for row in left), "Missing lookup key")
    return tuple({**row, **{field: index[row[key]][field] for field in p["fields"]}} for row in left)


def lookup_schema(inputs, p):
    return link_schema(inputs, {"on": p["on"], "fields": [p["field"]]})


def lookup(values, p):
    return join(values, {"on": p["on"], "fields": [p["field"]]})


def group_schema(inputs, p):
    (source,) = inputs
    keys = p["by"]
    require(isinstance(keys, list) and 1 <= len(keys) <= 3 and len(keys) == len(set(keys)), "Invalid grouping keys")
    for key in keys:
        require(column(source, key).kind != "number", "Grouping keys must be categorical or object IDs")
    return Schema("groups", source.columns, tuple(keys))


def group_by(values, p):
    groups = defaultdict(list)
    for row in values[0]:
        groups[tuple(row[key] for key in p["by"])].append(row)
    return dict(groups)


def aggregate_schema(inputs, p):
    (source,) = inputs
    output = p["as"]
    require(isinstance(output, str) and output.isidentifier() and output.isascii()
            and output not in source.columns and output != "rank", "Invalid new aggregate column")
    return table({**{key: source.columns[key] for key in source.keys},
                  output: column(source, p["value"], "number")}, source.keys)


def aggregate(values, p, *, mean):
    entries = []
    for key, rows in sorted(values[0].items()):
        total = sum(Fraction(row[p["value"]]) for row in rows)
        value = total / len(rows) if mean else total
        entries.append({**dict(zip(p["keys"], key, strict=True)), p["as"]: value})
    return tuple(entries)


def rank_schema(inputs, p):
    (source,) = inputs
    column(source, p["score"], "number")
    require(source.keys and p["order"] in {"ASC", "DESC"} and "rank" not in source.columns,
            "Rank needs an ordered score and a unique, stable key")
    return Schema("ranked", {**source.columns, "rank": Column("number")}, source.keys)


def rank(values, p):
    rows, = values
    sign = -1 if p["order"] == "DESC" else 1
    ordered = sorted(rows, key=lambda row: (sign * row[p["score"]], *(row[key] for key in p["keys"])))
    return tuple({**row, "rank": i} for i, row in enumerate(ordered, 1))


def top_schema(inputs, p):
    (source,) = inputs
    require(type(p["k"]) is int and 1 <= p["k"] <= 30, "Top-K needs a positive integer K up to 30")
    return table(source.columns, source.keys)


def max_schema(inputs, p):
    result = rank_schema(inputs, {**p, "order": "DESC"})
    return table(result.columns, result.keys)


def filter_schema(inputs, p):
    (source,) = inputs
    column(source, p["field"], "number")
    require(p["comparison"] in COMPARISONS, "Invalid threshold comparison")
    require(type(p["threshold"]) in {int, str} and len(str(p["threshold"])) <= 24, "Invalid exact threshold")
    Fraction(p["threshold"])
    return source


def member_schema(inputs, p):
    source, members = inputs
    require(column(source, p["field"]) == members.field, "Membership must use the same key domain")
    return source


def project_schema(inputs, p):
    result = column(inputs[0], p["field"])
    require(result.kind != "number", "Only identifiers or categories may form a membership set")
    return Schema("set", field=result)


def logic_schema(inputs, _):
    require(all(item == inputs[0] for item in inputs), "Logical inputs must have the same key domain")
    return inputs[0]


def conjunction(values, _):
    return values[0].intersection(*values[1:])


def disjunction(values, _):
    return values[0].union(*values[1:])


OPERATORS = {
    "JOIN": Operator(("table", "table"), "table", {"on": "common key field", "fields": "list of right columns"},
                     link_schema, join, lambda n, p: f"按{p['on']}把{n[1]}的{'、'.join(p['fields'])}关联到{n[0]}。",
                     "Complete many-to-one match; right key unique, no missing keys or overwritten columns."),
    "LOOKUP": Operator(("table", "table"), "table", {"on": "common key field", "field": "one right column"},
                       lookup_schema, lookup, lambda n, p: f"按{p['on']}从{n[1]}查找{p['field']}并填入{n[0]}。",
                       "Single-field JOIN with the same cardinality and type constraints."),
    "GROUP_BY": Operator(("table",), "groups", {"by": "list of categorical fields"}, group_schema, group_by,
                         lambda n, p: f"将{n[0]}按{'、'.join(p['by'])}分组。", "One to three categorical keys."),
    "GROUP_SUM": Operator(("groups",), "table", {"value": "numeric field", "as": "new column"}, aggregate_schema,
                          lambda v, p: aggregate(v, p, mean=False),
                          lambda n, p: f"对{n[0]}中的每组求{p['value']}合计，记为{p['as']}。", "Keep keys and input unit."),
    "GROUP_AVG": Operator(("groups",), "table", {"value": "numeric field", "as": "new column"}, aggregate_schema,
                          lambda v, p: aggregate(v, p, mean=True),
                          lambda n, p: f"对{n[0]}中的每组求{p['value']}算术平均，记为{p['as']}。", "Keep unit; exact arithmetic."),
    "RANK": Operator(("table",), "ranked", {"score": "numeric field", "order": "ASC or DESC"}, rank_schema, rank,
                     lambda n, p: f"将{n[0]}按{p['score']}{'从高到低' if p['order']=='DESC' else '从低到高'}排序，同值按主键 Unicode 顺序。",
                     "Stable unique row keys break equal scores; ranks are sequential."),
    "TOP_K": Operator(("ranked",), "table", {"k": "positive integer"}, top_schema, lambda v, p: v[0][:p["k"]],
                      lambda n, p: f"取{n[0]}的前{p['k']}行。", "Input is the ordered result of RANK."),
    "ARGMAX": Operator(("table",), "table", {"score": "numeric field"}, max_schema,
                       lambda v, p: rank(v, {**p, "order": "DESC"})[:1],
                       lambda n, p: f"取{n[0]}中{p['score']}最高的一行，同值按主键 Unicode 顺序取第一名。",
                       "Empty input returns an empty table."),
    "FILTER_NUM": Operator(("table",), "table", {"field": "numeric field", "comparison": "GE, LE, GT, LT or EQ",
                                                "threshold": "integer or exact numeric string"}, filter_schema,
                           lambda v, p: tuple(row for row in v[0] if COMPARISONS[p["comparison"]](
                               row[p["field"]], Fraction(p["threshold"]))),
                           lambda n, p: f"保留{n[0]}中{p['field']} {SIGNS[p['comparison']]} {p['threshold']} 的记录。",
                           "Threshold shares the field unit."),
    "PROJECT_SET": Operator(("table",), "set", {"field": "identifier or category field"}, project_schema,
                            lambda v, p: frozenset(row[p["field"]] for row in v[0]),
                            lambda n, p: f"从{n[0]}提取去重后的{p['field']}名单。", "Key domain is preserved."),
    "FILTER_IN": Operator(("table", "set"), "table", {"field": "identifier or category field"}, member_schema,
                          lambda v, p: tuple(row for row in v[0] if row[p["field"]] in v[1]),
                          lambda n, p: f"保留{n[0]}中{p['field']}出现在{n[1]}的记录。", "Membership keys share a domain."),
    "AND": Operator(("set",), "set", {}, logic_schema, conjunction, lambda n, _: "同时出现在" + "、".join(n) + "的对象。",
                    "At least two sets of the same domain.", variadic=True),
    "OR": Operator(("set",), "set", {}, logic_schema, disjunction, lambda n, _: "出现在" + "或".join(n) + "任一名单的对象。",
                   "At least two sets of the same domain, inclusive OR.", variadic=True),
    "COUNT": Operator(("set",), "number", {}, lambda *_: Schema("number", field=Column("number")),
                      lambda v, _: len(v[0]), lambda n, _: f"统计{n[0]}中的不同对象数。", "Empty set returns zero."),
}


def contract():
    return {name: {"inputs": list(spec.inputs), "output": spec.output, "parameters": spec.parameters,
                   "constraints": spec.constraints} for name, spec in OPERATORS.items()}


def compile_operator(name, inputs, params):
    require(name in OPERATORS, f"Unknown program operator {name}")
    spec = OPERATORS[name]
    kinds = tuple(item.kind for item in inputs)
    valid = len(kinds) >= 2 and all(kind == spec.inputs[0] for kind in kinds) if spec.variadic else kinds == spec.inputs
    require(valid, f"{name} cannot consume {kinds}")
    require(isinstance(params, dict) and params.keys() == spec.parameters.keys(), f"Invalid parameters for {name}")
    schema = spec.infer(inputs, params)
    runtime = dict(params)
    if name in {"GROUP_SUM", "GROUP_AVG", "RANK", "ARGMAX"}:
        runtime["keys"] = list(inputs[0].keys)
    return schema, runtime
