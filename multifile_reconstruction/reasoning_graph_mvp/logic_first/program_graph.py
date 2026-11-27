"""Compile model-authored relational programs and find single-record corrections."""
from __future__ import annotations

from copy import deepcopy
from graphlib import CycleError, TopologicalSorter
import random
import re

from .hotel_visibility import solve_public
from .program_operators import Column, OPERATORS, compile_operator, require, table
from .scenario_graph import fields, identifier, label


def brief(value):
    value = label(value)
    require(len(value) <= 28 and not any(c in value for c in ",，"), "Expected a short display label")
    return value


def distinct_labels(values, size):
    require(isinstance(values, list) and len(values) == size, f"Expected {size} display names")
    values = [brief(name) for name in values]
    require(len(set(values)) == size, "Display names must be distinct")
    return values


def compile_program(raw, level):
    require(level in {3, 4}, "Expected program level 3 or 4")
    fields(raw, "title", "period", "directory", "records", "nodes", "roster", "report")
    require(re.fullmatch(r"20\d{2}-Q[1-4]", raw["period"]), "Invalid reporting period")
    directory, records = deepcopy(raw["directory"]), deepcopy(raw["records"])
    fields(directory, "id", "label", "entity_key", "group_key", "entity_label", "group_label", "entities", "groups")
    fields(records, "id", "label", "row_key", "measures")
    entity, group, row = (identifier(key) for key in (directory["entity_key"], directory["group_key"], records["row_key"]))
    require(len({entity, group, row}) == 3 and "rank" not in {entity, group, row}, "Source field IDs must be distinct")
    directory["entities"] = distinct_labels(directory["entities"], 6)
    directory["groups"] = distinct_labels(directory["groups"], 3)
    for key in ("label", "entity_label", "group_label"):
        directory[key] = brief(directory[key])
    records["label"] = brief(records["label"])
    require(isinstance(records["measures"], list) and 1 <= len(records["measures"]) <= 3, "Expected one to three measures")
    numeric = {}
    for spec in records["measures"]:
        fields(spec, "id", "label", "unit", "min", "max", "step")
        key = identifier(spec["id"])
        require(key not in {entity, group, row, "rank"} and key not in numeric, "Measure ID collides with a source field")
        spec["label"], spec["unit"] = brief(spec["label"]), brief(spec["unit"])
        lo, hi, step = (spec[k] for k in ("min", "max", "step"))
        require(all(type(x) is int for x in (lo, hi, step)) and 0 <= lo < hi <= 10**8
                and 0 < step <= hi - lo, "Invalid sampling bounds")
        numeric[key] = Column("number", spec["unit"])
    directory_id, records_id = identifier(directory["id"]), identifier(records["id"])
    require(directory_id != records_id and "policy_gate" not in {directory_id, records_id}, "Invalid source IDs")
    dimensional = Column("entity:" + entity)
    schemas = {directory_id: table({entity: dimensional, group: Column("group:" + group)}, [entity]),
               records_id: table({row: Column("record:" + row), entity: dimensional, **numeric}, [row])}
    titles = {directory_id: directory["label"], records_id: records["label"]}
    require(isinstance(raw["nodes"], list) and 1 <= len(raw["nodes"]) <= 30, "Expected 1 to 30 program nodes")
    nodes = {}
    for node in raw["nodes"]:
        fields(node, "id", "label", "op", "inputs", "params")
        key = identifier(node["id"])
        require(key not in schemas and key not in nodes and key != "policy_gate", "Duplicate or reserved node ID")
        inputs = node["inputs"]
        require(isinstance(inputs, list) and all(isinstance(value, str) for value in inputs) and len(inputs) == len(set(inputs)),
                "Inputs must name distinct graph nodes")
        titles[key] = brief(node["label"])
        nodes[key] = node
    require(len(set(titles.values())) == len(titles), "Operator labels must be distinct")
    roster, report = raw["roster"], raw["report"]
    require(roster in nodes and report in nodes and nodes[report]["op"] == "COUNT" and nodes[report]["inputs"] == [roster],
            "Report must count the final entity roster")
    require(all(set(node["inputs"]) <= schemas.keys() | nodes.keys() for node in nodes.values()), "Unknown dependency")
    try:
        order = tuple(TopologicalSorter({key: node["inputs"] for key, node in nodes.items()}).static_order())
    except CycleError as exc:
        raise ValueError("Circular program dependency") from exc
    productions, ancestors = [], {key: set() for key in schemas}
    for key in order:
        if key not in nodes:
            continue
        node = nodes[key]
        inputs = node["inputs"]
        schemas[key], params = compile_operator(node["op"], [schemas[arg] for arg in inputs], node["params"])
        ancestors[key] = set(inputs).union(*(ancestors[arg] for arg in inputs))
        productions.append({"id": key, "output": key, "op": node["op"], "inputs": inputs,
                            "requires": ["policy_gate"], "program_parameters": params})
    require(schemas[roster].kind == "set" and schemas[roster].field == dimensional, "Roster must contain entity names")
    require(ancestors[report] == schemas.keys() - {report}, "Every declared node and source must contribute to the report")
    links = {key for key, node in nodes.items() if node["op"] in {"JOIN", "LOOKUP"}
             and {directory_id, records_id} <= ancestors[key]}
    groups = {key for key, node in nodes.items() if node["op"] == "GROUP_BY" and links & ancestors[key]
              and group in node["params"]["by"]}
    aggregates = {key for key, node in nodes.items() if node["op"] in {"GROUP_SUM", "GROUP_AVG"}
                  and groups & ancestors[key]}
    ranked = {key for key, node in nodes.items() if node["op"] in {"RANK", "TOP_K", "ARGMAX"}}
    require(aggregates, "No connected path from object linkage to category aggregation")
    if level == 3:
        require(not ranked and any(node["op"] == "FILTER_NUM" and aggregates & ancestors[key] for key, node in nodes.items()),
                "Level 3 needs a group threshold and no rank")
    else:
        require(any(nodes[key]["op"] in {"TOP_K", "ARGMAX"} and aggregates & ancestors[key] for key in ranked),
                "Level 4 needs ranked group selection after group aggregation")
    graph = {"title": brief(raw["title"]), "period": raw["period"], "level": level,
             "entities": directory["entities"], "targets": [roster, report], "productions": productions,
             "nodes": {key: {"kind": "base" if key not in nodes else "report" if key in {roster, report} else "derived",
                              "label": title, "unit": {}} for key, title in titles.items()},
             "directory": directory, "records": records, "policy_origin": "model-authored synthetic policy"}
    graph['nodes']['policy_gate'] = {'kind': 'rule', 'label': '合成复核政策', 'unit': {}}
    return graph


