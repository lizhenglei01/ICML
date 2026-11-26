"""Compile authored metrics and synthetic policies, then search valid revisions."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from functools import lru_cache
from graphlib import CycleError, TopologicalSorter
import operator
import random
import re

import sympy as sp

from .business_definitions import DEFINITIONS, SOURCES, canonical, units as registered_units
from .engine import units_after
from .hotel_visibility import _equal, solve_public
from .scenario_author import SEMANTICS


OPS = {"ADD": operator.add, "SUB": operator.sub, "MUL": operator.mul, "DIV": operator.truediv}
COMPARE = {"GE": operator.ge, "GT": operator.gt, "LE": operator.le, "LT": operator.lt}
SYMBOL = {"ADD": "+", "SUB": "−", "MUL": "×", "DIV": "÷",
          "GE": "≥", "GT": ">", "LE": "≤", "LT": "<"}
PLACES = 4


def guard(ok, message):
    if not ok:
        raise ValueError(message)


def identifier(value):
    guard(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value),
          f"Invalid identifier {value!r}")
    return value


def label(value):
    guard(isinstance(value, str) and 0 < len(value.strip()) <= 150
          and not any(c in value for c in ("\n", "\r", " — ")), "Invalid evidence label")
    return value.strip()


def number(value):
    guard(type(value) in (str, int, float) and len(str(value)) <= 24, "Invalid finite number")
    return Fraction(str(value))


def units(unit):
    guard(isinstance(unit, dict) and len(unit) <= 3, "Invalid unit dimensions")
    standard, custom = {}, {}
    for name, power in unit.items():
        guard(identifier(name.lower()) and type(power) is int and 0 < abs(power) <= 3,
              "Invalid unit dimension or exponent")
        if name in {"CNY", "room_night", "room", "day", "item", "complaint"}:
            standard[name] = power
        else:
            custom[name] = power
    result = registered_units(standard)
    guard(not result.keys() & custom.keys(), "Unit aliases collide")
    return {**result, **custom}


def _indexed(rows, minimum, maximum):
    guard(isinstance(rows, list) and minimum <= len(rows) <= maximum, "Invalid number of records")
    indexed = {identifier(row["id"]): deepcopy(row) for row in rows}
    guard(len(indexed) == len(rows), "Repeated node identifier")
    return indexed


def fields(value, *names):
    guard(isinstance(value, dict) and set(value) == set(names), f"Expected only fields {', '.join(names)}")


def observations(proposal):
    fields(proposal, "title", "entity_label", "period", "entities", "observations", "constraints")
    base = _indexed(proposal["observations"], 3, 5)
    entities = [label(e) for e in proposal["entities"]]
    guard(len(entities) == len(set(entities)) == 6 and all("," not in e and len(e) <= 42 for e in entities),
          "Expected six distinct, short entity names")
    guard(re.fullmatch(r"20\d{2}-Q[1-4]", proposal["period"]), "Invalid reporting period")
    for spec in base.values():
        fields(spec, "id", "label", "unit", "concept", "min", "max", "step")
        spec["label"] = label(spec["label"])
        units(spec["unit"])
        lo, hi, step = (spec[key] for key in ("min", "max", "step"))
        guard(all(type(x) is int for x in (lo, hi, step)) and
              0 <= lo < hi <= 10**9 and 0 < step <= hi - lo, f"Invalid domain for {spec['id']}")
        concept = spec["concept"]
        if concept is not None:
            guard(canonical(concept) in DEFINITIONS, f"Unconfirmed observed concept {concept}")
            guard(units(spec["unit"]) == DEFINITIONS[canonical(concept)]["unit"],
                  f"Unit disagrees with standard definition of {concept}")
    guard(0 <= len(proposal["constraints"]) <= 8, "Too many input constraints")
    for check in proposal["constraints"]:
        fields(check, "left", "op", "right")
        a, b = check["left"], check["right"]
        guard(a in base and b in base and a != b and check["op"] in COMPARE and
              units(base[a]["unit"]) == units(base[b]["unit"]), "Invalid input constraint")
    return {"title": label(proposal["title"]), "entity_label": label(proposal["entity_label"]),
            "period": proposal["period"], "entities": entities, "base": base,
            "constraints": deepcopy(proposal["constraints"])}


def term(rule, known):
    op, args = rule["op"], rule["args"]
    guard(set(rule) == {"op", "args"} and op in OPS and isinstance(args, list)
          and len(args) == 2 and all(arg in known for arg in args), "Invalid formula structure or references")
    return op, args


def verify_metrics(base, forward):
    fields(forward, "metrics", "backward_target")
    metrics = _indexed(forward["metrics"], 2, 4)
    known = {**base["base"], **metrics}
    guard(len(known) == len(base["base"]) + len(metrics), "Metric shadows a base observation")
    guard(forward["backward_target"] in metrics, "Backward target is missing")
    dependencies = {key: term(spec["formula"], known)[1] for key, spec in metrics.items()}
    try:
        order = tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic arithmetic dependency") from exc
    guide, formula_expressions, validation = {}, {}, {}
    # Use separate raw symbols for the formula and for an independently registered
    # meaning. Standard concepts use the external-definition registry when present.
    source_symbols = {key: sp.Symbol(key, positive=spec["min"] > 0, real=True)
                      for key, spec in base["base"].items()}
    standard_expressions = {key: (
        DEFINITIONS[canonical(spec["concept"])]["expression"] if spec["concept"] else source_symbols[key])
        for key, spec in base["base"].items()}
    formula_expressions.update(source_symbols)
    guide.update(source_symbols)
    part_total = {(r["left"], r["right"]) for r in base["constraints"] if r["op"] in ("LE", "LT")}
    part_total.update((r["right"], r["left"]) for r in base["constraints"] if r["op"] in ("GE", "GT"))
    for key in order:
        if key not in metrics:
            continue
        spec = metrics[key]
        fields(spec, "id", "label", "unit", "concept", "semantics", "operands", "formula")
        spec["label"] = label(spec["label"])
        kind = spec["semantics"]
        op, args = term(spec["formula"], known)
        guard(kind in SEMANTICS and isinstance(spec["operands"], list) and len(spec["operands"]) == 2
              and all(i in guide for i in spec["operands"]),
              f"Unsupported or cyclic semantic definition for {key}")
        a, b = spec["operands"]
        if kind == "share":
            guard(a in base["base"] and b in base["base"] and (a, b) in part_total,
                  f"Share {key} needs a checked part-total constraint on raw inputs")
        if kind in {"share", "ratio"}:
            guard(units(known[a]["unit"]) == units(known[b]["unit"]), f"{kind} must compare the same unit")
        if kind == "per_unit":
            guard(bool(units(known[b]["unit"])), f"Per-unit denominator of {key} has no unit")
        expected_op = SEMANTICS[kind]
        if expected_op == "DIV":
            guard(b in base["base"] and base["base"][b]["min"] > 0,
                  f"Denominator of {key} is not a guaranteed positive raw quantity")
        guard(all(arg in formula_expressions for arg in args), f"Cyclic arithmetic inputs of {key}")
        actual = OPS[op](*(formula_expressions[arg] for arg in args))
        expected = OPS[expected_op](guide[a], guide[b])
        guard(sp.cancel(actual - expected) == 0, f"Formula for {key} disagrees with {kind} of its declared operands")
        guard(units_after(op, [units(known[i]["unit"]) for i in args]) == units(spec["unit"])
              == units_after(expected_op, [units(known[a]["unit"]), units(known[b]["unit"])]),
              f"Wrong unit for {key}")
        semantics = {"kind": kind, "operands": [a, b], "scope": "same entity and reporting period"}
        actual_standard = OPS[op](*(standard_expressions[i] for i in args))
        if spec["concept"] is not None:
            concept = canonical(spec["concept"])
            guard(concept in DEFINITIONS, f"Unconfirmed objective business concept {spec['concept']}")
            definition = DEFINITIONS[concept]
            guard(definition["unit"] == units(spec["unit"]) and
                  sp.cancel(actual_standard - definition["expression"]) == 0,
                  f"Formula for {key} disagrees with the independently registered definition")
            semantics.update(authority="registered_definition", concept=concept, source=SOURCES[definition["source"]])
            validation[key] = semantics
        else:
            semantics.update(authority="synthetic_indicator",
                             source="Literal arithmetic definition over explicitly scoped observations")
            semantics["literal_operator"] = expected_op
            validation[key] = semantics
        formula_expressions[key], guide[key], standard_expressions[key] = actual, expected, actual_standard
    return metrics, validation, formula_expressions


def add_backward(base, metrics, expressions, target, proposal):
    fields(proposal, "paths")
    known = {**base["base"], **metrics}
    primary = metrics[target]["formula"]
    verified, accepted, rejected = [], [], []
    guard(1 <= len(proposal["paths"]) <= 6, "Missing backward path candidates")
    for candidate in proposal["paths"]:
        try:
            op, args = term(candidate, known)
            # Reject direct and indirect cycles in additional productions.
            graph = {key: set(spec["formula"]["args"]) for key, spec in metrics.items()}
            graph[target].update(args)
            tuple(TopologicalSorter(graph).static_order())
            value = OPS[op](*(expressions[i] for i in args))
            guard(sp.cancel(value - expressions[target]) == 0
                  and units_after(op, [units(known[i]["unit"]) for i in args]) == units(metrics[target]["unit"]),
                  "Backward formula fails exact equivalence or unit verification")
            if candidate != primary and candidate not in accepted:
                accepted.append(candidate)
        except (KeyError, TypeError, ValueError, CycleError, ZeroDivisionError) as exc:
            rejected.append({"candidate": candidate, "reason": str(exc)})
            continue
        # A directly rediscovered path is valid even when it adds no new edge.
        if candidate not in verified:
            verified.append(candidate)
    guard(bool(verified), f"No independently valid backward route for {target}: {rejected}")
    return {"target": target, "verified_paths": verified, "additional_paths": accepted, "rejected_paths": rejected}


def compile_graph(base, metrics, meanings, backward, proposal, *, allocation_mode=None):
    fields(proposal, "conditions", "selection", "reports",
           *(name for name in ("groups", "allocation") if name in proposal))
    conditions = _indexed(proposal["conditions"], 2, 4)
    groups = _indexed(proposal.get("groups", []), 0, 3)
    reports = _indexed(proposal["reports"], 1, 2)
    selection = deepcopy(proposal["selection"])
    fields(selection, "id", "label", "op", "inputs")
    selection_id = identifier(selection["id"])
    label(selection["label"])
    logic = {**groups, selection_id: selection}
    for group in logic.values():
        fields(group, "id", "label", "op", "inputs")
        label(group["label"])
        inputs = group["inputs"]
        guard(group["op"] in ("AND", "OR") and isinstance(inputs, list) and 2 <= len(inputs) <= 4
              and len(set(inputs)) == len(inputs) and set(inputs) <= conditions.keys() | logic.keys(),
              "Invalid policy composition")
    try:
        order = tuple(TopologicalSorter({k: v["inputs"] for k, v in logic.items()}).static_order())
    except CycleError as exc:
        raise ValueError("Policy logic has a cycle") from exc
    reached = {selection_id}
    for key in reversed(order):
        if key in reached and key in logic:
            reached.update(logic[key]["inputs"])
    guard(reached == conditions.keys() | logic.keys(), "Policy has unused conditions or groups")
    guard(len({r["metric"] for r in conditions.values()}) == len(conditions), "Policy conditions need distinct metrics")
    nodes = {key: {"kind": "base", "label": value["label"], "unit": units(value["unit"])}
             for key, value in base["base"].items()}
    nodes.update({key: {"kind": "derived", "label": value["label"], "unit": units(value["unit"]),
                        "meaning": meanings[key]} for key, value in metrics.items()})
    allocation = deepcopy(proposal.get("allocation"))
    guard((allocation is None and allocation_mode is None)
          or (allocation is not None and allocation_mode in {"ALLOCATE", "INDIVIDUAL_LIMIT"}),
          "Allocation needs an explicit decision mode")
    allocation_ids = ["resource_limit", "candidate_order", "screened_candidates"] if allocation else []
    all_ids = list(nodes) + list(conditions) + list(groups) + list(reports) + [selection_id, "policy_gate", *allocation_ids]
    guards = ["threshold_" + key for key in conditions]
    guard(len(set(all_ids + guards)) == len(all_ids + guards), "Policy reuses a reserved identifier")
    known = {**base["base"], **metrics}
    productions = []

    def add(key, op, inputs, *, alternative="", requires=(), decimal_places=None):
        rule = {"id": key + alternative, "output": key, "op": op, "inputs": list(inputs)}
        if requires:
            rule["requires"] = list(requires)
        if decimal_places:
            rule["decimal_places"] = decimal_places
        productions.append(rule)
        return rule

    for key, metric in metrics.items():
        op, args = term(metric["formula"], known)
        add(key, op, args, decimal_places=PLACES)
    for i, path in enumerate(backward["additional_paths"]):
        add(backward["target"], *term(path, known), alternative=f"_alternate_{i}", decimal_places=PLACES)
    for key, entry in conditions.items():
        fields(entry, "id", "metric", "op", "threshold")
        metric = entry["metric"]
        guard(metric in metrics and entry["op"] in COMPARE, f"Invalid policy condition {key}")
        entry["threshold"] = number(entry["threshold"])
        guard(round(entry["threshold"], PLACES) == entry["threshold"], "Threshold exceeds report precision")
        if meanings[metric]["kind"] == "share":
            guard(0 <= entry["threshold"] <= 1, "Share threshold is outside [0, 1]")
        threshold = "threshold_" + key
        nodes[threshold] = {"kind": "policy", "label": f"{nodes[metric]['label']}门槛", "unit": nodes[metric]["unit"]}
        nodes[key] = {"kind": "condition", "label": f"{nodes[metric]['label']}符合门槛", "unit": {}}
        add(key, "COMPARE", [metric, threshold], requires=("policy_gate",))["comparison"] = entry["op"]
    nodes["policy_gate"] = {"kind": "rule", "label": "合成复核政策", "unit": {}}
    for key in order:
        if key not in logic:
            continue
        entry = logic[key]
        output = "screened_candidates" if allocation and key == selection_id else key
        nodes[output] = {"kind": "selection" if output == selection_id else "condition",
                         "label": "入选对象" if output == selection_id else entry["label"], "unit": {}}
        add(output, entry["op"], entry["inputs"], requires=("policy_gate",))
    if allocation:
        fields(allocation, "priority", "order", "demand", "limit", "limit_label")
        priority, demand = allocation["priority"], allocation["demand"]
        guard(priority in metrics and demand in base["base"] and allocation["order"] in {"ASC", "DESC"},
              "Invalid ranking or resource metric")
        limit = number(allocation["limit"])
        guard(base["base"][demand]["min"] > 0 and nodes[demand]["unit"] and limit > 0
              and limit.denominator == 1 and limit <= 10**10, "Invalid resource amount or budget")
        allocation["limit"] = limit
        nodes["resource_limit"] = {"kind": "policy", "label": label(allocation["limit_label"]),
                                   "unit": nodes[demand]["unit"]}
        nodes["candidate_order"] = {"kind": "condition", "label": "候选排序", "unit": {}}
        nodes[selection_id] = {"kind": "selection", "label": "入选对象", "unit": {}}
        add("candidate_order", "RANK", ["screened_candidates", priority], requires=("policy_gate",))["order"] = allocation["order"]
        candidates = "candidate_order" if allocation_mode == "ALLOCATE" else "screened_candidates"
        add(selection_id, allocation_mode, [candidates, demand, "resource_limit"], requires=("policy_gate",))
    for key, report in reports.items():
        fields(report, "id", "op", "metric", *(('scope',) if 'scope' in report else ()))
        op, metric = report["op"], report["metric"]
        scope = report.get("scope", selection_id)
        guard(scope in nodes and nodes[scope]["kind"] in {"condition", "selection"}
              and scope != "candidate_order", "Report scope must be a named group or selection")
        group_name = "入选对象" if scope == selection_id else nodes[scope]["label"]
        guard((op == "COUNT" and metric is None) or (op in {"AVG", "SUM"} and metric in known),
              f"Invalid report operation {op} for {key}")
        if op == "COUNT":
            nodes[key] = {"kind": "report", "label": f"{group_name}数量", "unit": {"entity": 1}}
        else:
            name = "等权平均" if op == "AVG" else "合计"
            nodes[key] = {"kind": "report", "label": f"{group_name}的{nodes[metric]['label']}{name}", "unit": nodes[metric]["unit"]}
        add(key, op, [scope] if op == "COUNT" else [scope, metric],
            decimal_places=PLACES if op in {"AVG", "SUM"} else None)
    guard(len({r["id"] for r in productions}) == len(productions), "Duplicate production id")
    policy = {"origin": "LLM-authored synthetic policy", "conditions": conditions,
              "selection": selection, "reports": reports}
    if groups:
        policy["groups"] = {key: groups[key] for key in order if key in groups}
    if allocation:
        policy["allocation"] = {**allocation, "mode": allocation_mode}
    return {**base, "nodes": nodes, "metrics": metrics, "productions": productions, "policy": policy,
            "targets": [selection_id, *reports], "backward": backward}


def _round(value):
    with localcontext() as context:
        context.prec = 56
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return Fraction(decimal.quantize(Decimal(1).scaleb(-PLACES), rounding=ROUND_HALF_EVEN))


def thresholds(graph):
    policy = graph["policy"]
    result = {"threshold_" + key: rule["threshold"] for key, rule in policy["conditions"].items()}
    if allocation := policy.get("allocation"):
        result["resource_limit"] = allocation["limit"]
    return result


def valid_row(graph, row):
    for key, spec in graph["base"].items():
        value = row[key]
        if not spec["min"] <= value <= spec["max"] or (value - spec["min"]) % spec["step"]:
            return False
    return all(COMPARE[check["op"]](row[check["left"]], row[check["right"]])
               for check in graph["constraints"])


def row_metrics(graph, row):
    @lru_cache
    def evaluate(key):
        if key in row:
            return Fraction(row[key])
        spec = graph["nodes"][key]["meaning"]
        x, y = (evaluate(arg) for arg in spec["operands"])
        kind = spec["kind"]
        if kind == "total":
            n = x + y
        elif kind == "difference":
            n = x - y
        elif kind == "product":
            n = x * y
        else:
            n = x / y
        return _round(n)

    return {key: evaluate(key) for key in graph["metrics"]}


def reference(graph, observations):
    """Independent row interpreter for semantic contracts and policy descriptions."""
    policy = graph["policy"]
    entities = graph["entities"]
    values = {key: {} for key in graph["metrics"]}
    conditions = {key: set() for key in policy["conditions"]}
    for entity in entities:
        row = {key: Fraction(observations[key][entity]) for key in graph["base"]}
        guard(valid_row(graph, row), f"Observation constraints fail for {entity}")

        measures = row_metrics(graph, row)
        for key in values:
            values[key][entity] = measures[key]
        for key, rule in policy["conditions"].items():
            if COMPARE[rule["op"]](measures[rule["metric"]], observations["threshold_" + key]):
                conditions[key].add(entity)
    selection = policy["selection"]
    out = {**observations, **values, **conditions}
    for rule in (*policy.get("groups", {}).values(), selection):
        candidates = [entity for entity in entities if
                      (all if rule["op"] == "AND" else any)(entity in out[key] for key in rule["inputs"])]
        out[rule["id"]] = set(candidates)
    members = out[selection["id"]]
    if allocation := policy.get("allocation"):
        out["screened_candidates"] = members
        score = values[allocation["priority"]]
        direction = allocation["order"] == "DESC"
        ranking = tuple(sorted(sorted(members), key=score.__getitem__, reverse=direction))
        out["candidate_order"] = ranking
        budget = observations["resource_limit"]
        demand = observations[allocation["demand"]]
        accepted = []
        for name in ranking:
            spent = sum(demand[item] for item in accepted) if allocation["mode"] == "ALLOCATE" else 0
            if spent + demand[name] <= budget:
                accepted.append(name)
        members = out[selection["id"]] = set(accepted)
    for key, report in policy["reports"].items():
        group = out[report.get("scope", selection["id"])]
        if report["op"] == "COUNT":
            out[key] = len(group)
        else:
            guard(group or report["op"] != "AVG", "Undefined mean on an empty selection")
            metric = out[report["metric"]]
            total = sum((metric[e] for e in group), Fraction(0))
            out[key] = _round(total / len(group) if report["op"] == "AVG" else total)
    return out


def execute_verified(graph, observations):
    expected = reference(graph, observations)
    proof = solve_public(graph, observations, ["policy_gate"], list(expected))
    guard(not proof["missing_targets"] and not proof["conflicts"] and
          all(_equal(expected[key], result["value"]) for key, result in proof["proofs"].items()),
          "Independent interpreter disagrees with graph execution")
    return expected


def updated(observations, event):
    node, entity = event["node_id"], event["entity"]
    guard(observations[node][entity] == event["before"], "Event does not match initial input")
    return {**observations, node: {**observations[node], entity: event["after"]}}


def domain_choices(spec, old, rng, anchors=()):
    low, high, step = (spec[k] for k in ("min", "max", "step"))
    domain = range(low, high + 1, step)
    samples = {domain[0], domain[-1], *rng.sample(domain, min(22, len(domain)))}
    for anchor in (old, *anchors):
        nearest = low + ((anchor - low) // step) * step
        samples.update(value for value in (nearest - step, nearest, nearest + step) if value in domain)
    return sorted(samples - {old}, key=lambda value: (abs(value - old), value))


def nontrivial(graph, state):
    selected = state[graph["targets"][0]]
    conditions = (state[key] for key in graph["policy"]["selection"]["inputs"])
    return 0 < len(selected) < len(graph["entities"]) and all(
        0 < len(members) < len(graph["entities"]) and members != selected for members in conditions)


def row_pool(graph, rng):
    """Generate rows from typed bounds, then sample each policy outcome separately."""
    base = graph["base"]
    conditions = graph["policy"]["conditions"].values()
    buckets = {}
    seen = set()

    def consider(row):
        identity = tuple(row.values())
        if identity in seen or not valid_row(graph, row):
            return
        seen.add(identity)
        values = row_metrics(graph, row)
        mask = tuple(COMPARE[r["op"]](values[r["metric"]], r["threshold"]) for r in conditions)
        bucket = buckets.setdefault(mask, [])
        if len(bucket) < 60:
            bucket.append(row)

    for _ in range(160):
        row = {key: rng.randrange(spec["min"], spec["max"] + 1, spec["step"]) for key, spec in base.items()}
        consider(row)
        # Draw outlier and near-boundary cases without using domain-specific
        # branches. Counts constrained by other counts can land on either side.
        for key, spec in base.items():
            for value in domain_choices(spec, row[key], rng, row.values())[::4]:
                consider({**row, key: value})
    for _ in range(4):
        for trials in list(buckets.values()):
            for row in trials[:24]:
                for key, spec in base.items():
                    for value in domain_choices(spec, row[key], rng, row.values())[::3]:
                        consider({**row, key: value})
    return buckets


def revisions(graph, seed, statistics, *, worlds=160):
    rng = random.Random(seed)
    entities = graph["entities"]
    base = graph["base"]
    buckets = row_pool(graph, rng)
    statistics["row_strata"] = {"".join(map(lambda bit: str(int(bit)), mask)): len(rows) for mask, rows in buckets.items()}
    necessary = [(True, False), (False, True),
                 (True, True) if graph["policy"]["selection"]["op"] == "AND" else (False, False)]
    guard(all(mask in buckets for mask in necessary), "Policy has unreachable independent outcomes in the sampled domain")
    for _ in range(worlds):
        rows = [rng.choice(buckets[mask]) for mask in necessary]
        remainder = [mask for mask in buckets if mask != necessary[-1]]
        rows.extend(rng.choice(buckets[rng.choice(remainder)]) for _ in range(len(entities) - len(rows)))
        rng.shuffle(rows)
        observations = {key: {entity: row[key] for entity, row in zip(entities, rows, strict=True)} for key in base}
        observations.update(thresholds(graph))
        statistics["initial_data_candidates"] += 1
        try:
            old = reference(graph, observations)
        except (ValueError, ZeroDivisionError):
            continue
        if not nontrivial(graph, old):
            continue
        # Prioritize amendments that add a member, so the old report does
        # not accidentally reveal that member's old derivation inputs.
        for entity in entities:
            if entity in old[graph["targets"][0]]:
                continue
            for key, spec in base.items():
                old_value = observations[key][entity]
                anchors = [values[entity] for node, values in observations.items() if node in base]
                for candidate in domain_choices(spec, old_value, rng, anchors):
                    event = {"id": "review-correction", "node_id": key, "entity": entity,
                             "before": old_value, "after": candidate, "period": graph["period"]}
                    corrected = updated(observations, event)
                    statistics["event_candidates"] += 1
                    try:
                        after = reference(graph, corrected)
                    except (ValueError, ZeroDivisionError):
                        continue
                    if (entity not in after[graph["targets"][0]] or not nontrivial(graph, after)
                            or not any(old[k] != after[k] for k in graph["targets"][1:])):
                        continue
                    yield observations, corrected, event
