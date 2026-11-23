from copy import deepcopy
import json

import pytest

from .build import SOURCE, build
from .evaluate import evaluate, leaves, grade_files
from .run import audit, controls, assert_public_boundary


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    path = tmp_path_factory.mktemp("reconstruction")
    build(path, author="literal")
    return path


def test_native_evidence_covers_the_original_state(package):
    report = audit(package)
    assert report["pass"]
    assert report["correct_native_values"] == report["total"] == 100


def test_public_assets_have_no_answer_key_or_hidden_worksheets(package):
    assert {p.name for p in (package / "public").iterdir()} == {
        "day_schedule.xlsx", "visitor_notes.docx", "travel_brief.pptx",
        "instruction.md"}
    report = audit(package)
    assert report["hidden_sheets"] == report["construction_marker_leaks"] == []


def test_grader_rejects_missing_misbound_chart_and_boolean_evidence(package):
    results = controls(package)
    assert results["correct"]["pass"]
    assert all(not v["pass"] for k, v in results.items() if k != "correct")


def test_string_paraphrases_are_not_silently_declared_semantically_wrong(package):
    truth = json.loads((package / "private/truth.json").read_text())
    bindings = json.loads((package / "private/bindings.json").read_text())
    candidate = deepcopy(truth)
    candidate["trip_mood"] = "laid back"
    report = evaluate(truth, candidate, bindings)
    assert not report["pass"]
    assert report["semantic_review_count"] == 1


def test_standard_structure_is_private_and_instruction_gives_no_field_inventory(package):
    schema = json.loads((package / "private/reconstruction.schema.json").read_text())
    assert schema["properties"]["stops"]["minItems"] == 7
    text = json.dumps(schema)
    assert "Casco Bay" not in text and "16:25" not in text
    assert len(dict(leaves(json.loads(SOURCE.read_text())["current_json"]))) == 100
    assert not (package / "public/reconstruction.schema.json").exists()
    instruction = (package / "public/instruction.md").read_text()
    for hint in ("prefixItems", "scheduled_stop_count", "ferry_contact", "seven", "7 activities"):
        assert hint not in instruction
    assert_public_boundary(package / "public")


def test_reader_rejects_a_schema_or_old_evaluation_in_its_workspace(tmp_path):
    for name in ("day_schedule.xlsx", "visitor_notes.docx", "travel_brief.pptx", "instruction.md"):
        (tmp_path / name).touch()
    assert_public_boundary(tmp_path)
    (tmp_path / "evaluation.json").write_text('{"expected":"hidden"}')
    with pytest.raises(ValueError, match="forbidden"):
        assert_public_boundary(tmp_path)


def test_model_chosen_keys_are_not_scored_as_exact_path_errors(package, tmp_path):
    submission = tmp_path / "state.json"
    submission.write_text('{"我自己整理的行程": []}')
    report = grade_files(package, submission, tmp_path / "evaluation.json")
    assert report["pass"] is None and report["status"] == "NOT_SCORED"
    assert report["correct"] is None and report["total"] is None
