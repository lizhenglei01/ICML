"""Prepare/run one small SciTabAlign -> Office coordinate -> linexcel probe.

No generator, registered CFBench binding graph, gold annotation or reference
answer is passed to the model. The given sheet and given claim are intentional
manual selections, following SciTabAlign's supplied-table task setting.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from fractions import Fraction

from docx import Document
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "三方依赖代码"
OFFICE = DEPS / "OfficeEditBench"
SCITAB = DEPS / "SciTabAlign"
LINEXCEL = DEPS / "linexcel"
SOURCE = OFFICE / "medium/cohorts/cross_media/private/reference/OBM-036"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_function(path, name):
    """Compile the exact upstream AST node, bypassing unrelated import side effects.

    The upstream prompt module unconditionally opens a missing few-shot data
    file even for zero-shot use. We neither fake that data nor edit upstream.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def original_utils():
    spec = importlib.util.spec_from_file_location("alignment_scitab_utils", SCITAB / "utils.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_manifest():
    groups = {
        OFFICE: [str(p.relative_to(OFFICE)) for p in [
            SOURCE / "unit_review.xlsx", SOURCE / "management_discussion.docx",
            OFFICE / "LICENSE.txt"]],
        SCITAB: ["prompts/claim_evi_prompt_template.py", "utils.py", "LICENSE"],
        LINEXCEL: ["src/linexcel/analyzer.py", "src/linexcel/graph.py",
                   "src/linexcel/result.py", "uv.lock", "LICENSE"],
    }
    records = []
    for repo, files in groups.items():
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True)
        for relative in files:
            path = repo / relative
            stored = subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{relative}"])
            if path.read_bytes() != stored:
                raise ValueError(f"Source differs from upstream commit: {repo.name}/{relative}")
            records.append({"repository": repo.name, "commit": commit, "path": relative,
                            "sha256": digest(path), "matches_git_blob": True,
                            "repository_status": status})
    return records


def audit_source():
    """Independent arithmetic, before model prediction; no CFBench imports."""
    formulas = load_workbook(SOURCE / "unit_review.xlsx", data_only=False)
    cached = load_workbook(SOURCE / "unit_review.xlsx", data_only=True)
    s, c = formulas["Review"], cached["Review"]
    raw = Fraction(str(formulas["Facts"]["I2"].value))
    current = raw / 1_000_000
    prior = Fraction(str(s["B4"].value))
    # Difference/denominator is deliberately independent of B5's expression.
    growth = (current - prior) / prior
    doc = Document(SOURCE / "management_discussion.docx")
    checks = [
        ("fact_copy", s["B2"].value == raw),
        ("source_conversion_formula", s["B3"].value.replace(" ", "") == "=B2/1000000"),
        ("source_growth_formula", s["B5"].value.replace(" ", "") == "=B3/B4-1"),
        ("cached_current", c["B3"].value == float(current)),
        ("cached_growth", math.isclose(c["B5"].value, float(growth), abs_tol=1e-12, rel_tol=1e-12)),
        ("positive_single_digit_percent", 0 < growth < Fraction(1, 10)),
        ("word_growth_assertion", "增长" in doc.paragraphs[19].text),
        ("word_percentage", re.findall(r"\d+(?:\.\d+)?%", doc.paragraphs[19].text) == ["7.2%"]),
        ("independent_rounding", (growth * 1000 + Fraction(1, 2)).numerator //
         (growth * 1000 + Fraction(1, 2)).denominator == 72),
    ]
    return {"passed": all(ok for _, ok in checks),
            "checks": [{"id": k, "passed": bool(v)} for k, v in checks],
            "exact_growth": str(growth), "current_millions": float(current),
            "prior_millions": float(prior), "growth_percent": float(100 * growth)}


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    manifest = source_manifest()
    write_json(out / "source_manifest.json", manifest)
    audit = audit_source()
    write_json(out / "private/source_audit.json", audit)
    if not audit["passed"]:
        raise ValueError("Independent source audit failed")
    wb = load_workbook(SOURCE / "unit_review.xlsx", data_only=False)
    cache = load_workbook(SOURCE / "unit_review.xlsx", data_only=True)
    ws, cs = wb["Review"], cache["Review"]
    values, coordinate_map = [], {}
    for table_row, row in enumerate(ws.iter_rows(min_row=2), 1):
        values.append([])
        for col_index, cell in enumerate(row):
            coordinate_map[f"{table_row},{col_index}"] = f"{ws.title}!{cell.coordinate}"
            value = cell.value
            if cell.data_type == "f":
                value = f"{value} [saved value: {cs[cell.coordinate].value}]"
            values[-1].append("" if value is None else str(value))
    utils = original_utils()
    table = utils.format_caption_and_table(
        [cell.value for cell in ws[1]], values,
        "unit_review.xlsx / Review; source formulas and saved values; USD millions where stated")
    doc = Document(SOURCE / "management_discussion.docx")
    original = doc.paragraphs[19].text
    cases = [
        {"id": "original_word", "claim": original, "kind": "verbatim original paragraph"},
        {"id": "without_percentage", "claim": re.sub(r"约增长\s*\d+(?:\.\d+)?%", "有所增长", original),
         "kind": "constructed numeric-free variant; not a verbatim source paragraph"},
    ]
    prompt_fn = original_function(SCITAB / "prompts/claim_evi_prompt_template.py", "build_zero_shot_prompt")
    inputs = {"selection": {"sheet": "Review", "paragraph_zero_based": 19,
              "manual": True, "reason": "Minimal supplied-table/supplied-claim evidence selection, not document retrieval"},
              "table": table, "cases": cases}
    write_json(out / "public/input.json", inputs)
    write_json(out / "private/coordinate_map.json", coordinate_map)
    write_json(out / "private/gold.json", json.loads((Path(__file__).with_name("gold.json")).read_text()))
    # These are the complete model-visible messages: no gold or expected cells.
    requests = [{"id": c["id"], "messages": [
        {"role": "system", "content": "You are an expert in claim verification against scientific tables."},
        {"role": "user", "content": prompt_fn(c["claim"], table)}]} for c in cases]
    write_json(out / "public/requests.json", requests)
    return requests, coordinate_map


