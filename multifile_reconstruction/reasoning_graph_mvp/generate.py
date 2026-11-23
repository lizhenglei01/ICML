"""Generator-facing prompts: operator DSL and field meanings, no target formula."""
from dataclasses import asdict
from .operators import OPERATORS

CONTRACT = {
    "graph_keys": ["target", "quantities", "productions"],
    "quantity": {"id":"quantity_name", "concept":"business_concept_or_auxiliary", "unit":"money or ratio or money^exponent"},
    "production": {"id":"unique_operator_id", "output":"quantity_name", "op":"operator_name", "inputs":["ordered_input_quantity_names"]},
    "OR": "A quantity may be a supplied fact, or be derived by any one of its alternative productions.",
    "AND": "A production requires all of its ordered input quantities.",
}


def common(task):
    # Do not put a worked financial example or intermediate name in this contract.
    meanings = {"revenue":"营业收入 / sales revenue", "cost":"营业成本 / cost of revenue",
                "profit":"毛利金额 / gross profit amount"}
    return {"task":task, "field_meanings":{k:meanings[k] for k in task["known_facts"]},
            "target_meaning":"毛利率 / gross profit margin; express as a dimensionless decimal",
            "operators":{k:asdict(v) for k,v in OPERATORS.items()}, "output_contract":CONTRACT,
            "constraints":["Only supplied facts are given leaves. Invented intermediate quantities require a derivation.",
                "No numeric literal constants, extra given facts, unsupported operators or cycles.",
                "Introduce necessary intermediate quantities yourself; do not simply output a numeric answer.",
                "Use meaningful business concepts when justified; mathematical helper quantities may use concept auxiliary.",
                "Alternative given-vs-computed derivations of a quantity are allowed; seek different sufficient input sets when facts permit."]}


def direct_request(task):
    import json
    return {"messages":[{"role":"system","content":"You propose executable business calculation graphs. Return JSON only. Do not use tools, files or external services."},
        {"role":"user","content":json.dumps({**common(task), "method":"Direct full-graph generation",
             "request":"In this single response propose the complete graph, including legitimate alternative derivations if available. Return exactly the three graph keys."},ensure_ascii=False)}]}


def backward_request(task, graph, goal, feedback, step):
    import json
    return {"messages":[{"role":"system","content":"Perform goal-driven backward graph search. Return JSON only. Do not use tools, files or external services."},
        {"role":"user","content":json.dumps({**common(task), "method":"Backward expansion", "step":step,
            "current_graph":graph, "expand_quantity":goal, "validator_feedback":feedback,
            "request":"Choose operator production(s) for the requested quantity. Introduce their intermediate inputs if needed. "
                      "Only add productions whose output is expand_quantity; do not expand other outputs yet. "
                      "Request their expansion using next_expansions. A supplied quantity may also be expanded to discover a computed alternative. "
                      "Your returned productions replace existing productions for this requested output. If searching for more alternatives, retain existing valid ones. "
                      "Return exactly quantities (new/updated quantity definitions), productions, next_expansions (quantity names you want to expand next)."},ensure_ascii=False)}]}


def merge_expansion(graph, delta, goal, known):
    from copy import deepcopy
    from .operators import InvalidGraph
    if set(delta)!={"quantities","productions","next_expansions"}:
        raise InvalidGraph("EXPANSION_SCHEMA","Expansion needs quantities, productions and next_expansions")
    if any(p.get("output")!=goal for p in delta["productions"]):
        raise InvalidGraph("WRONG_EXPANSION_OUTPUT","One backward step may expand only the requested quantity")
    result=deepcopy(graph)
    quantities={q["id"]:q for q in result["quantities"]}
    for q in delta["quantities"]: quantities[q["id"]]=q
    result["quantities"]=list(quantities.values())
    result["productions"]=[p for p in result["productions"] if p["output"]!=goal]+delta["productions"]
    available=set(known)|set(quantities)
    if any(q not in available for q in delta["next_expansions"]):
        raise InvalidGraph("UNKNOWN_EXPANSION_GOAL","Requested next quantity is not declared")
    return result,list(dict.fromkeys(delta["next_expansions"]))
