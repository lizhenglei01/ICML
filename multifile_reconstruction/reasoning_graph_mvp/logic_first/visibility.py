"""Paper-inspired visibility experiments and bounded, executable proof search.

Depth is minimized over declared operations and registered rewrites, NOT over all
mathematical programs, natural-language inferences or accidental numeric guesses.
"""
from copy import deepcopy
import hashlib
import heapq
import json
import math
from pathlib import Path
import random

from .engine import apply, execute, validate, jsonable
from .verify import independent_answers, compare_answers
from .render import render_bundle, audit_public


SCOPE = ("Minimum parallel operator depth over graph edges, reversible arithmetic, "
         "Boolean associativity/commutativity, and observed empty/universal-set shortcuts. "
         "Vector operations count as one semantic step. This is not a universal lower bound "
         "over all programs or natural-language reasoning; no file-dispersion claim.")


def expand_calculations(original):
    """New paired graph: generated indicators are exact totals / sample counts.

    This is an explicit synthetic reporting definition, not an inferred business rule.
    """
    graph = deepcopy(original)
    rng = random.Random(original["seed"] + 104729)
    metric_labels = {m["id"]: m["label"] for m in graph["metadata"]["metrics"]}
    used = {n["inputs"][0] for n in graph["nodes"] if n["op"] in {"GE", "LE", "MAX", "MIN"}}
    before, nodes = execute(original), []
    mapping = []
    for n in graph["nodes"]:
        if n["id"] not in used:
            nodes.append(n); continue
        key = n["id"]
        denominator = "samples_" + key
        numerator = "total_" + key
        sample_counts = {e: rng.randint(2, 8) for e in graph["entities"]}
        totals = {e: before[key][e] * sample_counts[e] for e in graph["entities"]}
        denom_unit = {"reporting_samples": 1}
        total_unit = dict(n["unit"])
        total_unit["reporting_samples"] = total_unit.get("reporting_samples", 0) + 1
        base_label = metric_labels[n["metric"]]
        # Half the indicators have one more genuine computation layer (net totals).
        with_adjustment = rng.random() < .5
        numerator_label = "净累计值 / Net total: " + base_label
        if with_adjustment:
            gross, deduction = "gross_" + key, "deduction_" + key
            adjustments = {e: rng.randint(1, 30) for e in graph["entities"]}
            for factid, label, vals in [(gross, "原累计值 / Gross total: " + base_label,
                                        {e: totals[e] + adjustments[e] for e in graph["entities"]}),
                                       (deduction, "扣减值 / Deduction: " + base_label, adjustments)]:
                nodes.append({"id": factid, "op": "FACT", "inputs": [], "unit": total_unit, "metric": factid})
                graph["facts"][factid] = vals
                graph["metadata"]["metrics"].append({"id": factid, "label": label, "unit": total_unit})
            nodes.append({"id": numerator, "op": "SUB", "inputs": [gross, deduction], "unit": total_unit,
                          "label": numerator_label, "rule_source": "Our explicit synthetic reporting definition: net = gross - deduction"})
        else:
            nodes.append({"id": numerator, "op": "FACT", "inputs": [], "unit": total_unit, "metric": numerator})
            graph["facts"][numerator] = totals
            graph["metadata"]["metrics"].append({"id": numerator, "label": numerator_label, "unit": total_unit})
        nodes.append({"id": denominator, "op": "FACT", "inputs": [], "unit": denom_unit, "metric": denominator})
        graph["facts"][denominator] = sample_counts
        graph["metadata"]["metrics"].append({"id": denominator, "label": "统计样本数 / Reporting sample count: " + base_label, "unit": denom_unit})
        nodes.append({"id": key, "op": "DIV", "inputs": [numerator, denominator], "unit": n["unit"],
                      "metric": n["metric"], "label": base_label,
                      "rule_source": "Our explicit synthetic reporting definition: indicator = net total / sample count"})
        del graph["facts"][key]
        mapping.append({"indicator": key, "numerator": numerator, "denominator": denominator,
                        "adjustment_layer": with_adjustment})
    graph["nodes"] = nodes
    graph["calculation_expansion"] = mapping
    graph["provenance"]["numerical_rules"] = "Human-defined general reporting rule; program-instantiated for sampled metrics"
    validate(graph)
    return graph


