import json
from pathlib import Path

from .ablation import prepare
from .build import build, digest
from .indirect import add_indirect_evidence
from .revision import build_revision, FILES
from .staged import revision_prompt


def test_comparison_arms_share_inputs_and_gold_but_not_mutable_files(tmp_path):
    source = tmp_path / "source"
    build(source, author="literal")
    add_indirect_evidence(source, model=False)
    build_revision(source)
    a, b = tmp_path / "two_round", tmp_path / "one_round"
    prepare(source, a)
    prepare(source, b)
    for name in FILES:
        assert digest(a / "public" / name) == digest(b / "public" / name)
        assert not (a / "public" / name).samefile(b / "public" / name)
    assert digest(a / "private/revision/oracle.json") == digest(b / "private/revision/oracle.json")
    assert (a / "phase2/instruction.md").read_bytes() == (b / "phase2/instruction.md").read_bytes()
    for arm in (a, b):
        assert not list((arm / "public").glob("*.json"))
        assert not (arm / "model").exists()
        assert json.loads((arm / "private/input_protocol.json").read_text())["schema_provided"] is False
    (a / "private/revision/oracle.json").write_text("changed")
    assert digest(source / "private/revision/oracle.json") == digest(b / "private/revision/oracle.json")


def test_same_revision_requirements_with_only_prior_state_treatment_changed():
    task = "Remove the ferry ride. Preserve unrelated arrangements."
    two = revision_prompt(task, prior_state=True)
    one = revision_prompt(task, prior_state=False)
    assert two.startswith(task) and one.startswith(task)
    assert "current_state.json" in two and "current_state.json" not in one
    assert "updated_state.json" in two and "updated_state.json" in one
    assert two[two.index("Edit the existing files"):] == one[one.index("Edit the existing files"):]
