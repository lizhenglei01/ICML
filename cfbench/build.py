import argparse
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

from .package import fingerprints, set_yoy_cache
from .parameters import DEFAULT_PARAMS, load_parameters, numeric_prior
from .task import derive, graph, paragraph_targets, percent, read_evidence
from .upstream import COHORT, FILES, OFFICE, TASK, patcher, provenance, sha256

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "artifacts"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(root=DEFAULT_OUT, params=DEFAULT_PARAMS):
    root = Path(root)
    manifest = provenance()
    ref, book, paras = read_evidence()
    old = derive(book["Review"]["B2"].value, book["Review"]["B4"].value)
    interventions = load_parameters(params)
    # Validate the entire batch before creating any task outputs.
    for item in interventions:
        derive(book["Review"]["B2"].value, numeric_prior(item))
    write_json(root / "parameters.json", {"schema": "cfbench-obm036-parameters-v1", "interventions": interventions})
    for item in interventions:
        name, comparator = item["id"], numeric_prior(item)
        task = root / name
        public, private = task / "public", task / "private"
        initial, reference = public / "input", private / "reference"
        initial.mkdir(parents=True, exist_ok=True)
        reference.mkdir(parents=True, exist_ok=True)
        for filename in FILES:
            shutil.copy2(ref / filename, initial / filename)
        # Only the synthetic comparator is intervened on. Do not mutate SEC data.
        patcher().patch_xlsx(initial / FILES[0], [
            {"type": "xlsx_cell", "sheet": "Review", "cell": "B4", "value": comparator},
        ])
        set_yoy_cache(initial / FILES[0], None)  # invalidate stale prior-state cache
        state = derive(book["Review"]["B2"].value, comparator)
        for filename in FILES:
            shutil.copy2(initial / filename, reference / filename)
        label = f"Synthetic prior-year management value, {comparator:,} million"
        patcher().patch_xlsx(reference / FILES[0], [
            {"type": "xlsx_cell", "sheet": "Approval", "cell": "B4", "value": label},
        ])
        targets = paragraph_targets(paras, state)
        editable_paragraphs = [i for i, text in targets.items() if text != paras[i]]
        patcher().patch_docx(reference / FILES[1], [
            {"type": "docx_replace", "old": paras[i], "new": text}
            for i, text in targets.items()
        ])
        set_yoy_cache(reference / FILES[0], state["yoy"])
        oracle = {
            "schema": "cfbench-obm036-v1", "task_id": name, "files": FILES,
            "required_updates": [
                {"id": "recomputed_yoy", "kind": "calculated_cell", "sheet": "Review", "cell": "B5", "value": state["yoy"]},
                {"id": "current_comparator_label", "kind": "cell", "sheet": "Approval", "cell": "B4", "value": label},
                {"id": "summary", "kind": "paragraph", "paragraph": 3,
                 "numbers": [f"{state['millions']:,.1f}", f"{comparator:,.1f}"]},
                {"id": "numeric_claim", "kind": "paragraph", "paragraph": 18,
                 "percent": percent(state["yoy"])},
                {"id": "semantic_claim", "kind": "paragraph", "paragraph": 19,
                 "percent": percent(state["yoy"], True), "direction": state["direction"],
                 "qualifier": state["qualifier"]},
            ],
            "protected_state": {
                "source_usd": state["usd"], "injected_prior": comparator,
                "formulas": {"B3": "=B2/1000000", "B5": "=B3/B4-1"},
                "editable_paragraphs": editable_paragraphs,
                "package_parts": fingerprints(initial, editable_paragraphs),
                "policy": "All package parts protected after masking only target text/value and formula caches; styles/run structure remain protected",
            },
            "fact_delta": {
                "changed": {k: {"before": old[k], "after": v} for k, v in state.items() if v != old[k]},
                "unchanged": {k: v for k, v in state.items() if v == old[k]},
            },
            "consistency_checks": ["summary_vs_workbook", "percentage_vs_workbook", "direction_vs_workbook"],
        }
        # Classify registered output bindings by actual before/after state.
        # Rounded statements can remain identical even when the source changes.
        required, preserved_outputs = [], []
        for check in oracle["required_updates"]:
            if check["kind"] == "paragraph":
                changed = check["paragraph"] in editable_paragraphs
            elif check["kind"] == "cell":
                changed = book[check["sheet"]][check["cell"]].value != check["value"]
            else:
                changed = old["yoy"] != state["yoy"]
            check["state_changed"] = changed
            (required if changed else preserved_outputs).append(check)
        oracle["required_updates"], oracle["protected_outputs"] = required, preserved_outputs
        # This public request overrides the old approval for CURRENT views only.
        # It contains no derived answer, paragraph addresses or hidden graph.
        (public / "instruction.md").write_text(
            "# 财务培训材料：模拟比较基期变更\n\n"
            f"本轮模拟上期比较数已批准改为 {comparator:,.1f} 百万美元，"
            "并已写入工作簿的 Prior comparator。请据此更新两份附件中所有现行引用、"
            "同比计算和方向性解释，保持彼此一致。保留可编辑公式，按原稿精度展示数值。\n\n"
            "本轮指令是现行模拟比较数的最新批准；工作簿中的现行批准摘要也需要同步。"
            "Word 末尾的签发记录及其原日期是上一轮历史记录，保留原文；"
            "SEC 原始事实、金额单位、其他数据和无关段落保持不变。"
            "所有模拟数值仅用于本轮反事实培训，不代表真实发行人历史业绩。\n\n"
            "交付：unit_review.xlsx、management_discussion.docx。\n", encoding="utf-8")
        attribution = (
            "OfficeEditBench, Aniriswu/OfficeEditBench, commit " + manifest["repositories"]["OfficeEditBench"]
            + ". Original code/narrative/layout: CC BY 4.0.\n"
            "https://github.com/Aniriswu/OfficeEditBench\n"
            "https://creativecommons.org/licenses/by/4.0/\n"
            "SEC frozen financial facts: https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets\n"
            "Upstream source ledger: medium/cohorts/cross_media/SOURCES.md.\n"
            "Derived training fixtures: CFBench changes only the synthetic prior comparator and current dependent views.\n"
            "Historical approvals and SEC facts remain unchanged. No endorsement is implied.\n")
        (public / "ATTRIBUTION.txt").write_text(attribution, encoding="utf-8")
        shutil.copy2(OFFICE / "LICENSE.txt", public / "UPSTREAM_LICENSE.txt")
        write_json(private / "truth.json", {"baseline": old, "counterfactual": state,
                   "semantic_flip": state["direction"] != old["direction"],
                   "evidence_graph": graph(book, paras),
                   "reference_text": targets})
        write_json(private / "oracle.json", oracle)
        write_json(private / "parameters.json", item)
        write_json(private / "dependencies.json", graph(book, paras))
        per_manifest = dict(manifest)
        per_manifest["generated_files"] = {
            str(f.relative_to(task)): sha256(f.read_bytes())
            for d in (initial, reference) for f in d.iterdir()
        }
        write_json(private / "source_manifest.json", per_manifest)
        # Only public/ is packaged for the solver. No private files or source code.
        with ZipFile(root / f"CFBench_OBM036_{name}_public.zip", "w") as z:
            for f in sorted(public.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(public))
    return root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    args = parser.parse_args()
    print(build(args.out, args.params))


if __name__ == "__main__":
    main()
