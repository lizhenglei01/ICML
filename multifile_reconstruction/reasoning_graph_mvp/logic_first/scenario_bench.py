"""Build verified Office revision tasks from a business topic and goal."""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from .business_view import export_diagram, unit_text
from .hotel_evidence import native_bundles
from .hotel_graph import save_json
from .hotel_office import BUSINESS_FILES, build_layout, display, native_controls
from .hotel_visibility import _equal, audit_layout, choose_layouts
from .scenario_author import propose
from .scenario_graph import (PLACES, SYMBOL, add_backward, compile_graph, domain_choices,
                             execute_verified, guard, observations, reference, revisions, updated, verify_metrics)
from worldloom.render import RenderError


def evidence_catalog(graph):
    """Find admissible carriers by node role and dimensional type."""
    eligible = {}
    for key, node in graph["nodes"].items():
        kind = node["kind"]
        if kind not in {"base", "derived", "policy", "rule"}:
            continue
        if kind in {"base", "derived"}:
            files = ["excel", "ppt"] if node["unit"] else ["ppt", "excel"]
        else:
            files = ["word", "excel"]
        eligible[key] = {"kind": kind, "central_files": ["excel"], "cross_files": files}
    return eligible


def texts(graph):
    policy = graph["policy"]
    conditions = [f"{graph['nodes'][entry['metric']]['label']} {SYMBOL[entry['op']]} "
                  f"该条件对应的门槛" for entry in policy["conditions"].values()]
    combine = "两条都满足才入选" if policy["selection"]["op"] == "AND" else "任一条满足即入选"
    rule = f"本期合成内部政策。条件一是{conditions[0]}，条件二是{conditions[1]}。{combine}。"
    if policy.get("groups") or len(conditions) > 2 or policy.get("allocation"):
        conditions_by_id = dict(zip(policy["conditions"], conditions, strict=True))
        groups = policy.get("groups", {})
        def condition_text(key):
            if key in conditions_by_id:
                return conditions_by_id[key]
            group = {**groups, policy["selection"]["id"]: policy["selection"]}[key]
            separator = "，且 " if group["op"] == "AND" else "，或 "
            return "（" + separator.join(map(condition_text, group["inputs"])) + "）"
        rule = f"本期合成内部政策。初筛要求为{condition_text(policy['selection']['id'])}。"
        if allocation := policy.get("allocation"):
            priority = graph["nodes"][allocation["priority"]]["label"]
            demand = graph["nodes"][allocation["demand"]]["label"]
            limit_label = graph["nodes"]["resource_limit"]["label"]
            direction = "从高到低" if allocation["order"] == "DESC" else "从低到高"
            rule += f"候选按{priority}{direction}排序，同分按名称 Unicode 顺序。"
            if allocation["mode"] == "ALLOCATE":
                rule += (f"初始可用资源为{limit_label}。依次检查，{demand}不超过剩余资源时入选并扣减额度。"
                         "资源不足则跳过该对象并继续检查，不分配部分额度。")
            else:
                rule += f"依次检查每个候选。{demand}不超过{limit_label}时入选。各对象独立使用同一上限。"
    definitions = []
    for key, metric in graph["metrics"].items():
        op, args = metric["formula"]["op"], metric["formula"]["args"]
        left, right = (graph["nodes"][item]["label"] for item in args)
        definitions.append(f"{graph['nodes'][key]['label']} = {left} {SYMBOL[op]} {right}")
    precision = f"派生指标和汇总每步按小数点后 {PLACES} 位计算，半值取偶。"
    return rule, "。".join(definitions) + "。" + precision


def stable_history(graph, state, event):
    selection = graph["targets"][0]
    selected = state[selection]
    return {
        "prior_posted_value": state[event["node_id"]][event["entity"]],
        "prior_selection_flags": {item: item in selected for item in graph["entities"]},
        "prior_selection_list": ", ".join(sorted(selected)),
        **{"prior_" + key: state[key] for key in graph["targets"][1:]},
    }


