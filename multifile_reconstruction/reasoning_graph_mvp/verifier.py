"""Evaluator-only business knowledge. Never imported into generator prompts.

The target definition and gross-profit identity are manually confirmed business
knowledge. Symbolic equivalence certifies candidates against that definition;
it does not discover or independently establish accounting semantics.
"""
from itertools import product
import math
import random
import sympy as sp

from .graph import structure, paths, evaluate_path, path_info
from .operators import InvalidGraph, apply

R = sp.Symbol("revenue", positive=True)
C = sp.Symbol("cost", nonnegative=True)
P = sp.Symbol("profit", real=True)
SYMBOLS = {"revenue": R, "cost": C, "profit": P}
EXPECTED = (R - C) / R
CONCEPTS = {
    "gross_profit_margin": EXPECTED,
    "gross_profit": R-C, "gross_profit_amount": R-C, "profit": R-C,
    "revenue": R, "sales_revenue": R,
    "cost": C, "cost_of_revenue": C, "cost_of_goods_sold": C,
    # Explicit evaluator-side annotation approval, after the initial pilot.
    # This is a name for R, not a new target formula or a generator hint.
    "sales_revenue_reconstructed_from_gross_profit_and_cost": R,
    "cost_ratio": C/R, "cost_to_revenue_ratio": C/R,
    "unit_ratio": sp.Integer(1), "one": sp.Integer(1),
}


def normalize(expr):
    return sp.simplify(expr.subs(P, R-C))


def numeric_facts(known):
    if set(known) - set(SYMBOLS) or not {"revenue", "cost"} <= set(known):
        raise InvalidGraph("FACT_SEMANTICS", "Input facts are outside this registered business case")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in known.values()):
        raise InvalidGraph("FACT_TYPE", "Business amounts must be finite real numbers")
    out = {k: sp.Rational(str(v)) for k,v in known.items()}
    if out["revenue"] <= 0 or out["cost"] < 0:
        raise InvalidGraph("FACT_DOMAIN", "Revenue must be positive and cost nonnegative")
    if "profit" in out and out["profit"] != out["revenue"] - out["cost"]:
        raise InvalidGraph("INCONSISTENT_FACTS", "Supplied business facts violate a registered identity")
    return out


def trials(known):
    pairs = [(120,90), (100,50), (100,150), (100,100), (1,0), (7,3), (200,0)]
    rng = random.Random(20261119)
    pairs += [(rng.randint(1,500), rng.randint(0,1000)) for _ in range(24)]
    return [{**{"revenue": r, "cost": c}, **({"profit": r-c} if "profit" in known else {})} for r,c in pairs]


def semantic_walk(tree, quantities, warnings):
    if "fact" in tree:
        q=quantities.get(tree["fact"])
        if q:
            concept=q["concept"]
            if concept not in CONCEPTS or sp.simplify(normalize(SYMBOLS[tree["fact"]])-CONCEPTS[concept])!=0:
                raise InvalidGraph("GIVEN_SEMANTICS","Supplied fact cannot be assigned a different business meaning")
        return SYMBOLS[tree["fact"]]
    args = [semantic_walk(a, quantities, warnings) for a in tree["args"]]
    canonical_args = [normalize(x) for x in args]
    if tree["op"] == "DIV" and canonical_args[1].is_zero is not False:
        raise InvalidGraph("UNPROVEN_NONZERO_DIVISOR", "Nonzero divisor not established over the business domain")
    if tree["op"] == "SQRT" and canonical_args[0].is_nonnegative is not True:
        raise InvalidGraph("UNPROVEN_SQRT_DOMAIN", "Nonnegative square-root input not established over the business domain")
    expr = apply(tree["op"], args, symbolic=True)
    concept = quantities.get(tree["quantity"], {"concept": tree["quantity"]})["concept"]
    # Auxiliary nodes are mathematical quantities making no extra business claim.
    if concept != "auxiliary":
        if concept not in CONCEPTS:
            raise InvalidGraph("UNREGISTERED_CONCEPT", "Intermediate business concept needs independent review")
        if sp.simplify(normalize(expr) - CONCEPTS[concept]) != 0:
            raise InvalidGraph("INTERMEDIATE_SEMANTICS", "A claimed intermediate business meaning is incorrect")
    if not normalize(expr).free_symbols:
        warnings.append({"quantity": tree["quantity"], "kind": "constant_intermediate", "value": str(normalize(expr)),
                         "note": "May be a legitimate alternate expression but increases work; not itself a correctness failure."})
    return expr


