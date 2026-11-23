"""Export exact business propagation and bind it to observed native Office edits."""
from collections import Counter, defaultdict, deque
from fractions import Fraction
from pathlib import Path

from openpyxl import load_workbook

from .hotel_graph import read_json, save_json
from .hotel_office import equal_value, grade, read_observations, same_names, structural_key
from .hotel_visibility import _equal


AUXILIARY = {
    "metric_definitions": "Controlled glossary text that defines arithmetic operations. It carries no independent numeric observation.",
}

STATUS_LABELS = {
    "changed_input": "更正源事实",
    "changed_dependent": "关联值变化",
    "related_unchanged": "存在依赖路径，值不变",
    "unrelated_unchanged": "无已登记传播路径，值不变",
}

ACTION_LABELS = {
    "required_edit": "需要编辑",
    "recalculates": "公式重算",
    "preserve": "原样保留",
}


def keyed(rows, key):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"Duplicate propagation identifier {key}")
    return result


def phase_proofs(graph, case):
    productions = keyed(graph["productions"], "id")
    nodes = keyed(graph["nodes"], "id")
    phases = {}
    for when, key in (("before", "old_proof"), ("after", "new_proof")):
        trace = case[key]
        if trace["missing_targets"] or trace["conflicts"]:
            raise ValueError(f"Incomplete {when} business execution")
        proofs = trace["proofs"]
        for identifier, expected in case[when].items():
            proof = proofs.get(identifier)
            node = nodes[identifier]
            if proof is None and node["kind"] == "rule" and expected == node["meaning"]:
                # The public rule is a registered capability; it is not a numeric target.
                continue
            if proof is None or not _equal(proof["value"], expected):
                raise ValueError(f"Execution and business truth disagree for {when}.{identifier}")
            chosen = proof["production"]
            if chosen is not None and productions[chosen]["output"] != identifier:
                raise ValueError(f"Production selected for the wrong node {identifier}")
        checked = defaultdict(list)
        for stepid, step in trace["reachable_productions"].items():
            rule = productions[step["production"]]
            if step["output"] != rule["output"] or not _equal(step["value"], case[when][rule["output"]]):
                raise ValueError(f"Alternative result does not match the established truth {stepid}")
            checked[rule["id"]].append({
                "step_id": stepid,
                "effective_inputs": step["inputs"],
                "rule_evidence": step["rule_evidence"],
                "shortcut": step.get("shortcut"),
            })
        phases[when] = {"proofs": proofs, "checked": checked}
    return phases


def reachability(start, productions, phase=None):
    outgoing = defaultdict(set)
    for rule in productions:
        if phase is None:
            inputs = rule["inputs"] + rule.get("requires", [])
        else:
            chosen = phase["proofs"].get(rule["output"], {})
            if chosen.get("production") != rule["id"]:
                continue
            inputs = chosen["inputs"] + rule.get("requires", [])
        for identifier in inputs:
            outgoing[identifier].add(rule["output"])
    found = {start}
    pending = deque([start])
    while pending:
        for identifier in outgoing[pending.popleft()] - found:
            found.add(identifier)
            pending.append(identifier)
    return found


def semantic_projection(value, binding):
    entity = binding.get("entity")
    if entity is not None:
        if isinstance(value, dict):
            return value[entity]
        if binding.get("value_type") == "bool" and isinstance(value, (set, frozenset, list, tuple)):
            return entity in value
        raise ValueError(f"Per-entity projection is invalid for {binding['evidence_id']}")
    if isinstance(value, (set, frozenset, list, tuple)):
        return ", ".join(sorted(value))
    return value


def matches(actual, expected, binding):
    if binding.get("value_type") == "number":
        return equal_value(actual, Fraction(str(expected)))
    if binding.get("value_type") == "text" and binding.get("semantic_node_id") in {"qualified", "peer_cohort"}:
        return same_names(actual, expected)
    return equal_value(actual, expected)


