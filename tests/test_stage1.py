"""Independent audits, parameter-only task construction, adversarial outputs."""
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from openpyxl import load_workbook

import cfbench.build as builder
from cfbench.evaluate import evaluate
from cfbench.independent import audit
from cfbench.parameters import load_parameters
from cfbench.smoke import implementation_snapshot
from cfbench.upstream import COHORT, FILES, TASK, provenance, sha256

ROOT = Path(os.environ.get("CFBENCH_ARTIFACT_ROOT", str(builder.DEFAULT_OUT)))
PARAMETERS = load_parameters(ROOT / "parameters.json")
NAMES = [p["id"] for p in PARAMETERS]


@pytest.mark.parametrize("name", NAMES)
def test_independent_truth_and_native_office(name):
    task = ROOT / name
    assert audit(task, COHORT / "private/reference" / TASK / FILES[0])["passed"]
    book = load_workbook(task / "private/reference" / FILES[0])
    assert book.sheetnames == ["Overview", "Facts", "Review", "Approval"]
    assert book["Facts"].max_row == 1001
    assert book["Facts"]["G2"].value == "AccountsPayableCurrent"
    assert book["Review"]["B3"].value == "=B2/1000000"
    assert book["Review"]["B5"].value == "=B3/B4-1"
    initial = load_workbook(task / "public/input" / FILES[0], data_only=True)
    assert initial["Review"]["B5"].value is None
    doc = Document(task / "private/reference" / FILES[1])
    baseline = Document(COHORT / "private/reference" / TASK / FILES[1])
    assert len(doc.paragraphs) == 55
    assert all(doc.paragraphs[i].text == baseline.paragraphs[i].text
               for i in range(55) if i not in {3, 18, 19})


def test_actual_upstream_blob_provenance_and_frozen_source():
    manifest = provenance()
    assert all(item["matches_git_blob"] for item in manifest["files"])
    proof = json.loads((ROOT / "logs/parameterization_proof.json").read_text())
    assert proof["implementation_before"] == proof["implementation_after"] == implementation_snapshot()
    assert proof["upstream_before"] == proof["upstream_after"]
    assert proof["all_upstreams_unchanged_and_clean"]
    assert proof["all_tasks_share_graph"]
    assert proof["task_count"] == len(NAMES)
    for name in NAMES:
        source = json.loads((ROOT / name / "private/source_manifest.json").read_text())
        for relative, digest in source["generated_files"].items():
            assert sha256((ROOT / name / relative).read_bytes()) == digest


@pytest.mark.parametrize("name", NAMES)
def test_public_bundle_isolated_and_uses_upstream_document(name):
    task = ROOT / name
    assert (task / "public/input" / FILES[1]).read_bytes() == (
        COHORT / "private/reference" / TASK / FILES[1]).read_bytes()
    with ZipFile(ROOT / f"CFBench_OBM036_{name}_public.zip") as z:
        assert set(z.namelist()) == {"instruction.md", "ATTRIBUTION.txt", "UPSTREAM_LICENSE.txt",
                                     *(f"input/{f}" for f in FILES)}
    with ZipFile(task / "public/input" / FILES[0]) as z:
        with ZipFile(COHORT / "private/reference" / TASK / FILES[0]) as original:
            assert set(z.namelist()) == set(original.namelist())
            changed = {n for n in z.namelist() if z.read(n) != original.read(n)}
            assert changed == {"xl/worksheets/sheet3.xml"}