def counterworlds(graph, basis, event, old, new, seed):
    """Find legal alternative states with the same correction and issued outputs."""
    rng = random.Random(seed)
    prior = stable_history(graph, old, event)
    target_entity = event["entity"]
    row = {key: basis[key][target_entity] for key in graph["base"]}
    variants = []
    for key, spec in graph["base"].items():
        if key == event["node_id"]:
            continue
        for value in domain_choices(spec, row[key], rng, row.values()):
            variants.append((key, target_entity, value, {**basis, key: {**basis[key], target_entity: value}}))
    for condition, rule in graph["policy"]["conditions"].items():
        key = "threshold_" + condition
        step = Fraction(1, 10**PLACES)
        candidates = {old[rule["metric"]][e] + offset for e in graph["entities"] for offset in (-step, 0, step)}
        candidates.update(new[rule["metric"]][e] + offset for e in graph["entities"] for offset in (-step, 0, step))
        if graph["nodes"][rule["metric"]]["meaning"]["kind"] == "share":
            candidates = {v for v in candidates if 0 <= v <= 1}
        for value in sorted(candidates - {basis[key]}):
            variants.append((key, None, value, {**basis, key: value}))
    witnesses = []
    for key, entity, value, alternative in variants:
        try:
            alt_old = reference(graph, alternative)
            if stable_history(graph, alt_old, event) != prior:
                continue
            revised = updated(alternative, event)
            alt_new = reference(graph, revised)
            if all(_equal(alt_new[target], new[target]) for target in graph["targets"]):
                continue
            execute_verified(graph, alternative)
            execute_verified(graph, revised)
        except (ValueError, ZeroDivisionError):
            continue
        witnesses.append({"id": f"alternative_{len(witnesses) + 1:03}",
                          "observations": {**alt_new, **prior, "event_new_value": event["after"]},
                          "rules": ["policy_gate"],
                          "answer": {target: alt_new[target] for target in graph["targets"]},
                          "facts": {"changed_node": key, "entity": entity, "value": value,
                                    "same_issued_public_results": True}})
    return witnesses


def audit(graph, bundles, expected, common, witnesses, *, paths=False):
    result = audit_layout(graph, bundles, expected, common=common, worlds=witnesses, enumerate_paths=paths)
    guard(result["passed"] and result["insufficient_subsets_with_counterworlds"] == result["insufficient_subsets"],
          "An insufficient file subset has no verified indistinguishable counterworld")
    return result


def select_case(graph, seed, stats, *, max_events=400):
    inventory = evidence_catalog(graph)
    for basis, revised, event in revisions(graph, seed, stats):
        stats["nontrivial_event_candidates"] += 1
        try:
            before, after = execute_verified(graph, basis), execute_verified(graph, revised)
            expected = {key: after[key] for key in graph["targets"]}
            common = {"observations": {"event_new_value": event["after"]}, "rules": []}
            layouts = choose_layouts(graph, after, inventory, expected, file_names=tuple(BUSINESS_FILES), common=common)
            witnesses = counterworlds(graph, basis, event, before, after, seed)
            prior = {"observations": {**common["observations"], **stable_history(graph, before, event)}, "rules": []}
            for layout in layouts.values():
                audit(graph, layout["files"], expected, prior, witnesses)
            return before, after, event, layouts, inventory, common, witnesses
        except ValueError as exc:
            stats["rejected_events"][str(exc)] += 1
        if stats["nontrivial_event_candidates"] >= max_events:
            break
    raise ValueError("No candidate event supports four distinct, witnessed evidence layouts")


def office_slots(graph, layout, old, new, event):
    slots, before, after = [], {}, {}
    rule, definitions = texts(graph)

    def add(file, node, title, role, previous, current, *, entity=None, chart=None, history=None):
        key = f"{role}.{node}" + (f".{graph['entities'].index(entity)}" if entity is not None else "")
        binding = {"node_id": key, "semantic_node_id": node, "file": file, "label": title, "role": role}
        if entity is not None:
            binding["entity"] = entity
        if chart:
            binding.update(chart=chart, category=entity, series=unit_text(graph["nodes"][node]["unit"]))
        if history:
            binding["history_id"] = history
        slots.append(binding)
        before[key], after[key] = previous, current

    for file, bundle in layout["files"].items():
        for key, value in bundle["observations"].items():
            node = graph["nodes"][key]
            title = f"{node['label']}（{unit_text(node['unit'])}）"
            if isinstance(value, dict):
                for entity in graph["entities"]:
                    add(file, key, f"{entity} · {title}", "evidence", value[entity], value[entity],
                        entity=entity, chart=title if file == "ppt" else None)
            else:
                add(file, key, title, "policy", value, value)
        if "policy_gate" in bundle["rules"]:
            add(file, "policy_gate", "合成复核政策", "policy", rule, rule)
            add(file, "metric_definitions", "指标计算和显示口径", "context", definitions, definitions)

    node, entity = event["node_id"], event["entity"]
    add("excel", node, f"{entity} · 已登记的{graph['nodes'][node]['label']}", "output",
        event["before"], event["after"], history="prior_posted_value")
    selection = graph["targets"][0]
    for entity in graph["entities"]:
        for file in ("excel", "ppt"):
            add(file, selection, f"{entity} · 复核资格", "output", entity in old[selection], entity in new[selection],
                entity=entity, chart="对象复核资格（通过为 1）" if file == "ppt" else None, history="prior_selection_flags")
    add("word", selection, "入选名单", "output", ", ".join(sorted(old[selection])), ", ".join(sorted(new[selection])),
        history="prior_selection_list")
    for key in graph["targets"][1:]:
        node = graph["nodes"][key]
        add("word", key, f"{node['label']}（{unit_text(node['unit'])}）", "output", old[key], new[key],
            history="prior_" + key)
    return slots, before, after