def count_projection(public, reference, formula, original, revised, values):
    contents = []
    computed = []
    for directory, observations in ((public, original), (reference, revised)):
        raw = load_workbook(directory / formula["file"], read_only=True, data_only=False)
        cached = load_workbook(directory / formula["file"], read_only=True, data_only=True)
        try:
            expression = raw[formula["sheet"]][formula["cell"]].value
            count = cached[formula["sheet"]][formula["cell"]].value
        finally:
            raw.close()
            cached.close()
        flags = [observations[key]["value"] for key in formula["inputs"]]
        if any(type(flag) is not bool for flag in flags) or count != sum(flags) or expression != formula["formula"]:
            raise ValueError("Native COUNTIF expression, cache and independently read flags disagree")
        contents.append({"formula": expression, "cached_value": count})
        computed.append(count)
    expected = values[formula["semantic_node_id"]]
    if any(computed[i] != formula[phase] or not _equal(computed[i], expected[phase])
           for i, phase in enumerate(("before", "after"))):
        raise ValueError("Native COUNTIF and business count disagree")
    target = {**formula, "location": {**formula["location"], "kind": "cell"}}
    row = {
        "evidence_id": formula["evidence_id"], "slot_id": formula["node_id"],
        "node_id": formula["semantic_node_id"], "file": formula["file"],
        "file_location": formula["location"], "role": "output_formula",
        "native_action": "recalculates" if computed[0] != computed[1] else "preserve",
        "origin_in_initial_files": "previous_issued_report", "observed_initial": computed[0],
        "observed_reference": computed[1], "business_before": expected["before"],
        "business_after": expected["after"], "formula_disposition": "preserve",
        "native_initial": contents[0], "native_reference": contents[1],
        "recalculation_inputs": formula["inputs"],
    }
    return row, structural_key(public, target)


def native_trace(package, values, event, oracle):
    public, reference = package / "public", package / "private/reference"
    bindings = keyed(oracle["bindings"], "evidence_id")
    original = keyed(read_observations(public, oracle["bindings"]), "evidence_id")
    revised = keyed(read_observations(reference, oracle["bindings"]), "evidence_id")
    expected = keyed(oracle["evidence_checks"], "evidence_id")
    if not (bindings.keys() == original.keys() == revised.keys() == expected.keys()):
        raise ValueError("Evidence, Office objects and Oracle do not cover the same bindings")
    object_checks = keyed(oracle["checks"], "object")
    required = {key for key, check in object_checks.items() if check["group"] == "required"}
    linked_required = set()
    located = defaultdict(list)
    auxiliary = defaultdict(list)
    represented_objects = set()

    def record(row, object_keys):
        row["oracle_objects"] = [{"id": key, "group": object_checks[key]["group"]} for key in object_keys]
        represented_objects.update(object_keys)
        if row["native_action"] == "required_edit":
            if not set(object_keys) <= required:
                raise ValueError(f"Required edit is not covered by native Oracle {row['evidence_id']}")
            linked_required.update(object_keys)
        if row["node_id"] in values:
            located[row["node_id"]].append(row)
        elif row["node_id"] in AUXILIARY and row["role"] in {"context", "policy"} and row["native_action"] == "preserve":
            auxiliary[row["node_id"]].append(row)
        else:
            raise ValueError(f"Unregistered graph node for native slot {row['node_id']}")

    for key, binding in bindings.items():
        identifier = binding.get("semantic_node_id", binding["node_id"])
        if identifier == event["node_id"] and binding.get("history_id") == "prior_posted_sold" and "entity" not in binding:
            binding = {**binding, "entity": event["entity"]}
        if expected[key]["node_id"] != binding["node_id"]:
            raise ValueError(f"Oracle changed slot identity for {key}")
        old, new = original[key]["value"], revised[key]["value"]
        oracle_old, oracle_new = expected[key]["before"], expected[key]["after"]
        if not matches(old, oracle_old, binding) or not matches(new, oracle_new, binding):
            raise ValueError(f"Native readback disagrees with Oracle for {key}")
        action = "preserve" if matches(old, new, binding) else "required_edit"
        if expected[key]["group"] != ("protected" if action == "preserve" else "required"):
            raise ValueError(f"Native action disagrees with Oracle for {key}")
        issued = binding["role"] == "output"
        origin = "previous_issued_report" if issued else "current_approved_source"
        row = {"evidence_id": key, "slot_id": binding["node_id"], "node_id": identifier,
               "file": binding["file"], "file_location": binding["location"], "label": binding["label"],
               "role": binding["role"], "origin_in_initial_files": origin, "native_action": action,
               "observed_initial": old, "observed_reference": new}
        if "entity" in binding:
            row["entity"] = binding["entity"]
        if identifier in values:
            old_truth, new_truth = (semantic_projection(values[identifier][when], binding) for when in ("before", "after"))
            initial = old_truth if issued else new_truth
            if not matches(old, initial, binding) or not matches(new, new_truth, binding):
                raise ValueError(f"Native position is inconsistent with the correct business phase for {key}")
            row.update(business_before=old_truth, business_after=new_truth)
        record(row, structural_key(public, binding))

    if formula := oracle.get("count_formula"):
        item, keys = count_projection(public, reference, formula, original, revised, values)
        if item["evidence_id"] in bindings or any(object_checks[key]["group"] != "protected" for key in keys):
            raise ValueError("COUNTIF must have distinct evidence and a protected expression")
        record(item, keys)
    if linked_required != required:
        raise ValueError(f"Required objects have no explanation in the trace: {sorted(required - linked_required)}")
    checked = grade(reference, oracle)
    if not checked["pass"]:
        raise ValueError("Native reference failed its Oracle during propagation export")
    external_protections = sorted(key for key in object_checks if key not in represented_objects)
    return located, auxiliary, {"verified_reference": checked["groups"],
                                "required_objects": sorted(required), "other_protected_objects": external_protections}


