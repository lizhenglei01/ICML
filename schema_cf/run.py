"""Table input -> structural proposals -> deterministic truth -> native Office tasks."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

from medical_cf.office import displayed, patch, rewrite_zip, sheet_parts, NS_S
from medical_cf.oracle import evaluate
from medical_cf.run import save

from .input import load
from .engine import analyze, propose, select, apply, facts, raw_id
from .independent import verify
from .office import render, reference, oracle, clone, independent_native, FILES
from .upstream_office import provenance


def fault_tests(task, bindings, before, after, specification, changed):
    tests = {}
    ref = task / "private/reference"
    required_semantic = [b for b, _ in changed if b["kind"] == "docx" and after[b["fact_id"]]["rule"] in ("leader", "leaders", "present")]
    protected_semantic = [b for b in bindings if b["kind"] == "docx" and after[b["fact_id"]]["rule"] in ("leader", "present")
                          and before[b["fact_id"]]["value"] == after[b["fact_id"]]["value"]]

    def variant(name, expected=False):
        p = task / "private/submissions" / name
        clone(ref, p)
        tests[name] = {"path": p, "expected_pass": expected}
        return p

    variant("correct", True)
    p = variant("excel_only")
    (p/FILES[1]).write_bytes((task/"public/input"/FILES[1]).read_bytes())
    p = variant("stale_semantics", not required_semantic)
    patch(p, [(b, displayed(b, before, {})) for b in required_semantic], FILES)
    p = variant("wrong_number")
    b = next(b for b, _ in changed if b["kind"] == "xlsx" and after[b["fact_id"]]["rule"] == "count")
    patch(p, [(b, after[b["fact_id"]]["value"] + 99)], FILES)
    p = variant("unrelated_content")
    b = next(b for b in bindings if b["kind"] == "xlsx" and after[b["fact_id"]]["rule"] == "source_cell"
             and after[b["fact_id"]]["value"] == before[b["fact_id"]]["value"])
    patch(p, [(b, "UNRELATED CORRUPTION")], FILES)
    p = variant("broken_formula")
    part = sheet_parts(p/FILES[0])["Metrics"]
    with ZipFile(p/FILES[0]) as z:
        tree = E.fromstring(z.read(part))
    c = tree.find(".//s:c[@r='E2']", NS_S)
    c.remove(c.find("s:f", NS_S))
    c.find("s:v", NS_S).text = "0"
    rewrite_zip(p/FILES[0], {part: E.tostring(tree)})
    p = variant("broken_structure")
    rewrite_zip(p/FILES[1], {"word/document.xml": b"<broken>"})
    if protected_semantic:
        p = variant("unnecessary_semantic_flip")
        b = protected_semantic[0]
        # Construct the opposite bool directly; no alternate computed ground truth is accepted.
        wrong = b["template"].format(value=not after[b["fact_id"]]["value"])
        patch(p, [(b, wrong)], FILES)
        variant("unchanged_semantics_preserved", True)
    summaries = {}
    for name, item in tests.items():
        report = evaluate(item["path"], specification)
        report["expected_pass"] = item["expected_pass"]
        report["expectation_met"] = report["pass"] == item["expected_pass"]
        save(task/"private/reports"/(name+".json"), report)
        summaries[name] = {"pass": report["pass"], "expectation_met": report["expectation_met"],
                           "failed_checks": [c["id"] for c in report["checks"] if not c["pass"]], "summary": report.get("summary", {})}
    if not all(t["expectation_met"] for t in summaries.values()):
        raise AssertionError(summaries)
    return summaries


def build(input_path, output, task_count=3, max_candidates=240, max_views=48, run_faults=True):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    if input_path.is_dir() and (output == input_path or input_path in output.parents):
        raise ValueError("Generated output must be outside source input directory")
    if task_count < 1 or max_candidates < 1 or max_views < 1:
        raise ValueError("Task, candidate and view limits must be positive")
    ds = load(input_path)
    analysis = analyze(ds, max_views=max_views)
    candidates = propose(ds, analysis, max_candidates=max_candidates)
    chosen = select(candidates, task_count)
    output.mkdir(parents=True, exist_ok=True)
    save(output/"input_manifest.json", ds.provenance)
    initial_office_provenance = provenance()
    save(output/"upstream_reuse.json", initial_office_provenance)
    save(output/"parameters.json", {"input": str(input_path), "task_count": task_count, "max_candidates": max_candidates, "max_views": max_views})
    save(output/"analysis.json", analysis)
    save(output/"candidates.json", candidates)
    save(output/"selection.json", chosen)
    before = facts(ds.rows, ds.schemas, analysis["views"])
    sql_before = verify(ds.rows, ds.schemas, analysis["views"], before)
    source = output/"baseline/input"
    metadata = render(source, ds.rows, ds.schemas, before, analysis["mutable_fields"])
    save(output/"baseline/private/data.json", ds.rows)
    save(output/"baseline/private/facts.json", before)
    save(output/"baseline/private/metadata.json", metadata)
    save(output/"baseline/private/independent_sql.json", sql_before)
    save(output/"baseline/private/native.json", independent_native(source, ds.rows, ds.schemas, metadata, before))
    tasks = []
    for candidate in chosen:
        after_rows = apply(ds.rows, ds.schemas, candidate)
        after = facts(after_rows, ds.schemas, analysis["views"])
        independent = verify(after_rows, ds.schemas, analysis["views"], after)
        task = output/"tasks"/candidate["id"]
        clone(source, task/"public/input")
        changed, upstream_checks = reference(source, task/"private/reference", metadata["bindings"], before, after)
        specification = oracle(source, metadata["bindings"], before, after)
        report = evaluate(task/"private/reference", specification)
        if not report["pass"]:
            raise AssertionError([c for c in report["checks"] if not c["pass"]])
        save(task/"private/parameters.json", candidate)
        save(task/"private/before_data.json", ds.rows)
        save(task/"private/after_data.json", after_rows)
        save(task/"private/truth.json", {"before": before, "after": after})
        save(task/"private/dependency_graph.json", {"nodes": after, "views": analysis["views"], "scope": "Conservative query-source dependencies; not inferred domain causal rules"})
        save(task/"private/bindings.json", metadata["bindings"])
        save(task/"private/oracle.json", specification)
        save(task/"private/independent_sql.json", independent)
        save(task/"private/native.json", independent_native(task/"private/reference", after_rows, ds.schemas, metadata, after))
        save(task/"private/upstream_calls.json", upstream_checks)
        save(task/"private/reports/reference.json", report)
        by_fact = {}
        for b in metadata["bindings"]:
            by_fact.setdefault(b["fact_id"], []).append(b)
        with (task/"private/training_labels.jsonl").open("w", encoding="utf-8") as f:
            for fid, node in after.items():
                f.write(json.dumps(dict(node, bindings=by_fact.get(fid, [])), ensure_ascii=False)+"\n")
        source_binding = next(b for b in metadata["bindings"] if b["kind"] == "xlsx" and b["fact_id"] == raw_id(candidate["table"], candidate["row_index"], candidate["column"]))
        instruction = (
            f"In this synthetic database-editing exercise, change {candidate['table']}.{candidate['column']} "
            f"for row key {json.dumps(candidate['row_key'], ensure_ascii=False) or candidate['row_index']} "
            f"from {json.dumps(candidate['before'], ensure_ascii=False)} to {json.dumps(candidate['after'], ensure_ascii=False)}. "
            f"The source field is in data.xlsx, {source_binding['sheet']}!{source_binding['cell']}.\n\n"
            "Update the source cell, its report statement, and all affected query results in both files. "
            "Query definitions in the supplied files specify joins, filters and count/count-distinct operations. "
            "Rank descending using competition ranks; include all maximum-count groups, including ties at zero. "
            "Preserve the original group domains (including newly zero-count groups), all unaffected values, sentence templates, layout and formulas. "
            "Deliver data.xlsx and report.docx. This is a structural what-if exercise; no unstated real-world business feasibility is assumed.\n"
        )
        (task/"public/instruction.md").write_text(instruction, encoding="utf-8")
        save(task/"status.json", {"status": "draft_needs_business_review", "numerical_truth": "independently_verified",
                "reason": "Schema proves declared structural constraints, not implicit business permissions. No business approval has been inferred."})
        tests = fault_tests(task, metadata["bindings"], before, after, specification, changed) if run_faults else {}
        tasks.append({"id": candidate["id"], "operator": candidate["operator"], "table": candidate["table"], "column": candidate["column"],
                      "before": candidate["before"], "after": candidate["after"], "changed_objects": len(changed),
                      "max_join_hops": candidate["max_join_hops"], "semantic_changes": candidate["semantic_changes"],
                      "reference": report["summary"], "independent_checks": len(independent["checks"]), "tests": tests,
                      "business_status": "draft_needs_business_review"})
    if provenance() != initial_office_provenance or load(input_path).provenance != ds.provenance:
        raise AssertionError("Input or upstream helper source changed")
    result = {"pass": True, "input": str(input_path), "tables": len(ds.rows), "rows": sum(len(r) for r in ds.rows.values()),
              "views": len(analysis["views"]), "candidate_statuses": dict(Counter(c["status"] for c in candidates["candidates"])),
              "tasks": tasks, "source_unchanged": True, "task_policy": "Structural synthetic task drafts; business approval not automatic",
              "limitations": ["Only scalar JSON/CSV/XLSX tables; formula-bearing inputs require a separate adapter.",
                  "Schema-less inputs use observed categorical groups; suggested cross-table FKs remain unconfirmed and unused.",
                  "Automatic plans: up to 2 FK hops, count/count-distinct, boolean filters, rank, maximum-set and nonzero predicates.",
                  "Unstated business invariants are not recovered; drafted interventions must be reviewed before domain-valid benchmark publication.",
                  "Controlled templates and strict OOXML preservation; no free paraphrase grading, arbitrary numeric formula discovery, PPT or LLM calls."]}
    save(output/"summary.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--tasks", type=int, default=3)
    p.add_argument("--max-candidates", type=int, default=240)
    p.add_argument("--max-views", type=int, default=48)
    args = p.parse_args()
    r = build(args.input, args.out, args.tasks, args.max_candidates, args.max_views)
    print(json.dumps({k: v for k, v in r.items() if k != "tasks"}, ensure_ascii=False, indent=2))
    for t in r["tasks"]:
        print(t["id"], t["table"], t["column"], repr(t["before"]), "->", repr(t["after"]), "joins", t["max_join_hops"], "reference PASS", t["business_status"])
        for name, check in t["tests"].items():
            print(" ", name, "PASS" if check["pass"] else "FAIL (expected)", "failed checks:", ",".join(check["failed_checks"][:10]) or "none")


if __name__ == "__main__":
    main()