def evaluate(graph, sources):
    proof = solve_public(graph, sources, ["policy_gate"], [rule["output"] for rule in graph["productions"]])
    require(not proof["missing_targets"] and not proof["conflicts"], "Program did not execute completely")
    return {**sources, **{key: result["value"] for key, result in proof["proofs"].items()}}


def sample(graph, rng, *, clustered=False):
    directory, records = graph["directory"], graph["records"]
    groups = directory["groups"] * 2
    rng.shuffle(groups)
    entity_key, group_key = directory["entity_key"], directory["group_key"]
    directory_rows = tuple({entity_key: entity, group_key: group} for entity, group in zip(graph["entities"], groups, strict=True))
    centers = {}
    for m in records["measures"]:
        domain = range(m["min"], m["max"] + 1, m["step"])
        if clustered:
            centers[m['id']] = {group: rng.randrange(len(domain)) for group in directory['groups']}
    rows = []
    for record in directory_rows:
        for _ in range(3):
            row = {records["row_key"]: f"R{len(rows) + 1:02d}", entity_key: record[entity_key]}
            for m in records["measures"]:
                domain = range(m["min"], m["max"] + 1, m["step"])
                lo, hi = 0, len(domain)
                if clustered:
                    center = centers[m['id']][record[group_key]]
                    radius = max(1, len(domain) // 3)
                    lo, hi = max(0, center - radius), min(hi, center + radius + 1)
                row[m["id"]] = domain[rng.randrange(lo, hi)]
            rows.append(row)
    return {directory["id"]: directory_rows, records["id"]: tuple(rows)}


def search_revision(graph, seed, *, worlds=100):
    rng = random.Random(seed)
    records, directory = graph["records"], graph["directory"]
    key, roster = records["id"], graph["targets"][0]
    group = directory['group_key']
    ranking_cuts = [rule['output'] for rule in graph['productions'] if rule['op'] in {'TOP_K', 'ARGMAX'}]
    trials = 0
    for world in range(1, worlds + 1):
        sources = sample(graph, rng, clustered=world % 2 == 0)
        old = evaluate(graph, sources)
        if not 0 < len(old[roster]) < len(graph["entities"]):
            continue
        for index, row in enumerate(sources[key]):
            owner = row[directory["entity_key"]]
            for spec in records["measures"]:
                field = spec["id"]
                domain = range(spec["min"], spec["max"] + 1, spec["step"])
                options = sorted({domain[0], domain[-1], *rng.sample(domain, min(12, len(domain)))} - {row[field]},
                                 key=lambda value: (abs(value - row[field]), value))
                for value in options:
                    trials += 1
                    corrected = list(sources[key])
                    corrected[index] = {**row, field: value}
                    latest = {**sources, key: tuple(corrected)}
                    new = evaluate(graph, latest)
                    gained, lost = new[roster] - old[roster], old[roster] - new[roster]
                    external = (gained | lost) - {owner}
                    if not external or not 0 < len(new[roster]) < len(graph["entities"]):
                        continue
                    if graph["level"] == 4 and not (gained and lost and len(external) >= 2):
                        continue
                    crossing = [node for node in ranking_cuts if
                                {row[group] for row in old[node] if group in row} !=
                                {row[group] for row in new[node] if group in row}]
                    if graph['level'] == 4 and not crossing:
                        continue
                    event = {"record_index": index, "record": row[records["row_key"]], "entity": owner,
                             "field": field, "before": row[field], "after": value}
                    return old, new, event, {"worlds": world, "event_trials": trials, "gains": sorted(gained),
                                             "losses": sorted(lost), "affected_other_entities": sorted(external),
                                             "changed_rank_cut_nodes": crossing}
    raise ValueError(f"No event changes other entities after {trials} candidate corrections")
