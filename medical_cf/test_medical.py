"""Behavior tests: independent arithmetic, new parameters, new instance, oracle mutations."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from . import upstream
from .facts import compute, intervene, validate
from .independent import verify, verify_files
from .office import patch
from .oracle import evaluate
from .run import build, save


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("medical-cf")
    config = json.loads(Path(__file__).with_name("config.json").read_text())
    result = build(config, root, recalculate=False)
    return root, result


def test_known_numbers_and_ties(generated):
    _, r = generated
    assert r["baseline_counts"] == {1: 5, 2: 3, 3: 1}
    t = r["tasks"]["john_to_surgery"]
    assert t["counts"] == {1: 4, 2: 4, 3: 1}
    assert t["leaders"] == [1, 2]
    assert r["tasks"]["john_to_psychiatry"]["counts"] == {1: 4, 2: 3, 3: 2}
    assert r["tasks"]["john_to_psychiatry"]["leaders"] == [1]


@pytest.mark.parametrize("variant", ["correct", "excel_only", "stale_semantics", "incorrect_semantic_flip",
    "unrelated_content", "wrong_count", "broken_formula", "broken_structure", "invalid_business_state"])
def test_oracle_expected_outcomes(generated, variant):
    root, r = generated
    for task_name, task in r["tasks"].items():
        report = json.loads((root / "tasks" / task_name / "private/reports" / (variant + ".json")).read_text())
        expected = variant == "correct" or (variant == "stale_semantics" and task_name == "john_to_psychiatry")
        assert report["pass"] is expected
        assert report["expectation_met"]
        assert report["checks"]


def test_semantics_stay_protected_when_counts_change(generated):
    root, r = generated
    task = root / "tasks/john_to_psychiatry/private"
    oracle = json.loads((task / "oracle.json").read_text())
    leaders = [c for c in oracle["checks"] if c["fact_id"].endswith(":leader") or c["fact_id"] == "departments:leaders"]
    assert leaders and all(c["category"] == "protected" for c in leaders)
    assert r["tasks"]["john_to_psychiatry"]["tests"]["stale_semantics"]["pass"]


def test_broken_formula_rejected_even_with_same_total(generated):
    root, _ = generated
    report = json.loads((root / "tasks/john_to_surgery/private/reports/broken_formula.json").read_text())
    failures = [c for c in report["checks"] if not c["pass"]]
    assert len(failures) == 1 and failures[0]["id"].startswith("structure:hospital.xlsx:xl/worksheets/")


def test_all_supported_interventions_against_sql():
    rows, schema = upstream.load()
    source_copy = deepcopy(rows)
    tested = rejected = 0
    for doctor in rows["Physician"]:
        for dept in rows["Department"]:
            try:
                changed = intervene(rows, schema, doctor["EmployeeID"], dept["DepartmentID"])
            except ValueError as e:
                assert "no-op" in str(e) or "already has an affiliation" in str(e)
                rejected += 1
                continue
            assert verify(changed, schema, compute(changed, schema))["pass"]
            assert changed["Appointment"] == rows["Appointment"]
            assert changed["Patient"] == rows["Patient"]
            tested += 1
    assert tested == 16 and rejected == 11
    assert rows == source_copy


def test_sql_catches_corrupt_generator_truth():
    rows, schema = upstream.load()
    facts = compute(rows, schema)
    facts["department:1:count"]["value"] = 999
    with pytest.raises(AssertionError):
        verify(rows, schema, facts)


def test_native_assertions_do_not_trust_oracle(generated, tmp_path):
    root, _ = generated
    source = root / "tasks/john_to_surgery/private"
    import shutil
    target = tmp_path / "bad-reference"
    shutil.copytree(source / "reference", target)
    bindings = json.loads((source / "bindings.json").read_text())
    b = next(b for b in bindings if b["kind"] == "docx" and b["fact_id"] == "department:1:count")
    patch(target, [(b, "General Medicine has 999 primary physicians.")])
    rows = json.loads((source / "after_data.json").read_text())
    _, schemas = upstream.load()
    sql = verify(rows, schemas, compute(rows, schemas))
    with pytest.raises(AssertionError):
        verify_files(target, rows, sql)


def test_constraints_reject_invalid_fk_primary_and_duplicates():
    rows, schema = upstream.load()
    bad = deepcopy(rows)
    bad["Affiliated_With"][0]["Department"] = 999
    with pytest.raises(ValueError, match="FK"):
        validate(bad, schema)
    bad = deepcopy(rows)
    bad["Affiliated_With"][0]["PrimaryAffiliation"] = 0
    with pytest.raises(ValueError, match="exactly one"):
        validate(bad, schema)
    bad = deepcopy(rows)
    bad["Affiliated_With"].append(deepcopy(bad["Affiliated_With"][0]))
    with pytest.raises(ValueError, match="PK"):
        validate(bad, schema)


def test_new_parameter_zero_count_no_validator_changes(tmp_path):
    config = {"interventions": [{"id": "molly_to_surgery", "physician": 9, "department": 2}]}
    r = build(config, tmp_path, run_negatives=False, recalculate=False)
    assert r["tasks"]["molly_to_surgery"]["counts"] == {1: 5, 2: 4, 3: 0}
    assert evaluate(tmp_path / "tasks/molly_to_surgery/private/reference", tmp_path / "tasks/molly_to_surgery/private/oracle.json")["pass"]


def test_different_same_schema_instance(tmp_path):
    """New values, reordered rows, renamed entities, and an empty new department."""
    rows, schema = upstream.load()
    rows["Physician"].append({"EmployeeID": 10, "Name": "Additional Doctor", "Position": "Staff", "SSN": 111111111})
    rows["Department"].append({"DepartmentID": 4, "Name": "New Empty Department"})
    rows["Department"][0]["Name"] = "Renamed Medicine"
    rows["Affiliated_With"].append({"Physician": 10, "Department": 1, "PrimaryAffiliation": 1})
    for table in rows:
        rows[table].reverse()
    case = tmp_path / "case"
    save(case / "schema.json", json.loads((upstream.CASE / "schema.json").read_text()))
    for table, records in rows.items():
        save(case / "tables" / (table + ".json"), records)
    r = build({"interventions": [{"id": "new_doctor", "physician": 10, "department": 4}]},
              tmp_path / "out", case=case, run_negatives=False, recalculate=False)
    assert r["baseline_counts"] == {1: 6, 2: 3, 3: 1, 4: 0}
    assert r["tasks"]["new_doctor"]["counts"] == {1: 5, 2: 3, 3: 1, 4: 1}
    assert r["upstream_unchanged"]


def test_public_answer_separation(generated):
    root, _ = generated
    for public in (root / "tasks").glob("*/public"):
        assert {str(p.relative_to(public)) for p in public.rglob("*") if p.is_file()} == {
            "input/hospital.xlsx", "input/review.docx", "instruction.md"}


def test_upstream_sources_unchanged(generated):
    root, _ = generated
    assert json.loads((root / "source_manifest.json").read_text()) == upstream.manifest()
