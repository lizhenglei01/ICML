"""Small executable business graph and bounded evidence certificates.

Depth is minimized over the explicitly registered operations/rewrites, not all
possible human/LLM reasoning. File necessity is tested with alternative legal
worlds matching observed facts. These are separate claims.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, product


@dataclass(frozen=True)
class Rule:
    output: str
    inputs: tuple[str, ...]
    operator: str
    note: str = ""


def scenario():
    return {"case": "采购放行 / Procurement release", "unit": "万元 / CNY 10k",
            "cap_before": 75, "cap_after": 100,
            "projects": {
                "Alpha": {"invoice_1": 120, "invoice_2": 80, "advance": 50, "factor": 0.6,
                          "insured": True, "clear_title": True},
                "Beta": {"invoice_1": 90, "invoice_2": 70, "advance": 100, "factor": 0.6,
                         "insured": True, "clear_title": True}},
            "origin": "New controlled synthetic business case; not upstream benchmark assets or discovered business rules."}


def values(project, cap_before, cap_after):
    v = {k: Fraction(str(x)) if not isinstance(x, bool) else x for k, x in project.items()}
    v["gross"] = v["invoice_1"] + v["invoice_2"]
    v["net"] = v["gross"] - v["advance"]
    v["reserve"] = v["net"] * v["factor"]
    v["cap_before"], v["cap_after"] = Fraction(cap_before), Fraction(cap_after)
    v["old_decision"] = v["reserve"] <= cap_before and v["insured"] and v["clear_title"]
    v["cap_div_factor"] = v["cap_after"] / v["factor"]
    v["gross_limit"] = v["advance"] + v["cap_div_factor"]
    v["money_ok"] = v["reserve"] <= v["cap_after"]
    v["decision"] = v["money_ok"] and v["insured"] and v["clear_title"]
    return v


def rules(v, *, allow_rewrites=True):
    result = [Rule("gross", ("invoice_1", "invoice_2"), "add"),
              Rule("net", ("gross", "advance"), "subtract"),
              Rule("reserve", ("net", "factor"), "multiply"),
              Rule("money_ok", ("reserve", "cap_after"), "less_equal"),
              Rule("decision", ("money_ok", "insured", "clear_title"), "and")]
    if allow_rewrites:
        # Valid only for positive factors; audited with symbolic equivalence.
        result += [Rule("cap_div_factor", ("cap_after", "factor"), "divide"),
                   Rule("gross_limit", ("advance", "cap_div_factor"), "add"),
                   Rule("money_ok", ("gross", "gross_limit"), "less_equal", "positive-factor algebraic shortcut"),
                   Rule("money_ok", ("net", "cap_div_factor"), "less_equal", "positive-factor algebraic shortcut")]
    # Actual-instance Boolean/monotonic shortcuts are intentionally included.
    for key in ("money_ok", "insured", "clear_title"):
        if not v[key]:
            result.append(Rule("decision", (key,), "and_short_circuit"))
    if v["old_decision"] and v["cap_after"] >= v["cap_before"]:
        result.append(Rule("decision", ("old_decision", "cap_before", "cap_after"), "cap_relaxation_monotonicity"))
    return result


def shortest_proof(observations, v, *, allow_rewrites=True):
    """All known value nodes start at depth zero; exhaustive relaxation of rules."""
    known = {"cap_before": {"depth": 0, "files": [], "rule": "shared change instruction"},
             "cap_after": {"depth": 0, "files": [], "rule": "shared change instruction"}}
    for obs in observations:
        candidate = {"depth": 0, "files": [obs["file"]], "rule": "public evidence"}
        if obs["node"] not in known:
            known[obs["node"]] = candidate
    for _ in range(30):
        changed = False
        for r in rules(v, allow_rewrites=allow_rewrites):
            if not all(i in known for i in r.inputs):
                continue
            d = 1 + max(known[i]["depth"] for i in r.inputs)
            fs = sorted({f for i in r.inputs for f in known[i]["files"]})
            candidate = {"depth": d, "files": fs, "rule": r.operator,
                         "inputs": list(r.inputs), "note": r.note}
            old = known.get(r.output)
            if old is None or (d, len(fs)) < (old["depth"], len(old["files"])):
                known[r.output] = candidate
                changed = True
        if not changed:
            return {"minimum_depth": known.get("decision", {}).get("depth"), "proof": known,
                    "scope": "Minimum over registered semantic operators, algebraic rewrites and guarded Boolean/monotonic shortcuts; not an unrestricted minimum over all arithmetic programs."}
    raise AssertionError("Rule relaxation did not converge")


def possible_worlds():
    # Explicit bounded audit domain; actual Alpha is included. A countermodel
    # proves non-identifiability even outside this finite domain, provided that
    # the displayed business constraints permit the witness. Sufficiency itself
    # also has an executable registered-rule proof above.
    names = ("invoice_1", "invoice_2", "advance", "factor", "insured", "clear_title")
    domain = ((90, 120, 150), (60, 80, 100), (0, 50, 100), (0.4, 0.6, 0.8), (False, True), (False, True))
    return [dict(zip(names, row)) for row in product(*domain)]


def evidence_subsets(observations, actual, cap_before, cap_after, files):
    worlds = [(world, values(world, cap_before, cap_after)) for world in possible_worlds()]
    results = []
    for size in range(len(files) + 1):
        for subset in combinations(files, size):
            available = [o for o in observations if o["file"] in subset]
            matching = [(world, v) for world, v in worlds
                        if all(v[o["node"]] == actual[o["node"]] for o in available)]
            if not matching:
                raise AssertionError("Observed evidence excludes actual registered world")
            answers = {v["decision"] for _, v in matching}
            witnesses = {}
            for answer in answers:
                world, v = next((w, v) for w, v in matching if v["decision"] == answer)
                witnesses[str(answer)] = {"base_facts": world, "reserve": float(v["reserve"]),
                                          "old_decision": v["old_decision"], "new_decision": answer}
            results.append({"files": list(subset), "matching_worlds": len(matching),
                            "possible_answers": sorted(answers), "sufficient_in_audit_domain": len(answers) == 1,
                            "counterexamples": witnesses if len(answers) > 1 else {}})
    sufficient = [len(r["files"]) for r in results if r["sufficient_in_audit_domain"]]
    minimum = min(sufficient) if sufficient else None
    return {"minimum_office_files_in_audit_domain": minimum, "subsets": results,
            "domain_size": len(worlds), "scope": "Finite legal-world identifiability audit plus executable proof. Missing-file ambiguity is supported by concrete countermodels. Shared change instruction is always available and is not counted as an Office file."}


def verify_rewrites():
    import sympy as s
    gross, advance, factor, cap = s.symbols("gross advance factor cap", positive=True)
    difference = s.simplify((gross-advance)*factor-cap - factor*(gross-(advance+cap/factor)))
    if difference != 0:
        raise AssertionError("Invalid registered algebraic rewrite")
    return {"pass": True, "identity": "(gross-advance)*factor-cap = factor*(gross-(advance+cap/factor))",
            "condition": "factor > 0, so the sign and <= comparison are preserved", "backend": "sympy.simplify"}
