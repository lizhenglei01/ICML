"""Independent oracle; reads hidden specification, never the reference answer."""
import argparse
import json
import math
import re
import shutil
import tempfile
from pathlib import Path

from docx import Document
from openpyxl import load_workbook

from .package import fingerprints
from .task import percent
from .upstream import FILES, patcher, verifier

NUMBER = r"[+-]?\d[\d,]*(?:\.\d+)?"


def close(a, b):
    return (isinstance(a, (int, float)) and not isinstance(a, bool)
            and math.isfinite(a) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9))


def direction_matches(text, expected):
    return expected in text and all(word not in text for word in {"增长", "下降", "持平"} - {expected})


def recalculate(path, book):
    # LibreOffice conversion can retain a forged formula cache. Invalidate all
    # formula caches on a disposable copy before invoking the upstream helper.
    with tempfile.TemporaryDirectory(prefix="cfbench-no-cache-") as tmp:
        copy = Path(tmp) / path.name
        shutil.copy2(path, copy)
        ops = [{"type": "xlsx_formula", "sheet": sheet.title,
                "cell": cell.coordinate, "formula": cell.value}
               for sheet in book for row in sheet for cell in row if cell.data_type == "f"]
        patcher().patch_xlsx(copy, ops)
        return verifier().office_recalc(copy)


def evaluate(oracle_path, submission):
    oracle = json.loads(Path(oracle_path).read_text())
    submission = Path(submission)
    results = []

    def add(category, key, ok, detail=""):
        results.append({"category": category, "id": key, "passed": bool(ok), "detail": detail})

    try:
        for f in FILES:
            if not (submission / f).is_file():
                raise ValueError(f"Missing deliverable: {f}")
        protected = oracle["protected_state"]
        actual_parts = fingerprints(submission, protected.get("editable_paragraphs"))
        expected_parts = protected["package_parts"]
        add("preservation", "package_members", set(actual_parts) == set(expected_parts))
        for name, digest in expected_parts.items():
            add("preservation", name, actual_parts.get(name) == digest)
        book = load_workbook(submission / FILES[0])
        doc = Document(submission / FILES[1])
        paras = verifier().paragraphs(doc)
        for cell, formula in protected["formulas"].items():
            add("preservation", f"formula_{cell}", book["Review"][cell].value == formula)
        add("preservation", "injected_source", close(book["Review"]["B4"].value, protected["injected_prior"]))
        add("preservation", "sec_source", close(book["Facts"]["I2"].value, protected["source_usd"]))
        unchanged = oracle["fact_delta"]["unchanged"]
        if "direction" in unchanged:
            add("preservation", "unchanged_semantic_state",
                direction_matches(paras[19], unchanged["direction"])
                and unchanged["qualifier"] in paras[19])
        # Actual LibreOffice calculation on a disposable copy; never trust caches.
        calculated = recalculate(submission / FILES[0], book)
        add("required", "independent_recalculation", calculated is not None)
        for check in oracle["required_updates"] + oracle.get("protected_outputs", []):
            kind = check["kind"]
            if kind == "calculated_cell":
                ok = calculated is not None and close(calculated[check["sheet"]][check["cell"]].value, check["value"])
            elif kind == "cell":
                ok = book[check["sheet"]][check["cell"]].value == check["value"]
            else:
                text = paras[check["paragraph"]]
                if "numbers" in check:
                    ok = re.findall(NUMBER, text) == check["numbers"]
                else:
                    ok = re.findall(NUMBER + "%", text) == [check["percent"]]
                    if "direction" in check:
                        ok = (ok and direction_matches(text, check["direction"]) and check["qualifier"] in text
                              and "陡增" not in text.replace("不能再描述成数量级异常的陡增", ""))
            add("required" if check.get("state_changed", True) else "preservation", check["id"], ok)
        if calculated is not None:
            s = calculated["Review"]
            current, prior = s["B3"].value, s["B4"].value
            yoy = (current - prior) / prior  # separately written arithmetic
            add("consistency", "summary_vs_workbook",
                re.findall(NUMBER, paras[3]) == [f"{current:,.1f}", f"{prior:,.1f}"])
            add("consistency", "percentage_vs_workbook",
                re.findall(NUMBER + "%", paras[18]) == [percent(yoy)]
                and re.findall(NUMBER + "%", paras[19]) == [percent(yoy, True)])
            direction = "增长" if yoy > 0 else "下降" if yoy < 0 else "持平"
            add("consistency", "direction_vs_workbook", direction_matches(paras[19], direction))
    except Exception as exc:
        add("integrity", "read_or_recalculate", False, f"{type(exc).__name__}: {exc}")
    categories = {}
    for category in sorted({r["category"] for r in results}):
        rows = [r for r in results if r["category"] == category]
        categories[category] = {"passed": sum(r["passed"] for r in rows), "total": len(rows)}
    return {"task_id": oracle["task_id"], "passed": bool(results) and all(r["passed"] for r in results),
            "categories": categories, "results": results}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("oracle", type=Path)
    p.add_argument("submission", type=Path)
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    report = evaluate(args.oracle, args.submission)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
