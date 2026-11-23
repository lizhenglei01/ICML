"""Continue the exact reader conversation on writable copies of its original files."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time

from .build import save, digest
from .evaluate import evaluate, leaves, free_form_report, protocol_has_schema
from .revision import FILES, grade


def parse_events(path):
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    errors = [e for e in events if e.get("type") in {"error", "turn.failed"} or (e.get("item") or {}).get("type") == "error"]
    if errors:
        raise RuntimeError("Model harness error (not a task score): " + str(errors[:1]))
    return events


def revision_prompt(instruction, *, prior_state):
    """Same business task/tools/deliverables; only the prior-state treatment differs."""
    context = (
        "This is the next turn of the same task and conversation. The files are writable copies of the exact files you read. "
        "current_state.json contains your own previous answer, without grading feedback. Use it as your working notes and recheck the source files if needed. "
        "The previous read-only instruction applied to the previous turn; now perform this revision. "
        if prior_state else
        "This is a fresh task. The workspace contains the original three Office files and the revision instruction. "
    )
    return instruction + "\n" + context + (
        "Edit the existing files in /mnt/workspace and save updated_state.json there using your own JSON organization. "
        "Use native Office libraries as needed. Do not rewrite unrelated prose or rebuild the entire presentation. "
        "No internet, other agents or external files. Do not treat a final message as a substitute for saving the modified files.")


def resume_revision(package, sandbox, command, env, *, timeout):
    package = Path(package)
    output = package / "model/revision"
    working = output / "working"
    working.mkdir(parents=True, exist_ok=True)
    events = parse_events(package / "model/events.jsonl")
    session = next(e["thread_id"] for e in events if e.get("type") == "thread.started")
    raw = (package / "model/reconstruction.txt").read_text().strip()
    if raw.startswith("```"):
        raw = raw[raw.index("\n")+1:-3].strip()
    previous = json.loads(raw)
    save(working / "current_state.json", previous)  # model's own answer, never gold
    for file in FILES:
        shutil.copy2(package / "public" / file, working / file)
    instruction = (package / "phase2/instruction.md").read_text()
    (working / "revision_instruction.md").write_text(instruction)
    prompt = revision_prompt(instruction, prior_state=True)
    (output / "prompt.txt").write_text(prompt)
    mounts = list(sandbox)
    index = mounts.index(str(package / "public"))
    if mounts[index-1] != "--ro-bind":
        raise AssertionError("Unexpected public mount specification")
    mounts[index-1:index+1] = ["--bind", str(working)]
    root_command = command[:command.index("--output-last-message")]
    resumed = root_command + ["resume", session, "--output-last-message", "/mnt/model-output/revision/completion.txt", "-"]
    before = {file: digest(working / file) for file in FILES}
    started = time.monotonic()
    print("NATIVE_REVISION_STARTED", session, flush=True)
    with (output / "events.jsonl").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
        try:
            result = subprocess.run(mounts + resumed, input=prompt, text=True, stdout=stdout,
                                    stderr=stderr, env=env, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    save(output / "run.json", {"thread_id": session, "same_conversation": True,
        "wall_seconds": time.monotonic() - started, "returncode": code,
        "starting_assets": before, "final_assets": {f: digest(working / f) for f in FILES},
        "starting_state_source": "Uncorrected model turn-1 reconstruction", "gold_feedback_given": False,
        "private_project_tree_visible": False})
    if code != 0:
        raise RuntimeError("Native revision runner failed; inspect " + str(output / "stderr.log"))
    parse_events(output / "events.jsonl")
    return evaluate_revision(package)


def evaluate_revision(package, *, same_conversation=True):
    """Regrade saved, unmodified model artifacts; never re-solve or repair them."""
    package = Path(package)
    output = package / "model/revision"
    working = output / "working"
    oracle = json.loads((package / "private/revision/oracle.json").read_text())
    native = grade(working, oracle)
    path = working / "updated_state.json"
    if path.exists():
        try:
            candidate = json.loads(path.read_text())
            # New indices differ after deletion: remap the old source locator by identity.
            changes = json.loads((package / "private/revision/fact_changes.json").read_text())
            bindings = [{**c["binding"], "path": c["new_path"]} for c in changes if not c["deleted"]]
            state = evaluate(oracle["truth"], candidate, bindings) if protocol_has_schema(package) else free_form_report(candidate)
        except (ValueError, KeyError) as exc:
            state = {"pass": False, "error": str(exc)}
    else:
        state = {"pass": False, "error": "updated_state.json not saved"}
    save(output / "native_evaluation.json", native)
    save(output / "state_evaluation.json", state)
    schema_given = protocol_has_schema(package)
    report = {"pass": native["pass"] and (state["pass"] if schema_given else state.get("valid_json_object", False)), "native_pass": native["pass"],
              "state_pass": state["pass"], "groups": native["groups"],
              "schema_provided": schema_given, "state_status": state.get("status", "SCORED"),
              "pass_scope": "Native Office edits and JSON syntax only; JSON semantic correctness remains unscored" if not schema_given else "Native Office edits and schema-based state comparison",
              "state_correct": state.get("correct"), "state_total": state.get("total"),
              "failures": [c for c in native["checks"] if not c["pass"]],
              "same_conversation": same_conversation, "gold_feedback_given": False}
    save(output / "evaluation.json", report)
    print("NATIVE_REVISION_RESULT", report["pass"], native["groups"], flush=True)
    return report
