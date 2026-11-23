"""Parameter-only OBM-036 experiment; new tasks never branch on task ids."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook

from .build import DEFAULT_OUT, build, write_json
from .evaluate import evaluate
from .independent import audit
from .package import set_yoy_cache
from .parameters import DEFAULT_PARAMS, load_parameters
from .task import read_evidence
from .upstream import COHORT, FILES, PINS, TASK, git, patcher, repository_path, sha256, verifier


def implementation_snapshot():
    directory = Path(__file__).parent
    return {p.name: sha256(p.read_bytes()) for p in sorted(directory.iterdir())
            if p.suffix in {".py", ".json"}}


def repository_snapshot():
    return {name: {"checkout": str(repository_path(name)),
                   "commit": git(repository_path(name), "rev-parse", "HEAD").decode().strip(),
                   "status": git(repository_path(name), "status", "--porcelain=v1").decode()}
            for name in PINS}


def run(root=DEFAULT_OUT, params=DEFAULT_PARAMS):
    started = time.time()
    code_before, repos_before = implementation_snapshot(), repository_snapshot()
    interventions = load_parameters(params)
    root = build(root, params)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "requested_parameters.json").write_bytes(Path(params).read_bytes())
    lines = []

    def log(line):
        print(line, flush=True)
        lines.append(line)
        (logs / "smoke.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    upstream_ref, original_book, baseline_paras = read_evidence()
    upstream = subprocess.run([sys.executable, str(COHORT / "verify_task.py"), TASK,
                               str(upstream_ref), "--json"], capture_output=True, text=True,
                              check=True, timeout=60)
    (logs / "upstream_original_reference.json").write_text(upstream.stdout)
    assert json.loads(upstream.stdout)["passed"]
    log("Upstream OBM-036 original reference: PASS (original verifier)")
    summary, graph_digests = {}, set()
    for intervention in interventions:
        scenario = intervention["id"]
        task = root / scenario
        reference, oracle_path = task / "private/reference", task / "private/oracle.json"
        independent = audit(task, upstream_ref / FILES[0])
        write_json(task / "private/reports/independent_ground_truth.json", independent)
        assert independent["passed"], independent
        log(f"{scenario}/independent_ground_truth: PASS ({len(independent['results'])} assertions)")
        graph_digests.add(sha256((task / "private/dependencies.json").read_bytes()))
        oracle = json.loads(oracle_path.read_text())
        truth = json.loads((task / "private/truth.json").read_text())
        state = truth["counterfactual"]
        magnitude = next(c["percent"] for c in oracle["required_updates"] + oracle["protected_outputs"]
                         if c["id"] == "semantic_claim")
        cases = {"correct": (reference, True)}
        negatives = ["xlsx_only", "protected_field", "wrong_formula", "wrong_value",
                     "stale_semantics", "wrong_direction", "numbers_only_old_judgment",
                     "forged_cache", "original_static_answer", "broken_structure"]
        for name in negatives:
            directory = task / "private/negative_submissions" / name
            directory.mkdir(parents=True, exist_ok=True)
            for filename in FILES:
                shutil.copy2(reference / filename, directory / filename)
            cases[name] = (directory, False)
        paths = {name: pair[0] for name, pair in cases.items()}
        shutil.copy2(task / "public/input" / FILES[1], paths["xlsx_only"] / FILES[1])
        patcher().patch_xlsx(paths["protected_field"] / FILES[0], [
            {"type": "xlsx_cell", "sheet": "Facts", "cell": "I3", "value": -123}])
        patcher().patch_xlsx(paths["wrong_formula"] / FILES[0], [
            {"type": "xlsx_formula", "sheet": "Review", "cell": "B5", "formula": f"={state['yoy']}"}])
        patcher().patch_xlsx(paths["wrong_value"] / FILES[0], [
            {"type": "xlsx_cell", "sheet": "Approval", "cell": "B4", "value": original_book["Approval"]["B4"].value}])
        current_text = Document(reference / FILES[1]).paragraphs[19].text
        patcher().patch_docx(paths["stale_semantics"] / FILES[1], [
            {"type": "docx_replace", "old": current_text, "new": baseline_paras[19]}])
        # A stale sentence is actually valid if rounding and judgment are unchanged.
        cases["stale_semantics"] = (paths["stale_semantics"], current_text == baseline_paras[19])
        opposite = "下降" if state["direction"] == "增长" else "增长"
        patcher().patch_docx(paths["wrong_direction"] / FILES[1], [
            {"type": "docx_replace", "old": current_text, "new": current_text.replace(state["direction"], opposite)}])
        numbers_only = baseline_paras[19].replace("7.2%", magnitude)
        patcher().patch_docx(paths["numbers_only_old_judgment"] / FILES[1], [
            {"type": "docx_replace", "old": current_text, "new": numbers_only}])
        cases["numbers_only_old_judgment"] = (paths["numbers_only_old_judgment"], state["direction"] == "增长")
        patcher().patch_xlsx(paths["forged_cache"] / FILES[0], [
            {"type": "xlsx_formula", "sheet": "Review", "cell": "B5", "formula": "=1"}])
        set_yoy_cache(paths["forged_cache"] / FILES[0], state["yoy"])
        for filename in FILES:
            shutil.copy2(upstream_ref / filename, paths["original_static_answer"] / filename)
        damaged = paths["broken_structure"] / FILES[1]
        temporary = damaged.with_suffix(".broken")
        with ZipFile(damaged) as source, ZipFile(temporary, "w") as target:
            assert "word/footer1.xml" in source.namelist()
            for info in source.infolist():
                if info.filename != "word/footer1.xml":
                    target.writestr(info, source.read(info.filename))
        temporary.replace(damaged)
        reports = {}
        for name, (directory, expected) in cases.items():
            report = evaluate(oracle_path, directory)
            report["expected_pass"] = expected
            reports[name] = report
            write_json(task / "private/reports" / f"{name}.json", report)
            failed = [r["id"] for r in report["results"] if not r["passed"]]
            log(f"{scenario}/{name}: {'PASS' if report['passed'] else 'FAIL'} "
                f"(expected {'PASS' if expected else 'FAIL'}), failures={failed}")
            assert report["passed"] == expected, (scenario, name, report)
        changes = verifier().workbook_diff(load_workbook(task / "public/input" / FILES[0]),
                                          load_workbook(reference / FILES[0]))
        assert changes == {("Approval", "B4")}, changes
        summary[scenario] = {
            "parameters": intervention, "correct": reports["correct"]["categories"],
            "cases": {k: {"passed": v["passed"], "expected_pass": v["expected_pass"],
                           "failed_checks": [r["id"] for r in v["results"] if not r["passed"]]}
                      for k, v in reports.items()},
            "negative_cases_rejected": sum(not v["passed"] for v in reports.values()),
            "independent_assertions": len(independent["results"]),
            "all_expected_results": True, "workbook_raw_value_changes": sorted(changes)}
    assert len(graph_digests) == 1, "Dependency registration must be identical across interventions"
    first = interventions[0]["id"]
    old_on_new = subprocess.run([sys.executable, str(COHORT / "verify_task.py"), TASK,
                                str(root / first / "private/reference"), "--json"],
                               capture_output=True, text=True, timeout=60)
    old_report = json.loads(old_on_new.stdout)
    assert not old_report["passed"]
    write_json(logs / "upstream_verifier_on_new_counterfactual.json", old_report)
    code_after, repos_after = implementation_snapshot(), repository_snapshot()
    assert code_before == code_after
    assert repos_before == repos_after and all(not r["status"] for r in repos_after.values())
    proof = {"parameter_file": str(Path(params).resolve()), "parameter_sha256": sha256(Path(params).read_bytes()),
             "implementation_before": code_before, "implementation_after": code_after,
             "implementation_unchanged": True, "upstream_before": repos_before,
             "upstream_after": repos_after, "all_upstreams_unchanged_and_clean": True,
             "dependency_graph_sha256": next(iter(graph_digests)), "all_tasks_share_graph": True,
             "task_count": len(interventions), "task_ids": [x["id"] for x in interventions],
             "scope": "Parameter-only generation within the manually registered OBM-036 adapter"}
    write_json(logs / "parameterization_proof.json", proof)
    runtime = {"python": platform.python_version(),
               "packages": {x: version(x) for x in ["openpyxl", "python-docx", "python-pptx", "lxml", "pytest"]},
               "libreoffice": subprocess.check_output(["soffice", "--version"], text=True).strip(),
               "elapsed_seconds": round(time.time() - started, 2)}
    write_json(logs / "environment.json", runtime)
    write_json(logs / "summary.json", summary)
    log(f"All assertions passed: {len(interventions)} parameterized tasks; code and upstreams unchanged; {runtime['elapsed_seconds']} seconds")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    args = p.parse_args()
    run(args.out, args.params)
