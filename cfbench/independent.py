"""Independent rational arithmetic audit. No generator/rounding rules imported."""
import json
import math
import re
from fractions import Fraction

from docx import Document
from openpyxl import load_workbook


def rounded(value, places=1, grouping=False):
    """Round exact fractions half away from zero, using integer arithmetic."""
    sign = "-" if value < 0 else ""
    value = abs(value) * (10 ** places)
    whole, remainder = divmod(value.numerator, value.denominator)
    if 2 * remainder >= value.denominator:
        whole += 1
    units, tail = divmod(whole, 10 ** places)
    head = f"{units:,}" if grouping else str(units)
    return sign + head + (f".{tail:0{places}d}" if places else "")


def audit(task, original_workbook):
    parameters = json.loads((task / "private/parameters.json").read_text())
    truth = json.loads((task / "private/truth.json").read_text())
    oracle = json.loads((task / "private/oracle.json").read_text())
    original = load_workbook(original_workbook, data_only=False)
    ref = task / "private/reference"
    reference = load_workbook(ref / "unit_review.xlsx", data_only=True)
    paras = [p.text for p in Document(ref / "management_discussion.docx").paragraphs]
    usd = Fraction(str(original["Facts"]["I2"].value))
    prior = Fraction(parameters["prior_millions"])
    millions = usd / 1000000
    yoy = (millions - prior) / prior
    signed = rounded(yoy * 100) + "%"
    magnitude = rounded(abs(yoy) * 100) + "%"
    direction = "增长" if millions > prior else "下降" if millions < prior else "持平"
    qualifier = "温和增长" if 0 < yoy < Fraction(1, 10) else direction
    expected = {"usd": int(usd), "millions": float(millions), "prior": float(prior),
                "yoy": float(yoy), "direction": direction, "qualifier": qualifier}
    results = []

    def check(key, observed, wanted, numeric=False):
        ok = (isinstance(observed, (int, float)) and not isinstance(observed, bool)
              and math.isclose(observed, wanted, rel_tol=1e-12, abs_tol=1e-12)) if numeric else observed == wanted
        results.append({"id": key, "passed": bool(ok), "observed": observed, "expected": wanted})

    for key, value in expected.items():
        check("truth_" + key, truth["counterfactual"][key], value, isinstance(value, (int, float)))
    for cell, value in {"B2": int(usd), "B3": float(millions), "B4": float(prior), "B5": float(yoy)}.items():
        check("reference_" + cell, reference["Review"][cell].value, value, True)
    check("word_summary", re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?", paras[3]),
          [rounded(millions, grouping=True), rounded(prior, grouping=True)])
    check("word_numeric_percent", re.findall(r"[+-]?\d+(?:\.\d+)?%", paras[18]), [signed])
    check("word_semantic_percent", re.findall(r"[+-]?\d+(?:\.\d+)?%", paras[19]), [magnitude])
    check("word_direction", direction in paras[19] and all(x not in paras[19] for x in {"增长", "下降", "持平"} - {direction}), True)
    checks = {c["id"]: c for c in oracle["required_updates"] + oracle.get("protected_outputs", [])}
    check("oracle_yoy", checks["recomputed_yoy"]["value"], float(yoy), True)
    check("oracle_summary", checks["summary"]["numbers"], [rounded(millions, grouping=True), rounded(prior, grouping=True)])
    check("oracle_numeric_percent", checks["numeric_claim"]["percent"], signed)
    check("oracle_semantic_percent", checks["semantic_claim"]["percent"], magnitude)
    check("oracle_direction", checks["semantic_claim"]["direction"], direction)
    check("oracle_qualifier", checks["semantic_claim"]["qualifier"], qualifier)
    return {"passed": all(r["passed"] for r in results),
            "method": "Exact Fraction arithmetic from original Facts!I2 and saved intervention; independent integer rounding; no derive/percent imports",
            "exact_yoy": {"numerator": yoy.numerator, "denominator": yoy.denominator},
            "results": results}