def deltas(graph, node, values):
    before, after = values["before"], values["after"]
    if isinstance(before, dict) and isinstance(after, dict):
        if set(before) != set(after):
            raise ValueError(f"Entity sets differ for vector node {node['id']}")
        return {entity: {"before": before[entity], "after": after[entity],
                         "changed": not _equal(before[entity], after[entity])}
                for entity in graph["entities"] if entity in before}
    if node["kind"] == "selection" or isinstance(before, (set, frozenset)):
        first, last = set(before), set(after)
        return {entity: {"before": entity in first, "after": entity in last,
                         "changed": (entity in first) != (entity in last)} for entity in graph["entities"]}
    return None


def markdown_report(report):
    event = report["event"]
    entity = event["entity"]
    lines = ["# 反事实传播明细", "", f"{entity} 的已售间夜数从 {event['old_value']:,} 更正为 {event['new_value']:,}。",
             "", "业务旧值来自更正前的账本。初始 Office 文件中的已核准经营记录可以已显示更正后的值。上次发布的报表会另行标明。",
             "", "## 业务节点", "", f"向量与候选集显示 {entity} 的值或成员状态。JSON 收录所有酒店的结果。",
             "", "| 节点 | 状态 | 业务旧值 → 新值 | 文件位置数 |", "|---|---|---|---:|"]

    def show(value):
        if type(value) is bool:
            return "是" if value else "否"
        return str(value).replace("|", "\\|").replace("\n", " ")

    for node in report["nodes"]:
        scoped = (node["entity_values"] or {}).get(entity, node)
        values = (show(scoped["before"]), show(scoped["after"]))
        displayed = f"{values[0]} → {values[1]}" if node["kind"] != "rule" else "完整政策见 JSON"
        status = STATUS_LABELS[node["state"]]
        if node["entity_values"] and not scoped["changed"] and node["state"] == "changed_dependent":
            changed_hotels = sum(record["changed"] for record in node["entity_values"].values())
            status = f"本酒店不变，另有 {changed_hotels} 家酒店变化"
        lines.append(f"| `{node['id']}` | {status} | {displayed} | {len(node['locations'])} |")
    lines.extend(["", "## 每项文件处理", "", "下表按原生位置列出需要编辑或重算的对象。段落、幻灯片、形状、表格、图表点的索引从 0 开始。JSON 同时保留所有原样保留的对象、完整前后值和已验证路径。",
                  "", "| 节点 | 对象范围 | 文件 | 原生位置 | 处理 | 初始文件值 → 参考值 |", "|---|---|---|---|---|---|"])
    for node in report["nodes"]:
        for item in node["locations"]:
            action = item["native_action"]
            if action == "preserve":
                continue
            location = ", ".join(f"{key}={value}" for key, value in item["file_location"].items())
            old = show(item["observed_initial"])
            new = show(item["observed_reference"])
            scope = item.get("entity", "整体")
            lines.append(f"| `{node['id']}` | {scope} | {item['file']} | {location} | {ACTION_LABELS[action]} | {old} → {new} |")
    return "\n".join(lines) + "\n"


