"""Data-driven native-file oracle. No physician/department cases in this evaluator."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from lxml import etree as ET
from openpyxl import load_workbook

from .office import NS_S, NS_W, sheet_parts, displayed


def structural_snapshot(directory, bindings, filenames=("hospital.xlsx", "review.docx")):
    result = {}
    for filename in filenames:
        path = Path(directory) / filename
        paths = sheet_parts(path) if filename.endswith("xlsx") else {}
        with ZipFile(path) as z:
            if z.testzip() is not None or len(z.namelist()) != len(set(z.namelist())):
                raise ValueError("corrupt or duplicate ZIP members")
            for part in sorted(z.namelist()):
                data = z.read(part)
                if part.endswith(".xml") or part.endswith(".rels"):
                    root = ET.fromstring(data)
                    for b in bindings:
                        if b["file"] != filename:
                            continue
                        if b["kind"] == "xlsx" and paths[b["sheet"]] == part:
                            c = root.find(f".//s:c[@r='{b['cell']}']", NS_S)
                            if c is None:
                                raise ValueError(f"missing bound cell {b['id']}")
                            # Preserve cell style and formula; mask value/storage representation only.
                            c.attrib.pop("t", None)
                            for child in list(c):
                                if child.tag != "{" + NS_S["s"] + "}f":
                                    c.remove(child)
                        elif b["kind"] == "docx" and part == "word/document.xml":
                            p = root.findall("w:body/w:p", NS_W)[b["paragraph"]]
                            for t in p.findall(".//w:t", NS_W):
                                t.text = "MASK"
                    data = ET.tostring(root, method="c14n")
                result[filename + ":" + part] = hashlib.sha256(data).hexdigest()
    return result


def generate(source, bindings, before, after, names, filenames=("hospital.xlsx", "review.docx")):
    specs = []
    for b in bindings:
        old, new = displayed(b, before, names), displayed(b, after, names)
        specs.append(dict(b, category="required" if old != new else "protected", before=old, expected=new))
    by_fact = {}
    for s in specs:
        by_fact.setdefault(s["fact_id"], []).append(s["id"])
    groups = [{"fact_id": f, "bindings": ids} for f, ids in by_fact.items()
              if any(i.startswith("xlsx:") for i in ids) and any(i.startswith("docx:") for i in ids)]
    return {"schema_version": 1, "checks": specs, "consistency": groups,
            "files": list(filenames),
            "structure": structural_snapshot(source, bindings, filenames),
            "policy": "Exact known template content and masked native OOXML preservation; not a semantic paraphrase judge."}


def evaluate(submission, oracle):
    if isinstance(oracle, (str, Path)):
        oracle = json.loads(Path(oracle).read_text())
    results = []
    filenames = oracle.get("files", ["hospital.xlsx", "review.docx"])
    try:
        wb = load_workbook(Path(submission) / filenames[0], data_only=False)
        doc = Document(Path(submission) / filenames[1])
    except Exception as e:
        return {"pass": False, "checks": [{"id": "native_file_load", "category": "structure",
                "pass": False, "error": type(e).__name__ + ": " + str(e)}]}
    for s in oracle["checks"]:
        check = {"id": s["id"], "fact_id": s["fact_id"], "category": s["category"], "expected": s["expected"]}
        try:
            actual = wb[s["sheet"]][s["cell"]].value if s["kind"] == "xlsx" else doc.paragraphs[s["paragraph"]].text
            numeric = oracle.get("numeric_value_equality", False) and type(actual) in (int, float) and type(s["expected"]) in (int, float)
            check.update(actual=actual, **{"pass": actual == s["expected"] and (numeric or type(actual) is type(s["expected"]))})
        except Exception as e:
            check.update({"pass": False, "error": str(e)})
        results.append(check)
    checked = {c["id"]: c for c in results}
    for group in oracle["consistency"]:
        # All registered representations must agree with the same canonical expected fact.
        results.append({"id": "consistency:" + group["fact_id"], "category": "consistency",
                        "bindings": group["bindings"], "pass": all(checked[i]["pass"] for i in group["bindings"])})
    try:
        actual_structure = structural_snapshot(submission, oracle["checks"], filenames)
        for part in sorted(set(actual_structure) | set(oracle["structure"])):
            results.append({"id": "structure:" + part, "category": "structure",
                            "pass": actual_structure.get(part) == oracle["structure"].get(part)})
    except Exception as e:
        results.append({"id": "native_structure", "category": "structure", "pass": False, "error": str(e)})
    summary = {}
    for c in results:
        x = summary.setdefault(c["category"], {"passed": 0, "total": 0})
        x["total"] += 1
        x["passed"] += int(c["pass"])
    return {"pass": all(c["pass"] for c in results), "summary": summary, "checks": results}