def verify(graph, task, *, partial=False):
    known, target = task["known_facts"], task["target"]
    result = {"accepted": False, "status": "REJECTED", "errors": [], "valid_paths": [], "rejected_paths": [],
              "knowledge_origin": "Manually registered target definition, supplied-field meanings, gross-profit identity and domains; not learned by this validator."}
    try:
        actual = numeric_facts(known)
        if target != "gross_profit_margin": raise InvalidGraph("UNSUPPORTED_TARGET", "No confirmed rule for target")
        quantities, _, unresolved = structure(graph, known, target, partial=partial)
        if partial and unresolved:
            result.update(status="INCOMPLETE", unresolved=unresolved)
            return result
        all_paths = paths(graph, known)
        if not all_paths: raise InvalidGraph("NO_PATH", "No executable proof reaches target")
        for index, tree in enumerate(all_paths, 1):
            info = path_info(tree)
            report = {"path_id": index, "tree": tree, "input_set": sorted(info["inputs"]),
                      "operator_count": len(info["operators"]), "dependency_depth": info["depth"],
                      "intermediate_quantities": sorted(info["intermediates"]), "warnings": []}
            try:
                value, output_unit = evaluate_path(tree, quantities, actual)
                if output_unit != 0:
                    raise InvalidGraph("TARGET_UNIT", "Target metric must be dimensionless")
                expr = semantic_walk(tree, quantities, report["warnings"])
                report["symbolic_expression"] = str(expr)
                if sp.simplify(normalize(expr) - EXPECTED) != 0:
                    raise InvalidGraph("TARGET_SEMANTICS", "Expression does not satisfy the confirmed target definition")
                report["symbolically_equivalent"] = True
                perturbations = []
                for sample in trials(known):
                    numbers = {k: sp.Rational(v) for k,v in sample.items()}
                    observed, _ = evaluate_path(tree, quantities, numbers)
                    expected = EXPECTED.subs({R:numbers["revenue"], C:numbers["cost"]})
                    ok = observed.is_real is True and observed.is_finite is True and abs(float(observed-expected)) < 1e-12
                    perturbations.append({"facts": sample, "observed": float(observed), "expected": float(expected), "pass": ok})
                    if not ok: raise InvalidGraph("PERTURBATION_FAILURE", "Candidate fails a business-consistent perturbation")
                report.update(result=float(value), exact_result=str(value), perturbations=perturbations, accepted=True)
                result["valid_paths"].append(report)
            except InvalidGraph as exc:
                report.update(accepted=False, error_code=exc.code, error=str(exc))
                result["rejected_paths"].append(report)
        sets = {frozenset(p["input_set"]) for p in result["valid_paths"]}
        minimal = [s for s in sets if not any(t < s for t in sets)]
        result["minimal_input_sets"] = sorted([sorted(s) for s in minimal])
        result["candidate_path_count"] = len(all_paths)
        result["accepted"] = bool(result["valid_paths"]) and not result["rejected_paths"]
        result["status"] = "ACCEPTED" if result["accepted"] else "REJECTED"
    except InvalidGraph as exc:
        result["errors"].append({"code":exc.code,"message":str(exc)})
    except (TypeError, KeyError, ValueError, ZeroDivisionError) as exc:
        result["errors"].append({"code":"MALFORMED_GRAPH","message":str(exc)})
    return result


def safe_feedback(report):
    """Only diagnostic codes/counts, never expected formulas, values or trials."""
    return {"status":report["status"], "valid_path_count":len(report["valid_paths"]),
            "error_codes":sorted({e["code"] for e in report["errors"]} |
                                 {p["error_code"] for p in report["rejected_paths"]}),
            "unresolved":report.get("unresolved",[])}