def _rules(graph):
    """Finite production catalog. The same quantity may have alternate derivations."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    rules = []
    for n in graph["nodes"]:
        if n["op"] == "FACT": continue
        rules.append({"id": n["id"] + ":forward", "output": n["id"], "op": n["op"],
                      "inputs": n["inputs"], "node": n})
        if n["op"] in {"ADD", "SUB", "MUL", "DIV"}:
            a,b = n["inputs"]; c=n["id"]
            alternatives = {
                "ADD": [(a,"SUB",[c,b]),(b,"SUB",[c,a])],
                "SUB": [(a,"ADD",[c,b]),(b,"SUB",[a,c])],
                "MUL": [(a,"DIV",[c,b]),(b,"DIV",[c,a])],
                "DIV": [(a,"MUL",[c,b]),(b,"DIV",[a,c])],
            }[n["op"]]
            for i,(out,op,ins) in enumerate(alternatives):
                rules.append({"id":f"{c}:inverse{i}","output":out,"op":op,"inputs":ins,"node":n})
        if n["op"] in {"AND", "OR"}:
            def flatten(key):
                child = nodes[key]
                if child["op"] == n["op"]:
                    return sum((flatten(i) for i in child["inputs"]), [])
                return [key]
            inputs = list(dict.fromkeys(flatten(n["id"])))
            if len(inputs)>2:
                rules.append({"id":n["id"]+":associative", "output":n["id"], "op":n["op"]+"_FOLD", "inputs":inputs, "node":n})
            for operand in n["inputs"]:
                rules.append({"id":n["id"]+":short:"+operand,"output":n["id"],
                              "op":n["op"]+"_SHORT", "inputs":[operand],"node":n})
    return rules


def shortest_proofs(graph, observations, targets=None):
    """Monotone minimum-depth relaxation over registered AND-hyperedges / OR plans.

    Observations seed search. Private base-fact values are NEVER read by search.
    """
    types = validate(graph)
    known = {}
    for key,value in observations.items():
        if key not in types: raise ValueError("Unknown observed node")
        if types[key][0] == "set": value=set(value)
        known[key] = {"depth":0,"rule":"observed","inputs":[],"value":value,
                      "evidence":[key],"steps":[]}
    rules = _rules(graph)
    rounds = 0
    while True:
        changed = False; rounds += 1
        for rule in rules:
            if not all(k in known for k in rule["inputs"]): continue
            parents = [known[k] for k in rule["inputs"]]
            values = [p["value"] for p in parents]
            depth = 1 + max(p["depth"] for p in parents)
            try:
                if rule["op"].endswith("_SHORT"):
                    op=rule["op"].split("_")[0]
                    if op=="AND" and values[0] == set(): result=set()
                    elif op=="OR" and values[0] == set(graph["entities"]): result=set(graph["entities"])
                    else: continue
                elif rule["op"].endswith("_FOLD"):
                    result=set.intersection(*values) if rule["op"]=="AND_FOLD" else set.union(*values)
                    ready=[p["depth"] for p in parents];heapq.heapify(ready)
                    while len(ready)>1:
                        a,b=heapq.heappop(ready),heapq.heappop(ready)
                        heapq.heappush(ready,max(a,b)+1)
                    depth=ready[0]
                else:
                    result=apply(rule["op"],values,graph["entities"],rule["node"])
            except (ValueError, ZeroDivisionError): continue
            previous=known.get(rule["output"])
            if previous is not None and previous["depth"]<=depth: continue
            evidence=sorted({e for p in parents for e in p["evidence"]})
            steps=sorted({s for p in parents for s in p["steps"]} | {rule["id"]})
            known[rule["output"]]={"depth":depth,"rule":rule["id"],"inputs":rule["inputs"],
                                   "evidence":evidence,"steps":steps,"value":result}
            changed=True
        if not changed: break
        if rounds > len(graph["nodes"])*4: raise RuntimeError("Proof relaxation did not converge")
    requested=graph["outputs"] if targets is None else targets
    return {"scope":SCOPE,"rounds":rounds,"proofs":{
        k: ({"minimum_depth":known[k]["depth"],"value":jsonable(known[k]["value"]),
             "evidence":known[k]["evidence"],"rule":known[k]["rule"],
             "operator_steps":known[k]["steps"]} if k in known else {"minimum_depth":None}) for k in requested},
        "direct_answer_leaks":sorted(set(observations)&set(graph["outputs"]))}


def evidence_ablation(graph, observations, target):
    full=shortest_proofs(graph,observations,[target])["proofs"][target]
    if full["minimum_depth"] is None:
        return {"sufficient":False,"minimal_input_set":[],"deletion_checks":[]}
    # Backward proof gives one sufficient set. Greedy deletion obtains an inclusion-
    # minimal set under the registered inference system, not all minimum-cardinality sets.
    subset={k:observations[k] for k in full["evidence"]}
    for key in list(subset):
        reduced={k:v for k,v in subset.items() if k!=key}
        if shortest_proofs(graph,reduced,[target])["proofs"][target]["minimum_depth"] is not None:
            subset=reduced
    checks=[]
    for key in subset:
        r=shortest_proofs(graph,{k:v for k,v in subset.items() if k!=key},[target])["proofs"][target]
        checks.append({"removed":key,"still_derivable":r["minimum_depth"] is not None})
    return {"sufficient":True,"minimal_input_set":sorted(subset),"deletion_checks":checks,
            "scope":"One inclusion-minimal observation set under registered derivations; not exhaustive world-model uniqueness or minimum-cardinality proof."}


def build_pair(directory, original, *, render_charts=True):
    from .run import save
    directory=Path(directory)
    graph=expand_calculations(original)
    values=execute(graph)
    original_answers=independent_answers(original)
    expected=independent_answers(graph)
    unchanged=compare_answers(expected,original_answers)
    indicator_keys={item["indicator"] for item in graph["calculation_expansion"]}
    shallow_keys=indicator_keys | (set(graph["facts"]) & set(original["facts"]))
    deep_keys=set(graph["facts"])
    # Stable ordering keeps reproducible chart IDs and avoids hash-randomized sets.
    variants={"shallow":{k:values[k] for k in sorted(shallow_keys)},
              "deep":{k:values[k] for k in sorted(deep_keys)}}
    pair_hash=hashlib.sha256(json.dumps(jsonable(graph),sort_keys=True).encode()).hexdigest()
    reports={}
    for name,observations in variants.items():
        folder=directory/name
        mapping=render_bundle(folder/"public",graph,observations,render_charts=render_charts)
        public=audit_public(folder/"public",graph,mapping,charts=render_charts)
        computed=execute(graph,visible=observations)
        answer_check=compare_answers(computed,expected)
        proofs=shortest_proofs(graph,observations)
        ablations={target:evidence_ablation(graph,observations,target) for target in graph["outputs"]}
        reports[name]={"hidden_graph_sha256":pair_hash,"public_audit":public,"answers":answer_check,
                       "proof_search":proofs,"evidence_ablation":ablations,
                       "passed":public["passed"] and answer_check["passed"] and not proofs["direct_answer_leaks"]
                           and all(p["minimum_depth"] is not None for p in proofs["proofs"].values())}
        save(folder/"private"/"observations.json",observations)
        save(folder/"private"/"evidence_map.json",mapping["evidence"])
        save(folder/"private"/"checks.json",reports[name])
    pairs=[]
    for target in graph["outputs"]:
        a=reports["shallow"]["proof_search"]["proofs"][target]["minimum_depth"]
        b=reports["deep"]["proof_search"]["proofs"][target]["minimum_depth"]
        pairs.append({"target":target,"shallow_depth":a,"deep_depth":b,"strict_increase":a is not None and b is not None and b>a})
    result={"seed":graph["seed"],"same_truth":unchanged,"depth_comparison":pairs,
            "hidden_graph_sha256":pair_hash,"variants":reports,
            "passed":unchanged["passed"] and all(r["passed"] for r in reports.values()) and all(p["strict_increase"] for p in pairs),
            "scope":SCOPE}
    save(directory/"private"/"graph.json",graph)
    save(directory/"private"/"node_values.json",values)
    save(directory/"private"/"truth.json",expected)
    save(directory/"pair_report.json",result)
    return result