def instruction(graph, event):
    node = graph["nodes"][event["node_id"]]
    reports = "、".join(graph["nodes"][key]["label"] for key in graph["targets"][1:])
    return (
        f"# {graph['title']}｜{graph['period']}\n\n"
        f"已批准更正 {event['entity']} 的「{node['label']}」，由 {display(event['before'])} 改为 "
        f"{display(event['after'])}（{unit_text(node['unit'])}）。工作空间里的来源数据按更正后的口径发布，"
        "复核输出仍是上次发布的结果。请依据文件内的业务政策和指标口径重做复核。\n\n"
        f"- {BUSINESS_FILES['excel']} 更正已登记值和受影响的资格状态。\n"
        f"- {BUSINESS_FILES['word']} 更新入选名单和{reports}。"
        "名单按名称的 Unicode 顺序排列，用英文逗号和空格分隔。\n"
        f"- {BUSINESS_FILES['ppt']} 更新资格表及其原生可编辑图表的数据，通过为 1。\n\n"
        "提交这三份原生文件。保留原有结构和公式，以及仍正确的来源记录、政策和其他内容。\n"
    )


def delta(graph, old, new, event):
    related = {event["node_id"]}
    while True:
        reached = related | {rule["output"] for rule in graph["productions"] if related.intersection(rule["inputs"])}
        if reached == related:
            break
        related = reached
    rule, _ = texts(graph)
    result = {"policy_gate": {"before": rule, "after": rule, "related": False, "status": "preserved"}}
    for key in old:
        changed = not _equal(old[key], new[key])
        value = {"before": old[key], "after": new[key], "related": key in related,
                 "status": "direct" if key == event["node_id"] else "changed" if changed else "preserved"}
        if changed and isinstance(old[key], dict):
            value["changed_entities"] = [entity for entity in old[key] if not _equal(old[key][entity], new[key][entity])]
        result[key] = value
    return result


def draw(root, graph, caption="同一业务图用于四种证据布局。"):
    kinds = {"base": "evidence", "derived": "set", "policy": "evidence", "rule": "rule",
             "condition": "logic", "selection": "answer", "report": "answer"}
    nodes = [{"id": key, "kind": kinds[node["kind"]], "label": node["label"]} for key, node in graph["nodes"].items()]
    edges = []
    for plan in graph["productions"]:
        operation = "operation_" + plan["id"]
        nodes.append({"id": operation, "kind": "rule", "label": plan.get("comparison", plan["op"])})
        edges.extend({"source": key, "target": operation} for key in (*plan["inputs"], *plan.get("requires", ())))
        edges.append({"source": operation, "target": plan["output"]})
    export_diagram(root, graph["title"], nodes, edges, caption)


def package(root, graph, selection, stats):
    old, new, event, layouts, inventory, common, witnesses = selection
    expected = {key: new[key] for key in graph["targets"]}
    private = root / "private"
    save_json(private / "graph.json", graph)
    save_json(private / "truth.json", {"event": event, "nodes": delta(graph, old, new, event),
                                    "before": {key: old[key] for key in graph["targets"]}, "after": expected})
    save_json(private / "evidence_catalog.json", inventory)
    draw(private, graph)
    rule_text, definitions = texts(graph)
    task = instruction(graph, event)
    result = {}
    for name, layout in layouts.items():
        path = root / "layouts" / name
        slots, before, after = office_slots(graph, layout, old, new, event)
        native = build_layout(path, slots, before, after, task, title=graph["title"], ppt_size="long")
        bundles = native_bundles(graph, native["before_observations"], include_prior=True,
                                 gate_text=rule_text, formula_text=definitions)
        proof = audit(graph, bundles, expected, common, witnesses, paths=True)
        required = len(BUSINESS_FILES) if name.endswith("_cross_file") else 1
        guard((proof["minimum_files"], proof["minimum_depth"]) ==
              (required, layout["audit"]["minimum_depth"]), "Actual file layout changed proof depth or necessity")
        final_bundles = native_bundles(graph, native["after_observations"], include_prior=False,
                                       gate_text=rule_text, formula_text=definitions)
        final_proof = audit_layout(graph, final_bundles, expected, common=common, enumerate_paths=False)
        guard(final_proof["passed"] and final_proof["minimum_files"] == proof["minimum_files"]
              and final_proof["minimum_depth"] == proof["minimum_depth"], "Reference assets changed the underlying evidence")
        controls = native_controls(path, native["oracle"])
        guard("wrong_corrected_value" in controls and "unchanged_word" in controls,
              "Missing expected negative control")
        save_json(path / "private" / "visibility.json", proof)
        result[name] = {"minimum_files": proof["minimum_files"], "minimum_depth": proof["minimum_depth"],
                        "minimal_file_sets": proof["minimal_file_sets"], "selected_evidence": layout["evidence_ids"],
                        "insufficient_subsets": proof["insufficient_subsets"],
                        "witnessed_subsets": proof["insufficient_subsets_with_counterworlds"],
                        "reference_grade": native["reference_grade"]["groups"],
                        "controls": {case: item["pass"] for case, item in controls.items()}}
    report = {"passed": True, "title": graph["title"], "event": event, "target_count": len(graph["targets"]),
              "selection": graph["policy"]["selection"]["op"], "basis": stats, "layouts": result,
              "shortcuts_scope": "Registered arithmetic and policy productions, verified backward alternatives and set short circuits. Not natural-language shortcut exhaustiveness."}
    save_json(root / "summary.json", report)
    return report


