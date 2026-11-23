"""One command builds and audits examples. Never calls a model or edits upstream."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from .engine import execute, jsonable, CONTRACTS
from .synthesis import generate
from .verify import audit_graph, independent_answers, compare_answers
from .render import render_bundle, audit_public


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def build_one(output, seed, depth, metadata=None, render_charts=True):
    started = time.perf_counter()
    output = Path(output)
    graph = generate(seed, depth, metadata)
    state = execute(graph)
    mapping = render_bundle(output / "public", graph, render_charts=render_charts)
    graph_audit = audit_graph(graph, state)
    public_audit = audit_public(output / "public", graph, mapping, charts=render_charts)
    answers = independent_answers(graph)
    rejected = []
    for name, target, value in [
        ("wrong_entities", graph["selection"], []),
        ("wrong_count", "answer_count", answers["answer_count"] + 1),
        ("wrong_average", "answer_average", answers["answer_average"] + 1),
    ]:
        bad = dict(answers); bad[target] = value
        result = compare_answers(bad, answers)
        rejected.append({"name": name, "expected_pass": False, **result})
    report = {"seed": seed, "depth": depth, "graph_audit": graph_audit, "public_audit": public_audit,
              "negative_answers": rejected, "generation_seconds": time.perf_counter()-started}
    report["passed"] = graph_audit["passed"] and public_audit["passed"] and all(not r["passed"] for r in rejected)
    private = output / "private"
    save(private / "graph.json", graph)
    save(private / "node_values.json", state)
    save(private / "truth.json", answers)
    save(private / "evidence_map.json", mapping["evidence"])
    save(private / "writer_input.json", mapping["narrative_spec"])
    save(private / "checks.json", report)
    save(private / "parameters.json", {"seed": seed, "depth": depth, "metadata": graph["metadata"]})
    nodes={n['id']:n for n in graph['nodes']}
    def readable(key,prefix=''):
        n=nodes[key]
        suffix=f" threshold={n['threshold']}" if 'threshold' in n else ''
        lines=[prefix+key+' ['+n['op']+']'+suffix]
        for child in n['inputs']: lines.extend(readable(child,prefix+'  '))
        return lines
    (private/'tree.txt').write_text('\n'.join(readable(graph['selection']))+'\n',encoding='utf-8')
    return graph, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "artifacts")
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--metadata", type=Path, help="Optional metadata JSON; does not contain a prebuilt graph")
    parser.add_argument("--stage", choices=["1", "all"], default="1")
    args = parser.parse_args()
    if args.seeds < 1: parser.error("--seeds must be positive")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    examples, graphs = [], []
    for seed in range(args.start_seed, args.start_seed + args.seeds):
        graph, report = build_one(args.output / f"seed_{seed:03d}", seed, 2 + seed % 4, metadata)
        graphs.append(graph)
        examples.append({"seed": seed, "depth": graph["requested_depth"], "passed": report["passed"],
                         "filter_count": len(graph["constraints"]), "seconds": report["generation_seconds"]})
        print(f"seed={seed} depth={graph['requested_depth']} passed={report['passed']}", flush=True)
    phase1 = {"passed": all(e["passed"] for e in examples), "examples": examples,
              "distinct_depths": sorted({e["depth"] for e in examples}),
              "used_operators": sorted({n["op"] for g in graphs for n in g["nodes"]}),
              "unique_graphs": len({hashlib.sha256(json.dumps(g["nodes"],sort_keys=True).encode()).hexdigest() for g in graphs})}
    result = {"stage1": phase1, "stage2": {"status": "not_requested"}}
    save(args.output / "stage1_report.json", phase1)
    # This import is reached only after the complete first-stage generation audit passes.
    if args.stage == "all":
        if not phase1["passed"]: raise RuntimeError("Stage 1 failed; stage 2 is gated")
        from .visibility import build_pair
        pairs = [build_pair(args.output / f"seed_{g['seed']:03d}" / "visibility", g) for g in graphs]
        result["stage2"] = {"passed": all(p["passed"] for p in pairs), "pairs": pairs}
    result["elapsed_seconds"] = time.perf_counter() - started
    result["provenance"] = {
        "reference": "https://proceedings.mlr.press/v306/yu26al.html",
        "longchart_reference": "https://arxiv.org/abs/2608.01328",
        "implementation": "Independent deterministic implementation inspired by papers; not official generation code",
        "official_dochop_checkout": "84cf9be996038bd85778c9a5bc1770cbb516e1f5",
        "upstream_generation_functions_called": [], "llm_calls": 0,
        "human_defined": ["metadata and business labels", "operator meanings and units", "rule grammar", "question templates"],
        "differences": ["integer finite-domain synthesis instead of Gemini synthesis", "subset of rule types", "HTML/SVG instead of single-page ReportLab image", "no manual quality review yet"]}
    result["passed"] = phase1["passed"] and result["stage2"].get("passed", True)
    save(args.output / "summary.json", result)
    save(args.output / "operator_contracts.json", {k:{"inputs":v[0],"output":v[1]} for k,v in CONTRACTS.items()})
    print(json.dumps({"passed": result["passed"], "examples": len(examples), "seconds": round(result["elapsed_seconds"],2), "report": str(args.output / "summary.json")},ensure_ascii=False),flush=True)
    if not result["passed"]: raise SystemExit(1)


if __name__ == "__main__": main()
