"""Read-only diagnostic sidecars for frozen automatic-business Agent trials.

This does not alter the original Oracle, submissions, run status, or score. It
reruns the original grader and identifies only narrowly demonstrated differences
between business values and their native representations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_office import grade, same_numeric_paragraph


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equal_numeric_paragraph(expected, actual):
    """Accept only identical paragraph metadata/label and exactly equal decimals."""
    return same_numeric_paragraph(actual, expected)


def same_members_wrong_required_order(expected, actual):
    """Identify a violated Unicode roster order without waiving that requirement."""
    if not isinstance(expected, str) or not isinstance(actual, str) or expected == actual:
        return False
    gold, submitted = expected.split(", "), actual.split(", ")
    return (len(gold) >= 2 and len(set(gold)) == len(gold) and gold == sorted(gold)
            and all(name and name == name.strip() and "," not in name for name in gold + submitted)
            and Counter(gold) == Counter(submitted))


def wrong_order_paragraph(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    if {k: v for k, v in expected.items() if k != "text"} != {
            k: v for k, v in actual.items() if k != "text"}:
        return False
    if not isinstance(expected.get("text"), str) or not isinstance(actual.get("text"), str):
        return False
    label, sep, gold = expected["text"].partition(" — ")
    other, other_sep, submitted = actual["text"].partition(" — ")
    return (label == other == "入选名单" and bool(sep and other_sep)
            and same_members_wrong_required_order(gold, submitted))


def metadata_warning(event, model):
    """This exact CLI startup warning is retained, never treated as model failure."""
    item = event.get("item") or {}
    return (event.get("type") == "item.completed" and item.get("type") == "error"
            and item.get("message") == (
                f"Model metadata for `{model}` not found. Defaulting to fallback metadata; "
                "this can degrade performance and cause issues."))


def runtime_diagnosis(record):
    trace = record.get("trace") or {}
    events = trace.get("runtime_events") or []
    warnings = [event for event in events if metadata_warning(event, record.get("model"))]
    errors = [event for event in events if event not in warnings]
    # New wrappers may already have extracted the warning into a side field.
    extracted = record.get("warnings", record.get("metadata_warnings", []))
    if isinstance(extracted, list):
        warnings += [event for event in extracted if event not in warnings]
    successful_terminal = record.get("returncode") == 0 and bool(trace.get("usage")) and not errors
    return {"original_status": record.get("status"), "returncode": record.get("returncode"),
            "has_completed_usage": bool(trace.get("usage")), "metadata_warnings": warnings,
            "other_runtime_errors": errors,
            "warning_only_misclassified_as_run_error": bool(
                successful_terminal and warnings and record.get("status") == "RUN_ERROR"),
            "execution_completed_without_fatal_events": successful_terminal,
            "scope": "A nonfatal warning can still affect fallback model metadata; no claim of performance neutrality."}


def classify_failures(score, oracle):
    semantic = [item for item in score["checks"] if item["group"] == "semantic"]
    full_readback = len(semantic) == len(oracle["evidence_checks"])
    semantic_pass = full_readback and bool(semantic) and all(item["pass"] for item in semantic)
    evidence = {item["evidence_id"]: item for item in oracle["evidence_checks"]}
    roster_bindings = [item for item in oracle["bindings"] if item.get("label") == "入选名单"
                       and item.get("role") == "output" and item.get("file") == "policy.docx"
                       and item.get("location", {}).get("kind") == "paragraph"]
    roster_ids = {item["evidence_id"] for item in roster_bindings}
    roster_objects = {f"policy.docx:paragraph:{item['location']['paragraph']}" for item in roster_bindings}
    findings = []
    for check in score["checks"]:
        if check["pass"]:
            continue
        kind = "native_object_difference_needs_review"
        if check["group"] == "semantic":
            binding = evidence.get(check["object"])
            if (check["object"] in roster_ids
                    and same_members_wrong_required_order(check.get("expected"), check.get("observed"))):
                kind = "same_members_wrong_required_order"
            elif binding and binding["group"] == "required" and check.get("observed") == binding["before"]:
                kind = "required_business_value_left_at_previous_state"
            elif binding and binding["group"] == "protected":
                kind = "protected_business_value_changed"
            else:
                kind = "business_value_disagrees_with_frozen_gt"
        elif check["group"] == "structure":
            kind = "native_readback_or_structure_failure_needs_review"
        elif (check["object"] in roster_objects
              and wrong_order_paragraph(check.get("expected"), check.get("observed"))):
            kind = "same_members_wrong_required_order"
        elif (check["object"].startswith("policy.docx:paragraph:")
              and equal_numeric_paragraph(check.get("expected"), check.get("observed"))):
            kind = "equivalent_numeric_text_representation"
        elif check["group"] == "protected":
            kind = "protected_object_difference_needs_review"
        elif semantic_pass:
            kind = "native_representation_difference_with_matching_business_values"
        findings.append({**check, "diagnostic_classification": kind})
    return {"semantic_readback_complete": full_readback,
            "semantic_values_pass": semantic_pass if full_readback else None,
            "equivalent_decimal_text_only": bool(findings) and all(
                row["diagnostic_classification"] == "equivalent_numeric_text_representation" for row in findings),
            "same_members_wrong_required_order_only": bool(findings) and all(
                row["diagnostic_classification"] == "same_members_wrong_required_order" for row in findings),
            "required_roster_order_violated": any(
                row["diagnostic_classification"] == "same_members_wrong_required_order" for row in findings),
            "failure_class_counts": dict(Counter(row["diagnostic_classification"] for row in findings)),
            "findings": findings,
            "scope": "Semantic readback checks registered business values. Same-members wrong order violates the explicit public Unicode-order instruction; it is neither a grader error nor a wrong membership calculation. No structure, order or preservation requirement is waived."}


def cancelled_attempts(root):
    """Exclude documented later-started duplicate cancellations, never by score."""
    records = []
    excluded = set()
    for marker in sorted(root.rglob("cancelled_duplicate.json")):
        record = read(marker)
        cancelled = Path(record["cancelled"]).resolve()
        retained = Path(record["retained"]).resolve()
        relative = marker.parent.relative_to(root)
        if len(cancelled.parents) >= len(relative.parts):
            previous_root = cancelled.parents[len(relative.parts) - 1]
            if cancelled.relative_to(previous_root) == relative and retained.is_relative_to(previous_root):
                cancelled = marker.parent.resolve()
                retained = (root / retained.relative_to(previous_root)).resolve()
        if (record.get("status") != "CANCELLED_DUPLICATE_BEFORE_SCORING"
                or cancelled != marker.parent.resolve() or cancelled == retained
                or root not in cancelled.parents or root not in retained.parents):
            raise ValueError(f"Invalid duplicate cancellation marker: {marker}")
        early, late = read(retained / "started.json"), read(cancelled / "started.json")
        if (early.get("model") != late.get("model") or early.get("task") != late.get("task")
                or early["started_utc"] != record["retained_started_utc"]
                or late["started_utc"] != record["cancelled_started_utc"]
                or datetime.fromisoformat(early["started_utc"]) >= datetime.fromisoformat(late["started_utc"])):
            raise ValueError(f"Cancellation is not justified by earlier start time: {marker}")
        excluded.add(cancelled)
        records.append({"marker": str(marker.relative_to(root)), "marker_sha256": digest(marker),
                        "model": early["model"], "task": early["task"], "record": record,
                        "excluded_from_effective_trials": True,
                        "start_time_selection_verified": True,
                        "diagnostic_status": "CANCELLED_DUPLICATE_NOT_A_MODEL_FAILURE"})
    return records, excluded


def diagnose_trial(run_path, plan, root, original_grade=grade, *, rescore=False):
    record = read(run_path)
    name = record.get("task", run_path.parent.name)
    if name not in plan["packages"]:
        return None
    frozen = plan["packages"][name]
    archived = root.parent / "frozen_tasks" / name
    source = archived if archived.is_dir() else Path(frozen["package"])
    oracle_path = source / "private" / frozen["oracle_file"]
    if digest(oracle_path) != frozen["oracle_sha256"]:
        raise ValueError(f"Frozen Oracle changed for {name}")
    for file, expected_hash in frozen["public_sha256"].items():
        if digest(source / "public" / file) != expected_hash:
            raise ValueError(f"Frozen input changed for {name}/{file}")
    directory = run_path.parent
    raw_score_path = directory / "native_evaluation.json"
    result = {"model": record.get("model"), "task": name,
              "run_file": str(run_path.relative_to(root)), "run_sha256": digest(run_path),
              "recorded_original_native_pass": record.get("native_pass"),
              "recorded_original_score_groups": record.get("score_groups"),
              "runtime": runtime_diagnosis(record), "oracle_sha256": digest(oracle_path)}
    if raw_score_path.exists():
        result["recorded_native_evaluation_sha256"] = digest(raw_score_path)
    if record.get("status") in {"MODEL_MISMATCH", "SOURCE_CHANGED"}:
        result["diagnostic_status"] = "INVALID_SOURCE_OR_MODEL_IDENTITY"
        return result
    if not result["runtime"]["execution_completed_without_fatal_events"]:
        result["diagnostic_status"] = "NOT_GRADED_UNFINISHED_OR_RUNTIME_FAILURE"
        return result
    workspace = directory / "submission"
    missing = [file for file in frozen["office_files"]
               if not (workspace / file).is_file() or (workspace / file).is_symlink()]
    if missing:
        result.update(diagnostic_status="MISSING_NATIVE_SUBMISSION", missing_files=missing)
        return result
    hashes = {file: digest(workspace / file) for file in frozen["office_files"]}
    declared = record.get("submission_sha256", {})
    if declared and hashes != declared:
        result.update(diagnostic_status="SUBMISSION_DIFFERS_FROM_RECORDED_HASH", observed_sha256=hashes)
        return result
    original_record_hash = digest(run_path)
    oracle = read(oracle_path)
    score = original_grade(workspace, oracle)
    result.update(diagnostic_status="ORIGINAL_GRADER_REPLAYED", submission_sha256=hashes,
                  original_grader_replay=score, failure_analysis=classify_failures(score, oracle))
    if rescore:
        result["numeric_output_rescore"] = grade(workspace, oracle)
    result["replay_matches_saved_original_grade"] = (
        score == read(raw_score_path) if raw_score_path.exists() else None)
    after = {file: digest(workspace / file) for file in frozen["office_files"]}
    result["source_records_and_submission_unchanged"] = (
        hashes == after and original_record_hash == digest(run_path)
        and (not raw_score_path.exists() or digest(raw_score_path) == result["recorded_native_evaluation_sha256"]))
    if not result["source_records_and_submission_unchanged"]:
        raise ValueError(f"Files changed during diagnostic readback: {run_path}")
    return result


def summary_rows(results):
    rows = []
    for row in results:
        score = row.get("original_grader_replay") or {}
        analysis = row.get("failure_analysis") or {}
        rows.append({"model": row["model"], "task": row["task"], "run_file": row["run_file"],
                     "recorded_status": row["runtime"]["original_status"],
                     "diagnostic_status": row["diagnostic_status"], "frozen_native_pass": score.get("pass"),
                     "groups": score.get("groups"), "semantic_values_pass": analysis.get("semantic_values_pass"),
                     **({"numeric_output_rescore_pass": row["numeric_output_rescore"]["pass"]}
                        if "numeric_output_rescore" in row else {}),
                     "equivalent_decimal_text_only": analysis.get("equivalent_decimal_text_only", False),
                     "same_members_wrong_required_order_only": analysis.get("same_members_wrong_required_order_only", False),
                     "required_roster_order_violated": analysis.get("required_roster_order_violated", False),
                     "failure_class_counts": analysis.get("failure_class_counts", {}),
                     "metadata_warning_misclassification": row["runtime"]["warning_only_misclassified_as_run_error"]})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Root run containing the original plan.json")
    parser.add_argument("--out", type=Path, required=True, help="New sidecar directory outside the raw run")
    parser.add_argument("--numeric-output-rescore", action="store_true", help="Also score with the current grader")
    args = parser.parse_args()
    root, out = args.run.resolve(), args.out.resolve()
    if out == root or root in out.parents or out in root.parents:
        raise ValueError("Write diagnostics outside the raw run and its ancestor directories")
    if out.exists():
        raise ValueError("Use a fresh output directory so prior diagnostic records remain intact")
    plan = read(root / "plan.json")
    project = Path(__file__).resolve().parent
    grader_relative = "multifile_reconstruction/reasoning_graph_mvp/logic_first/hotel_office.py"
    expected_grader_hash = plan.get("source_sha256", {}).get(grader_relative)
    grader_source = project / grader_relative
    original_grade = grade
    if expected_grader_hash and digest(grader_source) != expected_grader_hash:
        grader_source = root / "source_at_start" / grader_relative
        if not grader_source.is_file() or digest(grader_source) != expected_grader_hash:
            raise ValueError("Original native grader source differs from the frozen plan")
        name = grade.__module__.rsplit(".", 1)[0] + "._archived_hotel_office"
        spec = spec_from_file_location(name, grader_source)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        original_grade = module.grade
    results = []
    cancellations, excluded = cancelled_attempts(root)
    for path in sorted(root.rglob("run.json")):
        if path.parent.resolve() in excluded:
            continue
        record = diagnose_trial(path, plan, root, original_grade, rescore=args.numeric_output_rescore)
        if record is not None:
            results.append(record)
    rows = summary_rows(results)
    report = {"run_id": plan["run_id"], "recorded_utc": datetime.now(timezone.utc).isoformat(),
              "plan_sha256": digest(root / "plan.json"), "diagnostic_script_sha256": digest(Path(__file__)),
              "grader_sha256": digest(grader_source),
              "rescore_grader_sha256": digest(project / grader_relative) if args.numeric_output_rescore else None,
              "grader_hash_matches_original_plan": bool(expected_grader_hash),
              "snapshot_reader_sha256": digest(project / "multifile_reconstruction/revision.py"),
              "method": "Original grader replay; narrowly classified sidecar diagnostics; no raw records or submissions changed.",
              "planned_trials": plan.get("planned_trials"), "observed_records": len(results),
              "cancelled_duplicate_count": len(cancellations), "cancelled_attempts": cancellations,
              "rows": rows, "trials": results,
              "limits": ["Every original attempt remains listed; retries are not silently substituted.",
                         "Equivalent decimal text diagnosis requires exact Decimal equality and unchanged paragraph metadata and label.",
                         "Other text, formula, style, chart or protected-object differences are not automatically waived.",
                         "Same-members wrong roster order is an explicit task requirement violation, not a semantic membership failure or grader false negative.",
                         "Documented later-started duplicate cancellations are listed separately and excluded without comparing outputs or scores.",
                         "Semantic success does not by itself establish native-file preservation or natural business realism."]}
    out.mkdir(parents=True)
    (out / "diagnosis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 冻结任务实际提交独立复核", "", f"原运行 `{plan['run_id']}`。", "",
             "原题、Oracle、实际提交与原始分数均保留。本表重新运行原评分器；等值数字文本表示仅作单独诊断，不覆盖原判。", "",
             f"已登记取消的后启动重复任务 {len(cancellations)} 个，仅按开始时间保留先启动任务，不计模型失败或有效试验。", "",
             "| 模型 | 任务 | 原运行状态 | 原评分复核 | 语义值 | 仅同值小数文本 | 仅名单顺序错误 |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row[key]) for key in (
            "model", "task", "recorded_status", "frozen_native_pass", "semantic_values_pass", "equivalent_decimal_text_only", "same_members_wrong_required_order_only")) + " |")
    if args.numeric_output_rescore:
        lines += ["", "| 模型 | 原评分通过 | 新评分通过 |", "|---|---:|---:|"]
        for model in sorted({row["model"] for row in rows}):
            model_rows = [row for row in rows if row["model"] == model and "numeric_output_rescore_pass" in row]
            old = sum(row["frozen_native_pass"] for row in model_rows)
            new = sum(row["numeric_output_rescore_pass"] for row in model_rows)
            lines.append(f"| {model} | {old}/{len(model_rows)} | {new}/{len(model_rows)} |")
    lines += ["", "语义值全对不代表文件保护全对；所有其他变化保留逐项诊断。metadata fallback 警告不终止执行的证据也保留，但不声称该默认元数据对性能完全无影响。", "",
              "名单成员正确但顺序违反公开 Unicode 排序要求，单列为明确指令未遵守，不当作评分误判，也不当作成员判断算错。", "",
              "逐项原 grader 复核、文件哈希和错误分类见 diagnosis.json。未结束或发生真实运行错误的记录不会按最终模型提交评分。"]
    (out / "diagnosis.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"observed_records": len(rows), "planned_trials": plan.get("planned_trials"),
                      "cancelled_duplicate_count": len(cancellations),
                      "rows": rows, "output": str(out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