def parse_response(response, coordinate_map):
    answer = original_utils().extract_answer(response)
    if not answer:
        raise ValueError("Missing/nonempty <ans> evidence list")
    pairs = ast.literal_eval(answer)
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Expected a nonempty list of coordinate pairs")
    addresses = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(type(x) is not int for x in pair):
            raise ValueError("Coordinates must be integer pairs")
        key = f"{pair[0]},{pair[1]}"
        if key not in coordinate_map:
            raise ValueError(f"Coordinate outside supplied table: {key}")
        addresses.append(coordinate_map[key])
    return sorted(set(addresses))


def judge(addresses, gold):
    selected = set(addresses)
    numeric = selected & set(gold["allowed_numeric_cells"])
    alternatives = [set(s) for s in gold["sufficient_numeric_sets"]]
    sufficient = any(s <= numeric for s in alternatives)
    # Labels/units are contextual evidence; they are not falsely counted as numeric errors.
    text = selected - numeric
    return {"passed": sufficient, "selected_cells": sorted(selected),
            "selected_numeric_cells": sorted(numeric), "selected_context_cells": sorted(text),
            "checks": [
                {"id": "correct_numeric_evidence", "passed": sufficient,
                 "reason": "Select the growth cell, or both current-in-millions and prior comparator"},
                {"id": "numeric_evidence_minimal", "passed": numeric in alternatives,
                 "informational": True}],
            "limits": "All supplied numeric cells belong to this single example's chain; unrelated-number rejection needs additional fixtures"}


def selected_formula_trace(addresses, graph):
    roots = [n["id"] for n in graph["nodes"] if n.get("formula") and
             f"{n.get('sheet')}!{n.get('addr')}" in addresses]
    incoming = {}
    for edge in graph["edges"]:
        incoming.setdefault(edge["target"], []).append(edge)
    wanted, pending, edges = set(roots), list(roots), {}
    while pending:
        for edge in incoming.get(pending.pop(), []):
            edges[edge["id"]] = edge
            if edge["source"] not in wanted:
                wanted.add(edge["source"])
                pending.append(edge["source"])
    return {"roots_selected_by_model": roots,
            "nodes": [n for n in graph["nodes"] if n["id"] in wanted],
            "edges": list(edges.values()),
            "coverage": graph["meta"].get("analysisCoverage"),
            "boundary": "Only existing Excel formula evidence; Word-to-cell edges remain model proposals"}


def trace_worker(destination):
    sys.path.insert(0, str(LINEXCEL / "src"))
    from linexcel import analyze
    result = analyze(SOURCE / "unit_review.xlsx")
    write_json(destination, result.to_dict())


def build_formula_graph(out):
    destination = out / "private/formula_graph.json"
    command = ["uv", "run", "--no-project", "--with", "formualizer==0.14.0",
               "--with", "openpyxl", "--with", "python-docx", "--with", "python-calamine", "--with", "oletools",
               "--with", "datasette", "--with", "pytest", "python", str(Path(__file__).resolve()),
               "--trace-worker", str(destination)]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(LINEXCEL / "src"))
    run = subprocess.run(command, env=env, cwd="/tmp", text=True, capture_output=True, timeout=150)
    (out / "formula_trace.log").write_text(run.stdout + run.stderr)
    if run.returncode:
        raise RuntimeError("Formula extraction failed; see formula_trace.log")
    graph = json.loads(destination.read_text())
    gold = json.loads((out / "private/gold.json").read_text())
    by_id = {n["id"]: n for n in graph["nodes"]}
    checks = []
    for cell, expected in gold["expected_direct_formula_precedents"].items():
        target = next(n["id"] for n in graph["nodes"] if f"{n.get('sheet')}!{n.get('addr')}" == cell)
        found = sorted(f"{by_id[e['source']].get('sheet')}!{by_id[e['source']].get('addr')}"
                       for e in graph["edges"] if e["target"] == target)
        checks.append({"cell": cell, "expected": sorted(expected), "found": found,
                       "passed": found == sorted(expected)})
    write_json(out / "private/formula_audit.json", {"passed": all(c["passed"] for c in checks), "checks": checks})
    if not all(c["passed"] for c in checks):
        raise ValueError("Formula dependency audit failed")
    return graph