def write_propagation(package, graph, case, oracle):
    """Verify and write the private trace spanning graph truth and Office readback."""
    package = Path(package)
    nodes = keyed(graph["nodes"], "id")
    values = graph["node_values"]
    if nodes.keys() != values.keys():
        raise ValueError(f"Missing or extra business truth for graph nodes: {sorted(nodes.keys() ^ values.keys())}")
    for when in ("before", "after"):
        if any(key not in values or not _equal(values[key][when], value) for key, value in case[when].items()):
            raise ValueError(f"Stored graph values do not match the case {when} values")
    start = case["event"]["node_id"]
    event_entity = case["event"]["entity"]
    if any(not _equal(values[start][when][event_entity], case["event"][event_key])
           for when, event_key in (("before", "old_value"), ("after", "new_value"))):
        raise ValueError("Event differs from the actual graph input change")
    phases = phase_proofs(graph, case)
    graph_productions = list(keyed(graph["productions"], "id").values())
    possible = reachability(start, graph_productions)
    chosen = {when: reachability(start, graph_productions, phase) for when, phase in phases.items()}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    edges = []
    for rule in graph_productions:
        for identifier in [rule["output"], *rule["inputs"], *rule.get("requires", [])]:
            if identifier not in nodes:
                raise ValueError(f"Production references an unknown propagation node {identifier}")
        incoming[rule["output"]].append(rule["id"])
        for identifier in dict.fromkeys(rule["inputs"] + rule.get("requires", [])):
            outgoing[identifier].append(rule["id"])
        execution = {}
        for when, phase in phases.items():
            selected = phase["proofs"].get(rule["output"], {}).get("production") == rule["id"]
            execution[when] = {"selected_in_canonical_proof": selected,
                               "evaluated_steps": phase["checked"].get(rule["id"], [])}
        edges.append({**rule, "execution": execution})
    located, auxiliary, office = native_trace(package, values, case["event"], oracle)
    records = []
    for key, node in nodes.items():
        pair = values[key]
        changed = not _equal(pair["before"], pair["after"])
        if changed and key not in possible:
            raise ValueError(f"Value changes without a registered dependency on the event: {key}")
        if changed:
            state = "changed_input" if key == start else "changed_dependent"
        else:
            state = "related_unchanged" if key in possible else "unrelated_unchanged"
        chosen_proofs = {}
        for when, phase in phases.items():
            proof = phase["proofs"].get(key)
            chosen_proofs[when] = (None if proof is None else
                {name: proof[name] for name in ("production", "depth", "inputs", "observations", "rule_evidence", "production_ids")})
        records.append({**node, **pair, "state": state, "incoming_productions": incoming[key],
                        "outgoing_productions": outgoing[key], "registered_event_path": key in possible,
                        "chosen_proof_event_path": {when: key in paths for when, paths in chosen.items()},
                        "chosen_proofs": chosen_proofs, "entity_values": deltas(graph, node, pair),
                        "materialization": "office" if located[key] else "internal_only", "locations": located[key]})
    report = {
        "schema": "hotel-propagation/1", "event": case["event"], "targets": graph["targets"],
        "phase_contract": {"business_before": "Exact pre-event ledger truth",
                           "business_after": "Exact corrected ledger and recomputed truth",
                           "initial_files": "Approved current source evidence mixed with the previous issued report",
                           "reference_files": "Same approved source evidence with the corrected issued report"},
        "proof_contract": "Registered reachability includes alternative productions. Canonical proof marks the chosen producer. Evaluated alternatives are listed independently of that choice.",
        "nodes": records, "productions": edges,
        "auxiliary_native_bindings": [{"id": key, "reason": AUXILIARY[key], "locations": items} for key, items in auxiliary.items()],
        "native_verification": office,
        "counts": {"node_states": dict(Counter(item["state"] for item in records)),
                   "native_actions": dict(Counter(item["native_action"] for group in [*located.values(), *auxiliary.values()] for item in group)),
                   "nodes_without_office_locations": sum(not item["locations"] for item in records)},
    }
    save_json(package / "private/propagation.json", report)
    (package / "private/propagation.md").write_text(markdown_report(report), encoding="utf-8")
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--oracle", type=Path)
    args = parser.parse_args()
    oracle = args.oracle or args.package / "private/oracle.json"
    report = write_propagation(args.package, read_json(args.graph), read_json(args.case), read_json(oracle))
    print(report["counts"])


if __name__ == "__main__":
    main()
