"""Run two inputs x direct/backward LLM generation, with private verification."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time

from .generate import direct_request, backward_request, merge_expansion
from .graph import readable
from .operators import InvalidGraph
from .verifier import verify, safe_feedback

HERE=Path(__file__).resolve().parent


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")


def parse(raw):
    text=raw.strip()
    if text.startswith("```") and text.endswith("```"):text=text[text.index("\n")+1:-3].strip()
    obj=json.loads(text)
    if not isinstance(obj,dict):raise ValueError("Expected JSON object")
    return obj


def call(request,directory):
    # Existing inference-only transport: empty cwd and no host tools/apps.
    from alignment_probe.codex_transport import call_codex,configured_route
    route=configured_route();save(directory/"request.json",request)
    raw,metadata=call_codex(request,route["model"],route["effort"] or "high",directory/"transport")
    (directory/"response.txt").write_text(raw);save(directory/"call.json",metadata)
    return parse(raw),metadata


def coverage(report,task):
    expected=[{"revenue","cost"}]
    if "profit" in task["known_facts"]:expected.append({"profit","revenue"})
    actual=[set(s) for s in report.get("minimal_input_sets",[])]
    return all(s in actual for s in expected)


def run_llm(task,method,directory,max_steps=6):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    save(directory/"input.json",task)
    started=time.monotonic();history=[];calls=0;validations=0
    graph={"target":task["target"],"quantities":[{"id":task["target"],"concept":task["target"],"unit":"ratio"}],"productions":[]}
    if method=="direct":
        graph,metadata=call(direct_request(task),directory/"call_01");calls+=1
        report=verify(graph,task);validations+=1
        history.append({"step":1,"feedback":safe_feedback(report),"call":metadata})
    else:
        frontier=[task["target"]];feedback={"status":"START"};report=None
        for step in range(1,max_steps+1):
            goal=frontier.pop(0) if frontier else task["target"]
            delta,metadata=call(backward_request(task,graph,goal,feedback,step),directory/f"call_{step:02d}");calls+=1
            before=deepcopy(graph)
            try:
                candidate,next_goals=merge_expansion(graph,delta,goal,task["known_facts"])
                report=verify(candidate,task,partial=True);validations+=1
                feedback=safe_feedback(report)
                if not report["errors"]:
                    graph=candidate
                    frontier=list(dict.fromkeys(frontier+report.get("unresolved",[])+next_goals))
                else:
                    graph=before;frontier.insert(0,goal)
            except (InvalidGraph,KeyError,TypeError) as exc:
                report={"accepted":False,"status":"REJECTED","valid_paths":[],"rejected_paths":[],
                        "errors":[{"code":getattr(exc,"code","EXPANSION_SCHEMA"),"message":str(exc)}]}
                validations+=1;feedback=safe_feedback(report);frontier.insert(0,goal)
            if report["accepted"] and not coverage(report,task):
                feedback["search_request"]="The current graph is valid. Search for further derivations with different sufficient input sets; you may expand a supplied dependency."
                if not frontier:frontier=[task["target"]]
            history.append({"step":step,"expanded_quantity":goal,"feedback":feedback,"frontier":list(frontier),"call":metadata})
            save(directory/f"step_{step:02d}_graph.json",graph)
            save(directory/f"step_{step:02d}_validation.json",report)
            print("BACKWARD_STEP",directory.name,step,goal,feedback,flush=True)
            if report["accepted"] and coverage(report,task):break
        report=verify(graph,task);validations+=1
    save(directory/"graph.json",graph)
    save(directory/"validation.json",report)
    save(directory/"search_trace.json",history)
    (directory/"graph.txt").write_text(readable(graph,task["known_facts"]))
    result={"origin":"llm_generation","method":method,"llm_generated":True,"model":history[0]["call"]["reported_model"] if history else None,
            "accepted":report["accepted"],"all_requested_input_sets_found":coverage(report,task),
            "generation_calls":calls,"validation_calls":validations,"wall_seconds":time.monotonic()-started,
            "minimal_input_sets":report.get("minimal_input_sets",[]),
            "paths":[{k:p[k] for k in ["input_set","operator_count","dependency_depth","intermediate_quantities","result","exact_result"]} for p in report["valid_paths"]],
            "error_codes":safe_feedback(report)["error_codes"],
            "tool_events":sum(len(h["call"].get("tool_events",[])) for h in history),
            "formula_supplied_to_generator":False,"feedback_contains_expected_formula_or_values":False}
    save(directory/"result.json",result)
    return result


def negative_candidates(task):
    q={"id":"gross_profit_margin","concept":"gross_profit_margin","unit":"ratio"}
    def single(op,inputs):return {"target":task["target"],"quantities":[q],"productions":[{"id":"bad","output":task["target"],"op":op,"inputs":inputs}]}
    candidates={"revenue_div_cost":single("DIV",["revenue","cost"])}
    for name,op,last in [("sum_div_revenue","ADD","DIV"),("sqrt_difference","SUB","SQRT")]:
        candidates[name]={"target":task["target"],"quantities":[q,{"id":"helper","concept":"auxiliary","unit":"money"}],
            "productions":[{"id":"helper_op","output":"helper","op":op,"inputs":["revenue","cost"]},
                           {"id":"target_op","output":task["target"],"op":last,"inputs":["helper","revenue"] if last=="DIV" else ["helper"]}]}
    return candidates


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=HERE/"results"/datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--offline",action="store_true")
    parser.add_argument("--method",choices=["direct","backward","all"],default="all")
    parser.add_argument("--case",choices=["basic","with_profit","all"],default="all")
    args=parser.parse_args();root=args.out.resolve();root.mkdir(parents=True,exist_ok=True)
    results={};tasks={name:json.loads((HERE/"examples"/(name+".json")).read_text()) for name in ["basic","with_profit"]}
    for name,task in tasks.items():
        if args.case not in {name,"all"}:continue
        results[name]={}
        for method in ["direct","backward"]:
            if args.method not in {method,"all"}:continue
            if args.offline:continue
            print("LLM_STARTED",name,method,flush=True)
            try:results[name][method]=run_llm(task,method,root/name/method)
            except Exception as exc:
                results[name][method]={"origin":"llm_attempt","completed":False,"error":type(exc).__name__+": "+str(exc),
                                       "accepted":None,"note":"No program-search output is presented as model output. Inspect call audit for transport vs response errors."}
                print("LLM_ERROR",name,method,results[name][method]["error"],flush=True)
            save(root/"comparison.json",results)
            print("RESULT",name,method,json.dumps(results[name][method],ensure_ascii=False),flush=True)
        # A deterministic fallback is labeled separately, including when API failed.
        if args.offline or any(v.get("accepted") is None for v in results[name].values()):
            from .baseline import search
            baseline=search(task);save(root/name/"program_baseline.json",baseline)
            results[name]["program_baseline"]={"origin":baseline["origin"],"llm_generated":False,"candidate_count":baseline["candidate_count"],"minimal_input_sets":baseline["minimal_input_sets"]}
        bad={key:verify(graph,task) for key,graph in negative_candidates(task).items()}
        if any(r["accepted"] for r in bad.values()):raise AssertionError("Verifier accepted a deliberately incorrect expression")
        save(root/name/"rejected_controls.json",bad)
    save(root/"comparison.json",results)
    save(root/"provenance.json",{"new_code":"reasoning_graph_mvp only; no Office generation or agent editing tests", "llm_knowledge":"The model's existing business knowledge; it is not trained here",
        "manual_knowledge":"Evaluator target definition, gross-profit identity, concepts/units and valid business domain",
        "program_work":"Graph/arity/cycle/unit/domain checking, symbolic equivalence, perturbation tests, AND-OR path enumeration and minimal-set reduction",
        "generator_contract":"Generic graph DSL/operator definitions and provided-field meanings only; no worked target formula or prebuilt graph",
        "fallback":"Only if offline or LLM attempt fails; marked deterministic_program_search"})
    print("OUTPUT",str(root),flush=True)


if __name__=="__main__":main()
