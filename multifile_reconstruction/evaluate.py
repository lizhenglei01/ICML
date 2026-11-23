"""Reconstruction grading: exact typed values, with explicit string-review cases."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from leaves(child, path + "/" + escaped)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, path + "/" + str(index))
    else:
        yield path, value


def normalize_string(value):
    return re.sub(r"\s+", " ", value.strip()).casefold().rstrip(".")


def evaluate(truth, submission, bindings):
    wanted, actual = dict(leaves(truth)), dict(leaves(submission))
    locations = {x["path"]: x for x in bindings}
    checks = []
    by_file = Counter()
    correct_by_file = Counter()
    for path, expected in wanted.items():
        observed = actual.get(path)
        present = path in actual
        same_type = present and type(observed) is type(expected)
        ok = same_type and observed == expected
        if same_type and isinstance(expected, str):
            ok = normalize_string(expected) == normalize_string(observed)
        status = "PASS" if ok else ("MISSING" if not present else "MISMATCH")
        item = {"path": path, "expected": expected, "observed": observed,
                "status": status, "source": locations[path]}
        if not ok and same_type and isinstance(expected, str):
            item["semantic_review_needed"] = True
        checks.append(item)
        by_file[locations[path]["file"]] += 1
        correct_by_file[locations[path]["file"]] += ok
    extra = sorted(set(actual) - set(wanted))
    correct = sum(c["status"] == "PASS" for c in checks)
    return {"pass": correct == len(wanted) and not extra,
            "correct": correct, "total": len(wanted), "extra_paths": extra,
            "by_primary_file": {name: {"correct": correct_by_file[name], "total": count}
                                for name, count in sorted(by_file.items())},
            "semantic_review_count": sum(c.get("semantic_review_needed", False) for c in checks),
            "checks": checks}


def free_form_report(candidate):
    valid = isinstance(candidate, dict)
    return {"pass": None, "status": "NOT_SCORED" if valid else "INVALID_JSON_OBJECT",
            "valid_json_object": valid, "correct": None, "total": None,
            "reason": "No organization/schema was supplied. Semantic alignment of model-chosen keys to hidden facts is not implemented; exact-path grading would be invalid."}


def protocol_has_schema(package):
    path = Path(package) / "private/input_protocol.json"
    # Historical experiments used the public schema. Never relabel their scores.
    return json.loads(path.read_text())["schema_provided"] if path.exists() else True


def grade_files(package, submission, output):
    package = Path(package)
    truth = json.loads((package / "private/truth.json").read_text())
    bindings = json.loads((package / "private/bindings.json").read_text())
    candidate = json.loads(Path(submission).read_text())
    report = evaluate(truth, candidate, bindings) if protocol_has_schema(package) else free_form_report(candidate)
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
