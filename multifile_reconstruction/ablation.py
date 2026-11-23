"""Frozen-input paired pilot: self-organized JSON then edit, versus direct edit.

This compares an extra preparation turn/history/compute plus JSON against one
turn. One pair cannot isolate the causal contribution of JSON serialization.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import time

from .build import save, digest
from .run import assert_public_boundary, run_model
from .revision import FILES
from .upstreams import ROOT


def prepare(source, target):
    source, target = Path(source), Path(target)
    if target.exists():
        raise FileExistsError("Use a fresh arm directory, never reuse prior model scoring files")
    shutil.copytree(source / "public", target / "public")
    assert_public_boundary(target / "public")
    for name in ("truth.json", "bindings.json", "input_protocol.json", "sources.json"):
        dest = target / "private" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "private" / name, dest)
    for name in ("oracle.json", "fact_changes.json", "truth.json", "independent_assertions.json", "dependencies.json"):
        dest = target / "private/revision" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "private/revision" / name, dest)
    shutil.copytree(source / "private/revision/reference", target / "private/revision/reference")
    (target / "phase2").mkdir()
    instruction = (source / "phase2/instruction.md").read_text().replace(
        "Continue from your reconstructed state and the existing three Office files.",
        "Revise the existing three Office files.")
    (target / "phase2/instruction.md").write_text(instruction)
    protocol = json.loads((target / "private/input_protocol.json").read_text())
    if protocol["schema_provided"]:
        raise ValueError("Ablation requires the schema-free input protocol")


def usage(package):
    rows = []
    for relative in ("model/events.jsonl", "model/revision/events.jsonl"):
        path = Path(package) / relative
        if path.exists():
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event.get("type") == "turn.completed":
                    rows.append({"trace": relative, "usage": event.get("usage")})
    return rows


def export_pair(root, arms):
    """Readable files side by side, independent of the isolated agent mount."""
    destination = ROOT / "multifile_reconstruction"
    two, direct = arms["two_round"], arms["one_round"]
    groups = {"原始事实": (two / "public", two / "private/truth.json"),
              "反事实标准答案": (two / "private/revision/reference", two / "private/revision/truth.json"),
              "两轮6.1结果": (two / "model/revision/working", two / "model/revision/working/updated_state.json"),
              "单轮6.1结果": (direct / "model/revision/working", direct / "model/revision/working/updated_state.json")}
    manifest = {}
    for label, (folder, state) in groups.items():
        manifest[label] = []
        for source, short in zip(FILES, ("日程.xlsx", "说明.docx", "简报.pptx")):
            target = destination / (label + "_" + short)
            if (folder / source).exists():
                shutil.copy2(folder / source, target)
                assert digest(target) == digest(folder / source)
                manifest[label].append(target.name)
        if state.exists():
            target = destination / (label + "_状态.json")
            shutil.copy2(state, target)
            assert digest(target) == digest(state)
            manifest[label].append(target.name)
    initial = two / "model/reconstruction.json"
    if initial.exists():
        shutil.copy2(initial, destination / "两轮6.1原始重建_状态.json")
    (destination / "task.md").write_text(
        "# 最新单轮／两轮对照\n\n"
        "同一套原始Excel、Word、PPT；均不提供JSON框架或标准答案。\n\n"
        "- 两轮：先自行理解资产并组织JSON，再在原会话接收修改要求。\n"
        "- 单轮：新会话直接接收修改要求，从原始资产开始。\n"
        "- 原始事实_、反事实标准答案_：两组共用的输入与隐藏参考的查看副本。\n"
        "- 两轮6.1结果_、单轮6.1结果_：各组真实提交，不是标准答案。\n"
        "- 两轮6.1原始重建_状态.json：第一轮自行整理的笔记。\n\n"
        "自由结构JSON尚无自动语义评分，文件由相同冻结Oracle评分。"
        "额外准备轮次同时增加了推理预算与历史，不能将差异仅归因于JSON格式。"
        "此前模型6.1结果_文件属于旧的提供schema实验，历史记录保留。\n\n" +
        (two / "phase2/instruction.md").read_text())
    save(root / "export.json", manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "multifile_reconstruction/artifacts/ferry_revision_no_schema")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=420)
    args = parser.parse_args()
    root = args.out or ROOT / "multifile_reconstruction/artifacts" / ("json_ablation_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    root = root.resolve()
    arms = {key: root / key for key in ("two_round", "one_round")}
    for arm in arms.values():
        prepare(args.source, arm)
    hashes = {key: {n: digest(arm / "public" / n) for n in FILES} for key, arm in arms.items()}
    oracle_hashes = {key: digest(arm / "private/revision/oracle.json") for key, arm in arms.items()}
    assert hashes["two_round"] == hashes["one_round"]
    assert len(set(oracle_hashes.values())) == 1
    assert (arms["two_round"] / "phase2/instruction.md").read_bytes() == (arms["one_round"] / "phase2/instruction.md").read_bytes()
    plan = {"source": str(args.source.resolve()), "model_requested": "gpt-6.1-sol",
            "arms": {k: str(v) for k, v in arms.items()}, "input_sha256": hashes, "oracle_sha256": oracle_hashes,
            "schema_provided": False, "freeze_before_model_execution": True,
            "limitation": "One case/one draw per arm; two-round treatment adds preparation compute and full conversation history as well as a self-organized JSON. It is not an isolated JSON-format causal test."}
    from alignment_probe.codex_transport import configured_route
    route = configured_route()
    if route["model"] != "gpt-6.1-sol":
        raise ValueError("This requested comparison must use gpt-6.1-sol in both arms")
    plan["route"] = route
    save(root / "plan.json", plan)
    results = {}
    print("ABLATION_FROZEN", str(root), flush=True)
    for key, arm in arms.items():
        started = time.monotonic()
        print("ARM_STARTED", key, flush=True)
        try:
            run_model(arm, revision=key == "two_round", direct=key == "one_round", timeout=args.timeout)
            evaluation = json.loads((arm / "model/revision/evaluation.json").read_text())
            results[key] = {"status": "COMPLETED", **evaluation,
                            "wall_seconds": time.monotonic() - started, "usage": usage(arm)}
        except Exception as exc:
            results[key] = {"status": "RUN_ERROR", "error": type(exc).__name__ + ": " + str(exc),
                            "wall_seconds": time.monotonic() - started, "pass": None}
        results[key]["input_unchanged"] = hashes[key] == {n: digest(arm / "public" / n) for n in FILES}
        results[key]["oracle_unchanged"] = oracle_hashes[key] == digest(arm / "private/revision/oracle.json")
        save(root / "results.json", results)
        print("ARM_RESULT", key, json.dumps({k: v for k, v in results[key].items() if k not in {"failures", "usage"}}, ensure_ascii=False), flush=True)
    save(ROOT / "multifile_reconstruction/单轮与两轮对照.json", {"experiment": str(root), "plan": plan, "results": results})
    export_pair(root, arms)


if __name__ == "__main__":
    main()