def call_model(request, model, wire_api):
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=key, timeout=60, max_retries=0)
    if wire_api == "responses":
        response = client.responses.create(model=model, input=request["messages"], max_output_tokens=1800)
        text = response.output_text
    else:
        response = client.chat.completions.create(model=model, messages=request["messages"], max_tokens=1800)
        text = response.choices[0].message.content or ""
    return text, {"requested_model": model, "reported_model": response.model,
                  "wire_api": wire_api, "response_id": response.id,
                  "usage": response.usage.model_dump() if response.usage else None}


def summarize_coverage(summary):
    """Keep sufficient coverage separate from exact/minimal evidence selection."""
    summary.pop("model_alignment_verified", None)
    cases = summary.get("cases", [])
    covered = sum(bool(c.get("passed")) for c in cases)
    minimal = sum(any(x["id"] == "numeric_evidence_minimal" and x["passed"]
                      for x in c.get("checks", [])) for c in cases)
    summary.update(evidence_coverage_passed=covered, numeric_minimality_passed=minimal,
                   evaluated_cases=len(cases), distractor_rejection_verified=False,
                   scope="One manually selected table and sentence; all provided numeric cells are on the same chain")
    if summary["status"] in {"PASS", "FAIL", "PASS_WITH_EXTRA_EVIDENCE"}:
        summary["status"] = ("PASS" if minimal == covered == len(cases) and cases
                             else "PASS_WITH_EXTRA_EVIDENCE" if covered == len(cases) and cases
                             else "FAIL")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "alignment_probe/results")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--model", default=os.environ.get("ALIGNMENT_MODEL"))
    parser.add_argument("--backend", choices=["api", "codex-relay"], default="api")
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--wire-api", choices=["responses", "chat"], default="responses")
    parser.add_argument("--trace-worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.trace_worker:
        trace_worker(args.trace_worker)
        return 0
    out = args.out.resolve()
    requests, mapping = prepare(out)
    summary = {"status": "PREPARING_FORMULA_TRACE", "model_alignment_verified": False,
               "source_audit": "PASS", "formula_dependency_audit": "NOT_RUN", "cases": []}
    write_json(out / "summary.json", summary)
    try:
        graph = build_formula_graph(out)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        summary.update(status="FAILED_FORMULA_TRACE", formula_dependency_audit="FAIL",
                       error={"type": type(exc).__name__})
        write_json(out / "summary.json", summary)
        raise
    summary.update(status="PREPARED_MODEL_NOT_RUN", formula_dependency_audit="PASS")
    write_json(out / "summary.json", summary)
    gold = json.loads((out / "private/gold.json").read_text())
    exit_code = 0
    if not args.prepare_only:
        if args.backend == "codex-relay":
            from codex_transport import call_codex, configured_route
            route = configured_route()
            args.model = args.model or route["model"]
            args.reasoning_effort = args.reasoning_effort or route["effort"]
        if not args.model:
            summary["status"] = "BLOCKED_MISSING_MODEL_CONFIG"
            exit_code = 2
        else:
            for request in requests:
                summary["status"] = "RUNNING_MODEL"
                write_json(out / "summary.json", summary)
                try:
                    if args.backend == "codex-relay":
                        text, metadata = call_codex(request, args.model, args.reasoning_effort,
                                                    out / "relay" / request["id"])
                    else:
                        text, metadata = call_model(request, args.model, args.wire_api)
                except Exception as exc:
                    summary["status"] = "BLOCKED_MODEL_CALL"
                    summary["error"] = {"type": type(exc).__name__, "http_status": getattr(exc, "status_code", None),
                                        "case_id": request["id"], "backend": args.backend}
                    # Do not log exceptions containing credentials or provider response bodies.
                    exit_code = 2
                    break
                write_json(out / f"responses/{request['id']}.json", {"raw_response": text, **metadata})
                try:
                    addresses = parse_response(text, mapping)
                    verdict = judge(addresses, gold)
                    verdict["formula_trace"] = selected_formula_trace(addresses, graph)
                except (ValueError, SyntaxError) as exc:
                    verdict = {"passed": False, "error": str(exc)}
                verdict["id"] = request["id"]
                summary["cases"].append(verdict)
                write_json(out / "summary.json", summary)
            else:
                summary["status"] = "PASS" if all(c["passed"] for c in summary["cases"]) else "FAIL"
                exit_code = 0 if summary["status"] == "PASS" else 1
    # Hash checks and git status detect any unintended upstream writes.
    if json.loads((out / "source_manifest.json").read_text()) != source_manifest():
        raise ValueError("Upstream source or repository status changed during the run")
    summary["upstream_unchanged"] = True
    summarize_coverage(summary)
    write_json(out / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