@pytest.mark.parametrize("name", NAMES)
def test_per_check_reports_reject_actual_errors_and_preserve_valid_judgments(name):
    reports = ROOT / name / "private/reports"
    expected_failures = {
        "xlsx_only": {"summary"},
        "protected_field": {"unit_review.xlsx:xl/worksheets/sheet2.xml"},
        "wrong_formula": {"formula_B5"},
        "wrong_value": {"current_comparator_label"},
        "wrong_direction": {"semantic_claim", "direction_vs_workbook"},
        "forged_cache": {"formula_B5", "recomputed_yoy"},
        "original_static_answer": {"injected_source", "recomputed_yoy"},
        "broken_structure": {"package_members", "management_discussion.docx:word/footer1.xml"},
    }
    for case, keys in expected_failures.items():
        report = json.loads((reports / f"{case}.json").read_text())
        assert not report["passed"]
        assert keys <= {r["id"] for r in report["results"] if not r["passed"]}
    for case in ["correct", "stale_semantics", "numbers_only_old_judgment"]:
        report = json.loads((reports / f"{case}.json").read_text())
        assert report["passed"] == report["expected_pass"]
    truth = json.loads((ROOT / name / "private/truth.json").read_text())
    numbers_only = json.loads((reports / "numbers_only_old_judgment.json").read_text())
    assert numbers_only["passed"] == (truth["counterfactual"]["direction"] == "增长")
    if not numbers_only["passed"]:
        # Every number is correct; only the interpretation was left stale.
        assert {r["id"] for r in numbers_only["results"] if not r["passed"]} == {
            "semantic_claim", "direction_vs_workbook"}


@pytest.mark.parametrize("name", NAMES)
def test_check_sets_generated_from_actual_output_changes(name):
    task = ROOT / name
    oracle = json.loads((task / "private/oracle.json").read_text())
    initial = Document(task / "public/input" / FILES[1])
    reference = Document(task / "private/reference" / FILES[1])
    expected = {i for i in [3, 18, 19] if initial.paragraphs[i].text != reference.paragraphs[i].text}
    assert set(oracle["protected_state"]["editable_paragraphs"]) == expected
    assert {c["paragraph"] for c in oracle["required_updates"] if c["kind"] == "paragraph"} == expected
    assert {c["paragraph"] for c in oracle["protected_outputs"]} == {3, 18, 19} - expected


def test_new_external_parameter_without_editing_evaluator(tmp_path):
    config = tmp_path / "fresh.json"
    config.write_text(json.dumps({"schema": "cfbench-obm036-parameters-v1",
                                 "interventions": [{"id": "external_2731", "prior_millions": "2731"}]}))
    frozen = implementation_snapshot()
    builder.build(tmp_path / "fresh_outputs", config)
    task = tmp_path / "fresh_outputs/external_2731"
    assert audit(task, COHORT / "private/reference" / TASK / FILES[0])["passed"]
    assert evaluate(task / "private/oracle.json", task / "private/reference")["passed"]
    assert implementation_snapshot() == frozen


def test_independent_audit_catches_shared_builder_oracle_bug(tmp_path, monkeypatch):
    config = tmp_path / "shared_error.json"
    config.write_text(json.dumps({"schema": "cfbench-obm036-parameters-v1",
                                 "interventions": [{"id": "shared_error", "prior_millions": "2700"}]}))
    real = builder.derive

    def erroneous_rule(usd, prior):
        state = real(usd, prior)
        state["yoy"] += 0.01  # generator and its Oracle both inherit this error
        return state

    monkeypatch.setattr(builder, "derive", erroneous_rule)
    builder.build(tmp_path / "shared_outputs", config)
    result = audit(tmp_path / "shared_outputs/shared_error", COHORT / "private/reference" / TASK / FILES[0])
    assert not result["passed"]
    assert {"truth_yoy", "reference_B5", "oracle_yoy"} <= {r["id"] for r in result["results"] if not r["passed"]}


@pytest.mark.parametrize("value", ["0", "2300", "NaN", "2600.01", "-1"])
def test_reject_invalid_or_noop_parameters(tmp_path, value):
    config = tmp_path / "bad.json"
    config.write_text(json.dumps({"schema": "cfbench-obm036-parameters-v1",
                                 "interventions": [{"id": "bad", "prior_millions": value}]}))
    with pytest.raises(ValueError):
        builder.build(tmp_path / "bad_outputs", config)
    assert not (tmp_path / "bad_outputs").exists()


def test_invalid_submission_fails_closed(tmp_path):
    oracle = ROOT / NAMES[0] / "private/oracle.json"
    assert not evaluate(oracle, tmp_path)["passed"]
    for filename in FILES:
        (tmp_path / filename).write_bytes(b"invalid OOXML")
    assert not evaluate(oracle, tmp_path)["passed"]
