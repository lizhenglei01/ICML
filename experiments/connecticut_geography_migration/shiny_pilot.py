"""Freeze and run a single blind maintenance trial on the publisher's Shiny snapshot."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_agent import (
    event_summary, isolated_env, model_command, sandbox_arguments,
)
from research_archive import digest, read, write

from .shiny_grade import grade


TASK = """# Maintain the County Health Rankings snapshot

Update this application so users can query both kinds of Connecticut geography in
the 2025 release. Some published measures, such as population, are available for a
planning region. Other measures, such as unemployment, are still published for the
former counties. Both the legacy county and planning region must be usable through
the normal state, area, and release-year controls. Maintain historical queries,
including the 2024 former-county data.

The displayed county/region numbers and the existing location-specific CSV download
should agree on the selected geography and release. When a measure has no value
for that selection, leave it unavailable. Preserve the existing behavior for other
states and keep the bundled published measurement data intact.

Make the necessary changes in this workspace. The workspace contains the upstream
application and published data. The R dependencies are installed, and Rscript is
available on PATH for local checks.
"""
MODEL = "gpt-6.2-sol"
EFFORT = "medium"
GRADER = Path(__file__).with_name("shiny_grade.py").resolve()


def checksums(folder):
    return {
        path.relative_to(folder).as_posix(): digest(path)
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(folder).parts
    }


def grade_workspace(workspace, runtime, oracle, logs):
    logs.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="chr-grade-") as temp:
        temp = Path(temp)
        command = sandbox_arguments(workspace, logs, temp, Path(shutil.which("codex")).resolve())
        command += [
            "--unshare-net", "--ro-bind", str(workspace), "/mnt/workspace",
            "--ro-bind", str(runtime), str(runtime),
            "--ro-bind", "/opt/google/chrome", "/opt/google/chrome",
            "--ro-bind", "/etc/fonts", "/etc/fonts",
            "--ro-bind", "/etc/alternatives", "/etc/alternatives",
            "--ro-bind", str(GRADER), "/mnt/shiny_grade.py",
        ]
        cases = [{key: value for key, value in case.items() if key != "values"} for case in oracle]
        process = subprocess.run(
            command + [sys.executable, "/mnt/shiny_grade.py", "/mnt/workspace", str(runtime / "bin/Rscript")],
            input=json.dumps(cases), env=isolated_env(), text=True, capture_output=True, timeout=240, check=False,
        )
    (logs / "stderr.log").write_text(process.stderr, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"Could not observe Shiny at {workspace}: {process.stderr[-2000:]}")
    observed = json.loads(process.stdout)
    result = grade(observed, oracle)
    write(logs / "grade.json", result)
    print(workspace.name, f'{result["passed"]}/{result["total"]}', flush=True)
    return result


def freeze(root, runtime):
    oracle_path = root / "oracle.json"
    oracle = read(oracle_path)
    source, reference = root / "source", root / "reference"
    for folder, label in ((source, "baseline"), (reference, "reference_check")):
        grade_workspace(folder, runtime, oracle, root / label)
    baseline = read(root / "baseline/grade.json")
    reference_grade = read(root / "reference_check/grade.json")
    if reference_grade["passed"] != reference_grade["total"]:
        raise RuntimeError("The reference must pass the frozen UI checks")
    if not 0 < baseline["passed"] < baseline["total"]:
        raise RuntimeError("The baseline should preserve the known working controls and miss the legacy county")
    if any("query_error" in row for row in baseline["observations"].values()):
        raise RuntimeError("The baseline includes a browser or application error unrelated to the missing geography")
    frozen = {
        "model": MODEL, "effort": EFFORT, "oracle_sha256": digest(oracle_path),
        "grader_sha256": digest(GRADER), "task": TASK,
        "source": checksums(source), "reference": checksums(reference),
    }
    write(root / "frozen.json", frozen)


def run_model(root, runtime):
    frozen = read(root / "frozen.json")
    oracle_path = root / "oracle.json"
    if (
        digest(oracle_path) != frozen["oracle_sha256"] or digest(GRADER) != frozen["grader_sha256"]
        or checksums(root / "source") != frozen["source"] or TASK != frozen["task"]
    ):
        raise ValueError("Frozen task, sources or scorer changed")
    workspace, completion = root / "workspace", root / "completion"
    shutil.copytree(root / "source", workspace)
    completion.mkdir()
    (workspace / "instruction.md").write_text(TASK, encoding="utf-8")
    initial = checksums(workspace)
    events = root / "events.jsonl"
    started = datetime.now(timezone.utc).isoformat()
    start = monotonic()
    with TemporaryDirectory(prefix="chr-agent-auth-") as temp:
        auth = Path(temp)
        auth_home = Path(isolated_env().get("CODEX_HOME", str(Path.home() / ".codex")))
        shutil.copyfile(auth_home / "auth.json", auth / "auth.json")
        command = sandbox_arguments(workspace, completion, auth, Path(shutil.which("codex")).resolve())
        command += [
            "--ro-bind", str(runtime), str(runtime),
            "--ro-bind", "/etc/alternatives", "/etc/alternatives",
            "--ro-bind", str(workspace / "parquet"), "/mnt/workspace/parquet",
        ]
        env = isolated_env()
        env["PATH"] = str(runtime / "bin") + ":" + env["PATH"]
        probe = 'test -r app.R && Rscript --vanilla -e "stopifnot(requireNamespace(\'shiny\', quietly=TRUE))"'
        probe += ' && test ! -r "$1" && test ! -r "$2"'
        subprocess.run(command + [
            "/mnt/codex-bin/codex", "sandbox", "-c", 'sandbox_mode="workspace-write"',
            "-c", "sandbox_workspace_write.network_access=false",
            "--", "bash", "-c", probe, "probe", str(oracle_path), str(GRADER),
        ], env=env, check=True, timeout=45)
        print("START", MODEL, started, flush=True)
        with events.open("w", encoding="utf-8") as out, (root / "agent_stderr.log").open("w", encoding="utf-8") as err:
            process = subprocess.run(
                command + model_command(MODEL, EFFORT), env=env,
                input="Read instruction.md and make the required changes in this workspace.\n",
                text=True, stdout=out, stderr=err, timeout=900, check=False,
            )
    trace = event_summary(events)
    final = checksums(workspace)
    changes = sorted(name for name in initial.keys() | final.keys() if initial.get(name) != final.get(name))
    result = {
        "started_utc": started, "wall_seconds": round(monotonic() - start, 3),
        "model": MODEL, "effort": EFFORT, "exit_code": process.returncode,
        "tool_calls": len(trace["tool_events"]), "usage": trace["usage"],
        "thread_ids": trace["thread_ids"], "changed": changes, "submitted_sha256": final,
    }
    write(root / "results.json", result)
    if process.returncode == 0 and trace["usage"]:
        oracle = read(oracle_path)
        observed = grade_workspace(workspace, runtime, oracle, root / "submitted")
        result["score"] = {key: observed[key] for key in ("passed", "total")}
        result["released_data_changed"] = [name for name in changes if name.startswith("parquet/")]
        write(root / "results.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "run"))
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    root = args.run_directory.resolve()
    runtime = root / "runtime/r"
    if args.mode == "freeze":
        freeze(root, runtime)
    else:
        run_model(root, runtime)


if __name__ == "__main__":
    main()
