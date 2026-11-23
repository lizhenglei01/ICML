"""Small evidence-checked scenario, not a general Office dependency engine."""
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from docx import Document
from openpyxl import load_workbook

from .upstream import COHORT, FILES, TASK, verifier

RULES_PATH = Path(__file__).with_name("obm036_rules.json")


def registered_rules():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


_BINDINGS = registered_rules()["bindings"]
SUMMARY = _BINDINGS["summary_paragraph"]
NUMERIC = _BINDINGS["percentage_paragraph"]
CONCLUSION = _BINDINGS["interpretation_paragraph"]


def read_evidence():
    ref = COHORT / "private/reference" / TASK
    book = load_workbook(ref / FILES[0])
    paras = verifier().paragraphs(Document(ref / FILES[1]))
    gold = json.loads((COHORT / "private/gold" / f"{TASK}.json").read_text())
    assert book["Facts"]["I2"].value == book["Review"]["B2"].value == 2466000000
    assert book["Facts"]["G2"].value == "AccountsPayableCurrent"  # not revenue!
    assert book["Review"]["B3"].value == "=B2/1000000"
    assert book["Review"]["B5"].value == "=B3/B4-1"
    assert book["Review"]["B4"].value == 2300
    assert "（本期－上期）除以上期" in paras[20]
    assert "个位数百分比" in paras[23] and "温和增长" in paras[23]
    assert "7.2%" in paras[NUMERIC] and "温和增长" in paras[CONCLUSION]
    assert "2,300" in book["Approval"]["B4"].value
    assert gold["metrics"]["usd"] == book["Facts"]["I2"].value
    return ref, book, paras


def derive(usd, prior):
    usd, prior = Decimal(str(usd)), Decimal(str(prior))
    if prior <= 0:
        raise ValueError("Comparator must be positive")
    rules = registered_rules()
    values = {"usd": usd, "prior": prior}
    # Execute the two registered arithmetic rules, never arbitrary expressions.
    for rule in rules["numeric_rules"]:
        if rule["operation"] == "divide_constant":
            result = values[rule["input"]] / Decimal(rule["constant"])
        elif rule["operation"] == "ratio_minus_one":
            result = values[rule["numerator"]] / values[rule["denominator"]] - 1
        else:
            raise ValueError("Unsupported registered arithmetic operation")
        values[rule["output"]] = result
    millions, yoy = values["millions"], values["yoy"]
    # Upstream only supports 'mild growth' for single-digit positive percentages.
    semantics = rules["semantic_rule"]
    if yoy > 0 and yoy >= Decimal(semantics["positive_upper_exclusive"]):
        raise ValueError("Outside the source-supported mild-growth domain")
    direction = semantics["labels"]["positive" if yoy > 0 else "negative" if yoy < 0 else "zero"]
    return {"usd": int(usd), "millions": float(millions), "prior": float(prior),
            "yoy": float(yoy), "direction": direction,
            "qualifier": semantics["positive_qualifier"] if yoy > 0 else direction}


def percent(value, magnitude=False):
    v = Decimal(str(value)) * 100
    if magnitude:
        v = abs(v)
    return f"{v.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):.1f}%"


def paragraph_targets(paras, state):
    p = state["prior"]
    signed, magnitude = percent(state["yoy"]), percent(state["yoy"], True)
    conclusion = (f"核对单位后，当前值较模拟上期约{state['direction']} {magnitude}，"
                  + ("属于温和增长；" if state["direction"] == "增长" else "")
                  + "不能再描述成数量级异常的陡增。")
    return {
        SUMMARY: paras[SUMMARY].replace("2,300.0", f"{p:,.1f}"),
        NUMERIC: paras[NUMERIC].replace("7.2%", signed),
        CONCLUSION: conclusion,
    }


def graph(book, paras):
    return {
        "scope": "Manually enumerated, source-checked OBM-036 dependency graph",
        "validation": "Script assertions + independent arithmetic/recalculation tests; no human sign-off claimed",
        "nodes": {
            "usd": {"location": "unit_review.xlsx/Facts!I2 and Review!B2", "role": "protected"},
            "millions": {"location": "unit_review.xlsx/Review!B3", "rule": "usd/1000000",
                         "evidence": book["Review"]["B3"].value},
            "prior": {"location": "unit_review.xlsx/Review!B4", "role": "intervention; already injected"},
            "yoy": {"location": "unit_review.xlsx/Review!B5", "rule": "millions/prior-1",
                    "evidence": [book["Review"]["B5"].value, paras[20]]},
            "comparator_label": {"location": "unit_review.xlsx/Approval!B4", "rule": "format prior"},
            "summary": {"location": f"management_discussion.docx/paragraph[{SUMMARY}]", "rule": "format millions and prior"},
            "numeric_claim": {"location": f"management_discussion.docx/paragraph[{NUMERIC}]", "rule": "format yoy, one decimal percent"},
            "semantic_claim": {"location": f"management_discussion.docx/paragraph[{CONCLUSION}]",
                               "rule": "sign(yoy); mild growth only for 0<yoy<0.1",
                               "evidence": [paras[18], paras[19], paras[23]],
                               "added_rule": "Negative yoy means decline (author-defined arithmetic interpretation)"},
        },
        "edges": registered_rules()["edges"],
        "registered_rules": registered_rules(),
        "historical_protection": "Document signed record paragraphs 50–54 retain original approval, explicitly historical in new instruction",
    }
