"""python -m medical_cf.run --config medical_cf/config.json --out medical_cf/artifacts"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import sys
from zipfile import ZipFile

from lxml import etree as ET

from openpyxl import load_workbook
from docx import Document

from . import upstream
from .facts import RULES, compute, intervene
from .independent import verify, verify_files, verify_recalculation
from .office import write_initial, make_reference, patch, displayed, rewrite_zip, sheet_parts, NS_S
from .oracle import generate, evaluate


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def clone(source, target):
    Path(target).mkdir(parents=True, exist_ok=True)
    for f in ("hospital.xlsx", "review.docx"):
        shutil.copy2(Path(source) / f, Path(target) / f)


def negative_suite(task, bindings, before, after, names, oracle):
    root = task / "private/submissions"
    reference, public = task / "private/reference", task / "public/input"
    variants = {}

    def submit(name, base=reference):
        target = root / name
        clone(base, target)
        variants[name] = {"path": target, "expected_pass": name == "correct"}
        return target

    submit("correct")
    p = submit("excel_only")
    shutil.copy2(public / "review.docx", p / "review.docx")
    p = submit("stale_semantics")
    # Reset every changed rank/leader sentence, while leaving numeric counts updated.
    stale = [(b, displayed(b, before, names)) for b in bindings
             if b["kind"] == "docx" and (b["fact_id"].endswith(":leader") or b["fact_id"] == "departments:leaders" or b["fact_id"].endswith(":rank"))
             and displayed(b, before, names) != displayed(b, after, names)]
    patch(p, stale)
    # If no semantic output changes, retaining old semantics is CORRECT, not a failure.
    variants["stale_semantics"]["expected_pass"] = not stale
    p = submit("incorrect_semantic_flip")
    semantic = next(b for b in bindings if b["kind"] == "docx" and b["fact_id"].endswith(":leader"))
    wrong = deepcopy(after)
    wrong[semantic["fact_id"]]["value"] = not wrong[semantic["fact_id"]]["value"]
    patch(p, [(semantic, displayed(semantic, wrong, names))])
    p = submit("unrelated_content")
    protected_name = next(b for b in bindings if b["fact_id"] == "db:Patient:0:Name")
    patch(p, [(protected_name, "WRONG UNRELATED PATIENT")])
    p = submit("wrong_count")
    changed_count = next(b for b in bindings if b["kind"] == "xlsx" and b["fact_id"].endswith(":count")
                         and before[b["fact_id"]]["value"] != after[b["fact_id"]]["value"])
    patch(p, [(changed_count, 999)])
    p = submit("broken_formula")
    part = sheet_parts(p / "hospital.xlsx")["Summary"]
    with ZipFile(p / "hospital.xlsx") as z:
        xml = ET.fromstring(z.read(part))
    c = xml.find(".//s:c[@r='G2']", NS_S)
    c.remove(c.find("s:f", NS_S))
    c.find("s:v", NS_S).text = str(sum(1 for f in after if f.startswith("physician:")))
    rewrite_zip(p / "hospital.xlsx", {part: ET.tostring(xml)})
    p = submit("broken_structure")
    rewrite_zip(p / "review.docx", {"word/document.xml": b"<broken>"})
    p = submit("invalid_business_state")
    # A physician now has no primary row. A native-readable but invalid submission.
    b = next(b for b in bindings if b["fact_id"].startswith("db:Affiliated_With:")
             and b["fact_id"].endswith(":PrimaryAffiliation") and after[b["fact_id"]]["value"] == 1)
    patch(p, [(b, 0)])

    reports = {}
    for name, variant in variants.items():
        report = evaluate(variant["path"], oracle)
        report["expected_pass"] = variant["expected_pass"]
        report["expectation_met"] = report["pass"] == report["expected_pass"]
        save(task / "private/reports" / (name + ".json"), report)
        failures = [c["id"] for c in report["checks"] if not c["pass"]]
        reports[name] = {k: report[k] for k in ("pass", "expected_pass", "expectation_met")}
        reports[name].update(summary=report.get("summary", {}), failed_checks=failures)
    if not all(x["expectation_met"] for x in reports.values()):
        raise AssertionError(reports)
    return reports


def build(config, output, case=upstream.CASE, run_negatives=True, recalculate=True):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    initial_manifest = upstream.manifest(case)
    save(output / "source_manifest.json", initial_manifest)
    save(output / "upstream_reuse.json", upstream.audit_entrypoints(case))
    rows, schemas = upstream.load(case)
    baseline = compute(rows, schemas)
    independent_before = verify(rows, schemas, baseline)
    evidence = upstream.serialize_and_check(baseline)
    source = output / "baseline/input"
    bindings = write_initial(source, rows, schemas, baseline, evidence)
    save(output / "baseline/private/data.json", rows)
    save(output / "baseline/private/facts.json", baseline)
    save(output / "baseline/private/bindings.json", bindings)
    save(output / "baseline/private/evidence.json", evidence)
    save(output / "baseline/private/independent.json", independent_before)
    save(output / "baseline/private/rules.json", RULES)
    save(output / "baseline/private/native_independent.json", verify_files(source, rows, independent_before))
    if recalculate:
        save(output / "baseline/private/native_recalculation.json", verify_recalculation(source, len(rows["Physician"])))
    save(output / "parameters.json", config)
    # Five original documents are left in the upstream checkout and hashed in the manifest.
    names = {r["DepartmentID"]: r["Name"] for r in rows["Department"]}
    physician_names = {r["EmployeeID"]: r["Name"] for r in rows["Physician"]}
    summaries = {}
    seen = set()
    for params in config["interventions"]:
        name = params["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in seen:
            raise ValueError("task IDs must be unique plain filenames")
        seen.add(name)
        for key in ("physician", "department"):
            if type(params[key]) is not int:
                raise ValueError("IDs must be integers")
        task = output / "tasks" / name
        public, private = task / "public", task / "private"
        clone(source, public / "input")
        after_rows = intervene(rows, schemas, params["physician"], params["department"])
        after = compute(after_rows, schemas)
        independent = verify(after_rows, schemas, after)
        changes = make_reference(source, private / "reference", bindings, baseline, after, names)
        oracle = generate(source, bindings, baseline, after, names)
        save(private / "parameters.json", params)
        save(private / "before_data.json", rows)
        save(private / "after_data.json", after_rows)
        save(private / "truth.json", {"before": baseline, "after": after})
        save(private / "dependency_graph.json", {"rules": RULES, "nodes": after,
                    "scope": "source/derivation graph with conservative affiliation dependencies, not a minimal causal graph"})
        save(private / "bindings.json", bindings)
        save(private / "oracle.json", oracle)
        save(private / "independent.json", independent)
        save(private / "native_independent.json", verify_files(private / "reference", after_rows, independent))
        if recalculate:
            save(private / "native_recalculation.json", verify_recalculation(private / "reference", len(after_rows["Physician"])))
        by_fact = {}
        for binding in bindings:
            by_fact.setdefault(binding["fact_id"], []).append(binding)
        with (private / "training_labels.jsonl").open("w", encoding="utf-8") as stream:
            for fid, fact in after.items():
                stream.write(json.dumps(dict(fact, bindings=by_fact.get(fid, [])), ensure_ascii=False) + "\n")
        instruction = (
            f"Move {physician_names[params['physician']]} (physician ID {params['physician']}) to "
            f"{names[params['department']]} (department ID {params['department']}) as their primary department.\n\n"
            "Edit both supplied files. Update the primary affiliation record, current primary-department views, "
            "department primary-physician counts, competition ranks and statements about which departments have the most primary physicians. "
            "Count only PrimaryAffiliation=1. Equal counts share a rank; the next rank skips tied positions. Include every tied leader. "
            "This is an administrative affiliation change, not a change to existing patient appointments. "
            "Preserve names, patient and appointment records, all other unaffected content, native formulas and formatting. "
            "Deliver hospital.xlsx and review.docx, retaining the supplied sentence templates and layout.\n"
        )
        (public / "instruction.md").write_text(instruction, encoding="utf-8")
        reference_report = evaluate(private / "reference", private / "oracle.json")
        save(private / "reports/reference.json", reference_report)
        if not reference_report["pass"]:
            raise AssertionError([c for c in reference_report["checks"] if not c["pass"]])
        tests = negative_suite(task, bindings, baseline, after, names, oracle) if run_negatives else {}
        # Protected semantic facts are allowed to have changed dependencies but unchanged outputs.
        protected_semantics = [c["id"] for c in oracle["checks"] if c["category"] == "protected" and (
            c["fact_id"].endswith(":leader") or c["fact_id"] == "departments:leaders")]
        # No private provenance identifiers, answers or graph files are embedded in public native assets.
        allowed = {"input/hospital.xlsx", "input/review.docx", "instruction.md"}
        actual = {str(p.relative_to(public)) for p in public.rglob("*") if p.is_file()}
        if actual != allowed:
            raise AssertionError("unexpected public files (possible answer leak)")
        summaries[name] = {"parameters": params, "counts": independent["counts"], "leaders": independent["leaders"],
                           "changed_objects": len(changes), "reference": reference_report["summary"],
                           "independent_checks": len(independent["checks"]), "protected_semantic_bindings": protected_semantics,
                           "tests": tests}
    final_manifest = upstream.manifest(case)
    if final_manifest != initial_manifest:
        raise AssertionError("upstream sources changed during experiment")
    result = {"pass": True, "baseline_counts": independent_before["counts"], "tasks": summaries,
              "upstream_unchanged": True, "bindings_created_during_render": True,
              "limitations": ["Registered hospital schema/rules and controlled sentence templates only.",
                 "No LLM original labeling/refiner/writer or full original pipeline run; deterministic extension reuses selected upstream components.",
                 "Word checks require supplied wording. Valid free paraphrases are outside this MVP's accepted output contract.",
                 "Transfers into an existing secondary affiliation are rejected explicitly; no automatic merge policy yet.",
                 "Source appointment date anomaly is preserved, not silently repaired.",
                 "No PPT, training, generic dependency discovery, or real-document alignment claim.",
                 "Numeric count/rank cells are materialized, not Excel calculation formulas; total formula is protected.",
                 "Same-schema instance support is tested, not arbitrary-schema generality."]}
    save(output / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("artifacts"))
    parser.add_argument("--case", type=Path, default=upstream.CASE)
    args = parser.parse_args()
    result = build(json.loads(args.config.read_text()), args.out, args.case)
    print("Baseline counts:", result["baseline_counts"])
    for name, t in result["tasks"].items():
        print(name, "counts=", t["counts"], "leaders=", t["leaders"], "reference=PASS", "SQL checks=", t["independent_checks"])
        for n, report in t["tests"].items():
            print(" ", n, "PASS" if report["pass"] else "FAIL (expected)", "failed checks:", ", ".join(report["failed_checks"]) or "none")
    print("Detailed native-object results:", args.out.resolve() / "tasks/*/private/reports/*.json")


if __name__ == "__main__":
    main()
