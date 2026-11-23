"""One-command build, native evidence audit, grading controls and real reconstruction."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from zipfile import ZipFile

from .build import build, save, digest, surface
from .evaluate import leaves, evaluate, grade_files
from .upstreams import ROOT
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation


def audit(package):
    package = Path(package)
    public = package / "public"
    truth = json.loads((package / "private/truth.json").read_text())
    wanted = dict(leaves(truth))
    bindings = json.loads((package / "private/bindings.json").read_text())
    wb = load_workbook(public / "day_schedule.xlsx", data_only=False)
    doc = Document(public / "visitor_notes.docx")
    deck = Presentation(public / "travel_brief.pptx")
    checks = []
    for b in bindings:
        expected = wanted[b["path"]]
        if b["file"].endswith("xlsx"):
            observed = wb[b["sheet"]][b["cell"]].value
        elif b["file"].endswith("docx"):
            observed = doc.paragraphs[b["paragraph"]].text[b["start"]:b["end"]]
            if b.get("rule"):
                from .indirect import read_indirect
                if observed != b["evidence_sentence"]:
                    raise AssertionError("Indirect evidence clause changed")
                observed = read_indirect(b, public)
        elif "point" in b:
            observed = deck.slides[b["slide"]].shapes[b["shape"]].chart.series[b["series"]].values[b["point"]]
        else:
            observed = deck.slides[b["slide"]].shapes[b["shape"]].text[b["start"]:b["end"]]
        ok = (observed == expected if isinstance(observed, (int, float)) and not isinstance(expected, bool)
              else str(observed) == surface(expected))
        checks.append({"path": b["path"], "file": b["file"], "expected": expected,
                       "observed": observed, "pass": ok})
    leaks = []
    for p in public.iterdir():
        if p.suffix not in {".xlsx", ".docx", ".pptx"}:
            continue
        with ZipFile(p) as archive:
            for name in archive.namelist():
                if name.endswith(".xml"):
                    text = archive.read(name).decode("utf-8", errors="replace")
                    if re.search(r"\{\{fact:|f:trip\.\d+|</?r\d+_c\d+>|expected_patch|annotation_notes", text):
                        leaks.append(f"{p.name}:{name}")
    hidden = [ws.title for ws in wb if ws.sheet_state != "visible"]
    result = {"pass": all(x["pass"] for x in checks) and len(checks) == len(wanted) and not leaks and not hidden,
              "correct_native_values": sum(x["pass"] for x in checks), "total": len(wanted),
              "hidden_sheets": hidden, "construction_marker_leaks": leaks, "checks": checks,
              "note": "Value presence and compiler bindings audited; natural-language meaning still requires review."}
    save(package / "private/native_audit.json", result)
    return result


def controls(package):
    package = Path(package)
    truth = json.loads((package / "private/truth.json").read_text())
    bindings = json.loads((package / "private/bindings.json").read_text())
    absent = deepcopy(truth)
    del absent["contact_plan"]["ferry_contact"]
    wrong_duration = deepcopy(truth)
    wrong_duration["stops"][4]["duration_minutes"] += 1
    swapped = deepcopy(truth)
    swapped["stops"][4]["ride_notes"]["bring_item"] = truth["stops"][0]["walk_notes"]["starting_focus"]
    wrong_bool = deepcopy(truth)
    wrong_bool["comfort_preferences"]["avoid_steep_climbs"] = False
    examples = {"correct": truth, "omitted_word_evidence": absent,
                "wrong_chart_value": wrong_duration, "misbound_activity_note": swapped,
                "wrong_boolean": wrong_bool}
    out = {}
    for name, value in examples.items():
        report = evaluate(truth, value, bindings)
        save(package / "private/controls" / f"{name}.json", report)
        if report["pass"] != (name == "correct"):
            raise AssertionError("Grading control behaved incorrectly: " + name)
        out[name] = {"pass": report["pass"], "correct": report["correct"], "total": report["total"],
                     "failures": [c for c in report["checks"] if c["status"] != "PASS"]}
    save(package / "private/controls.json", out)
    return out


def run_model(package, *, timeout=300, revision=False, direct=False):
    """A real coding harness may read only the public assets, not host gold.

    Bubblewrap hides /mnt and the user's normal Codex home. A temporary home
    carries authentication only. This is an experiment boundary, not a claim
    of a complete hostile-agent security system.
    """
    from alignment_probe.codex_transport import configured_route
    route = configured_route()
    if route["provider"] != "openai" or not route["use_saved_chatgpt_login"]:
        raise RuntimeError("This native-reader runner requires the existing official Codex login")
    package = Path(package).resolve()
    if direct and revision:
        raise ValueError("Choose direct one-turn editing or the two-turn protocol")
    public = package / "public"
    assert_public_boundary(public)
    output = package / "model"
    if output.exists() and any(output.iterdir()):
        # Prior scoring files may contain hidden expected values. They must not
        # be visible through the writable model-output mount on a later run.
        archive = package / "private/model_runs" / str(time.time_ns())
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(archive))
    output.mkdir(exist_ok=True)
    before = {p.name: digest(p) for p in public.iterdir() if p.is_file()}
    prompt = (public / "instruction.md").read_text() + (
        "\nThe current directory /mnt/workspace contains only the public files. Inspect those native files yourself, "
        "including chart data when needed, using the installed Python libraries (openpyxl, python-docx, python-pptx). "
        "Do not search the web or invoke other agents. Do not modify input files. "
        "Use only evidence from this workspace. Return the complete JSON object, without code fences or commentary.")
    workspace, mount_mode = public, "--ro-bind"
    if direct:
        from .revision import FILES
        from .staged import revision_prompt
        workspace = output / "revision/working"
        workspace.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            shutil.copy2(public / name, workspace / name)
        instruction = (package / "phase2/instruction.md").read_text()
        (workspace / "revision_instruction.md").write_text(instruction)
        prompt = revision_prompt(instruction, prior_state=False)
        mount_mode = "--bind"
    (output / "prompt.txt").write_text(prompt)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="reconstruction-auth-") as temporary:
        temp_home = Path(temporary)
        auth_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        shutil.copy2(auth_home / "auth.json", temp_home / "auth.json")
        sandbox = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
                   "--tmpfs", "/mnt", "--tmpfs", "/tmp",
                   "--bind", str(temp_home), str(auth_home),
                   "--ro-bind", str(Path(shutil.which("codex")).resolve().parent), "/mnt/codex-bin",
                   mount_mode, str(workspace), "/mnt/workspace", "--bind", str(output), "/mnt/model-output",
                   "--proc", "/proc", "--dev", "/dev", "--chdir", "/mnt/workspace"]
        # Check the filesystem boundary before spending inference tokens.
        check = subprocess.run(sandbox + ["/bin/sh", "-c",
            "test -f /mnt/workspace/day_schedule.xlsx && ! test -e /mnt/docker_disk/tmp/data/ICM2027"],
            capture_output=True, text=True)
        if check.returncode:
            raise RuntimeError("Public-only filesystem check failed: " + check.stderr)
        command = ["/mnt/codex-bin/codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                   "--skip-git-repo-check", "--sandbox", "danger-full-access", "--json", "--color", "never",
                   "--model", route["model"], "-c", 'model_reasoning_effort="' + (route["effort"] or "high") + '"',
                   "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                   "-c", "features.multi_agent=false", "-c", "features.apps=false",
                   "--output-last-message", "/mnt/model-output/" + ("completion.txt" if direct else "reconstruction.txt"), "-"]
        if revision:
            command.remove("--ephemeral")  # retain this isolated session for turn 2
        env = os.environ.copy()
        for key in ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            env.pop(key, None)
        env["CODEX_HOME"] = str(auth_home)
        print("DIRECT_EDIT_STARTED" if direct else "NATIVE_READER_STARTED", route["model"], flush=True)
        with (output / "events.jsonl").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
            try:
                result = subprocess.run(sandbox + command, input=prompt, text=True, stdout=stdout,
                                        stderr=stderr, env=env, timeout=timeout)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        reader_seconds = time.monotonic() - started
        if code == 0 and revision:
            from .staged import resume_revision
            resume_revision(package, sandbox, command, env, timeout=timeout)
        after = {p.name: digest(p) for p in public.iterdir() if p.is_file()}
        save(output / "run.json", {"route": route, "wall_seconds": reader_seconds,
            "returncode": code, "private_project_tree_visible": False,
            "schema_provided": False,
            "protocol": "one_round_direct" if direct else "two_round_with_self_json" if revision else "reconstruction_only",
            "public_files_before": before, "public_files_after": after,
            "inputs_unchanged": before == after,
            "input_mode": "Original XLSX/DOCX/PPTX bytes read through the coding harness; chart XML/data may be read. No claim of visual-only perception.",
            "isolation": "Bubblewrap hides /mnt and mounts only public assets plus this model output directory; separate temporary Codex home."})
    if code != 0:
        raise RuntimeError(f"Native reader failed ({code}); inspect {output / 'stderr.log'}")
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines() if line.strip()]
    infrastructure_errors = [e for e in events if e.get("type") in {"error", "turn.failed"}
                             or (e.get("item") or {}).get("type") == "error"]
    if infrastructure_errors:
        save(output / "infrastructure_errors.json", infrastructure_errors)
        raise RuntimeError("Native reader reported infrastructure errors; this is not a measured model failure")
    if direct:
        from .staged import evaluate_revision
        return evaluate_revision(package, same_conversation=False)
    raw = (output / "reconstruction.txt").read_text().strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw[raw.index("\n") + 1:-3].strip()
    submission = json.loads(raw)
    save(output / "reconstruction.json", submission)
    return grade_files(package, output / "reconstruction.json", output / "evaluation.json")


def assert_public_boundary(public):
    allowed = {"day_schedule.xlsx", "visitor_notes.docx", "travel_brief.pptx", "instruction.md"}
    if {p.name for p in Path(public).iterdir()} != allowed:
        raise ValueError("Reader workspace must contain only the three Office inputs and business instruction; schema, truth and extra files are forbidden")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--skip-model", action="store_true", help="Build and audit only; not a measured reconstruction")
    parser.add_argument("--literal-author", action="store_true", help="Offline authoring control, not LLM prose")
    parser.add_argument("--indirect", action="store_true", help="Use a verified temporal evidence fragment through Doc2DB Refiner")
    parser.add_argument("--revision", action="store_true", help="Build change oracle/controls and resume the reader session to edit the existing files")
    args = parser.parse_args()
    if args.out is None:
        args.out = ROOT / "multifile_reconstruction/artifacts" / ("ferry_revision_no_schema" if args.revision or args.indirect else "ferry_no_schema")
    # Reuse the accepted writer output, not an additional unrelated prose draft.
    cached = ROOT / "multifile_reconstruction/artifacts/ferry/private/authoring/accepted_tagged.txt"
    target_cache = args.out / "private/authoring/accepted_tagged.txt"
    if not args.literal_author and not target_cache.exists() and cached.exists():
        target_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, target_cache)
        save(target_cache.parent / "cache_origin.json", {"path": str(cached), "sha256": digest(cached)})
    summary = build(args.out, author="literal" if args.literal_author else "model")
    if args.indirect:
        from .indirect import add_indirect_evidence
        summary["indirect_evidence"] = add_indirect_evidence(args.out, model=not args.literal_author)
    print("BUILT", json.dumps(summary, ensure_ascii=False), flush=True)
    native = audit(args.out)
    if not native["pass"]:
        raise AssertionError("Rendered evidence audit failed")
    grading_controls = controls(args.out)
    print("NATIVE_AUDIT", native["correct_native_values"], "/", native["total"], flush=True)
    revision_controls = None
    if args.revision:
        from .revision import build_revision, controls as revision_control_suite
        oracle = build_revision(args.out)
        revision_controls = revision_control_suite(args.out, oracle)
        summary["scope"] = "Registered travel scenario: evidence reconstruction and minimal native-file maintenance; reviewed upstream gold with independent time assertions."
        for name, result in revision_controls.items():
            print("REVISION_CONTROL", name, result["pass"], result["groups"], flush=True)
    save(args.out / "build_summary.json", summary)
    report = None if args.skip_model else run_model(args.out, revision=args.revision)
    result = {"build": summary, "native_audit_pass": native["pass"],
              "grading_controls_pass": True, "model_run": report is not None,
              "model_result": None if report is None else {k: v for k, v in report.items() if k != "checks"},
              "package": str(args.out.resolve())}
    if args.revision:
        result["revision_controls"] = {k: {"pass": v["pass"], "groups": v["groups"]} for k, v in revision_controls.items()}
        revision_report = args.out / "model/revision/evaluation.json"
        result["revision_model_result"] = json.loads(revision_report.read_text()) if not args.skip_model and revision_report.exists() else None
        if not args.skip_model:
            from .export import export_comparison
            result["comparison"] = export_comparison(args.out)
    save(args.out / "summary.json", result)
    # Full per-object evidence is in the report, not a multi-page terminal dump.
    compact = {k: v for k, v in result.items() if k != "revision_model_result"}
    if result.get("revision_model_result") is not None:
        compact["revision_model_result"] = {k: v for k, v in result["revision_model_result"].items() if k != "failures"}
    print("SUMMARY", json.dumps(compact, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
