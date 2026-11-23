"""Export original, reference counterfactual and actual model files side by side."""
from pathlib import Path
import shutil

from .build import digest, save
from .evaluate import protocol_has_schema


def export_comparison(package, destination=None):
    package = Path(package).resolve()
    destination = Path(destination) if destination else Path(__file__).resolve().parent
    files = {"day_schedule.xlsx": "日程.xlsx", "visitor_notes.docx": "说明.docx", "travel_brief.pptx": "简报.pptx"}
    sources = {"原始事实": package / "public",
               "反事实标准答案": package / "private/revision/reference",
               "模型6.1结果": package / "model/revision/working"}
    json_sources = {"原始事实": package / "private/truth.json",
                    "反事实标准答案": package / "private/revision/truth.json",
                    "模型6.1结果": package / "model/revision/working/updated_state.json"}
    for source in sources.values():
        if any(not (source / name).is_file() for name in files):
            raise FileNotFoundError("Cannot export an incomplete original/reference/model comparison")
    if any(not source.is_file() for source in json_sources.values()):
        raise FileNotFoundError("Cannot export an incomplete three-state JSON comparison")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"source_package": str(package), "destination": str(destination.resolve()), "files": {}}
    for side, source in sources.items():
        manifest["files"][side] = {}
        for name, label in files.items():
            target = destination / (side + "_" + label)
            shutil.copy2(source / name, target)
            source_hash, copied_hash = digest(source / name), digest(target)
            if source_hash != copied_hash:
                raise AssertionError("Export changed file bytes")
            manifest["files"][side][target.name] = copied_hash
        target = destination / (side + "_状态.json")
        shutil.copy2(json_sources[side], target)
        if digest(json_sources[side]) != digest(target):
            raise AssertionError("Export changed JSON bytes")
        manifest["files"][side][target.name] = digest(target)
    protocol_note = (
        "实验标记：这组是此前提供了JSON组织框架的旧实验，不代表无框架重建能力；保留原始产物，不重写历史分数。\n\n"
        if protocol_has_schema(package) else
        "实验标记：本组未向模型提供JSON组织框架；模型状态JSON的语义正确性尚未自动评分，Office修改结果单独评分。\n\n")
    (destination / "task.md").write_text(
        "# 三套资产对照\n\n"
        + protocol_note +
        "本目录直接并排保存9个Office文件与3个状态JSON，按前缀区分：\n\n"
        "- 原始事实：取消渡轮前的 Excel、Word、PPT 和原始真值 JSON。\n"
        "- 反事实标准答案：评测端计算的取消渡轮后参考文件与真值 JSON。\n"
        "- 模型6.1结果：6.1 根据要求实际修改并保存的文件与 updated_state.json。\n\n"
        "标准答案与模型结果来源不同，分别保留；这组文件用于查看，模型运行时仅能访问公开输入。\n\n" +
        (package / "phase2/instruction.md").read_text())
    # Remove only the earlier exports whose bytes still match their originals.
    # Leave any user-edited or additional files intact.
    for legacy_name, source in (("before", sources["原始事实"]), ("after", sources["模型6.1结果"])):
        legacy = destination / legacy_name
        if legacy.is_dir():
            for name in files:
                path = legacy / name
                if path.is_file() and digest(path) == digest(source / name):
                    path.unlink()
            if not any(legacy.iterdir()):
                legacy.rmdir()
    save(package / "private/comparison_export.json", manifest)
    return {side: [str((destination / name).resolve()) for name in records]
            for side, records in manifest["files"].items()}