def build(topic, goal, root, *, seed=8128, attempts=3, reuse=False, effort="medium", decision_pair=False):
    root = Path(root)
    if (root / "case").exists():
        raise ValueError("Published case already exists; choose a new output directory")
    root.mkdir(parents=True, exist_ok=True)
    settings = {"topic": topic, "goal": goal, "seed": seed}
    if decision_pair:
        settings["decision_pair"] = True
    save_json(root / "input.json", settings)
    failures = []
    for index in range(1, attempts + 1):
        stage = "allocation_observations" if decision_pair else "observations"
        run = root / "proposals" / f"attempt_{index:02}"
        stats = Counter()
        stats["rejected_events"] = Counter()
        def author(phase, context):
            return propose(phase, topic, goal, context, run / phase, reuse=reuse, effort=effort)
        try:
            raw = author(stage, {})
            base = observations(raw)
            stage = "forward"
            forward = author(stage, raw)
            metrics, meanings, expressions = verify_metrics(base, forward)
            stage = "backward"
            target = forward["backward_target"]
            draft = next(m for m in forward["metrics"] if m["id"] == target)
            brief = {key: value for key, value in draft.items() if key not in {"operands", "formula"}}
            backward = add_backward(base, metrics, expressions, target, author(stage, {
                "observations": raw["observations"], "constraints": raw["constraints"], "target": brief,
                "intermediate_options": [m for m in forward["metrics"] if m["id"] != target]}))
            stage = "allocation_policy" if decision_pair else "policy"
            policy = author(stage, {"observations": raw["observations"], "metrics": forward["metrics"]})
            if decision_pair:
                from .scenario_allocation import build_pair

                stage = "decision_pair"
                with TemporaryDirectory(dir=root, prefix=".building-") as temporary:
                    pending = Path(temporary) / "case"
                    result = build_pair(pending, base, metrics, meanings, backward, policy, seed + index - 1, stats)
                    pending.rename(root / "case")
                result.update(attempt=index, rejected_proposals=failures)
                save_json(root / "summary.json", result)
                return result
            graph = compile_graph(base, metrics, meanings, backward, policy)
            save_json(run / "verified_graph.json", graph)
            stage = "counterfactual_and_layout"
            selected = select_case(graph, seed + index - 1, stats)
            stage = "native_package"
            with TemporaryDirectory(dir=root, prefix=".building-") as temporary:
                pending = Path(temporary) / "case"
                result = package(pending, graph, selected, stats)
                pending.rename(root / "case")
            result.update(attempt=index, rejected_proposals=failures)
            save_json(root / "summary.json", result)
            return result
        except (KeyError, TypeError, ValueError, OSError, RuntimeError, ArithmeticError, RenderError) as exc:
            failure = {"attempt": index, "stage": stage, "reason": str(exc), "statistics": stats}
            failures.append(failure)
            save_json(run / "rejection.json", failure)
            print(f"attempt {index} rejected at {stage} — {exc}", flush=True)
            if reuse:
                break
    report = {"passed": False, "rejected_proposals": failures}
    save_json(root / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=8128)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--reuse", action="store_true", help="Replay the recorded author prompts and proposals")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--decision-pair", action="store_true", help="Compare individual limits and a shared resource pool")
    args = parser.parse_args()
    report = build(args.topic, args.goal, args.out, seed=args.seed, attempts=args.attempts, reuse=args.reuse,
                   effort=args.effort, decision_pair=args.decision_pair)
    if not report["passed"]:
        raise SystemExit(1)
    for name, layout in report.get("layouts", report.get("variants", {})).items():
        print(f"{name} files={layout['minimum_files']} depth={layout['minimum_depth']}")


if __name__ == "__main__":
    main()
