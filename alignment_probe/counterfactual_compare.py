"""Compare two actual transports on a controlled Office numeric-to-text edit.

The model receives the recalculated Review table and ALL original Word
paragraphs, not the hidden editable paragraph set or the expected direction.
Only model-proposed replacements are applied to native Word copies.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from zipfile import ZipFile

from docx import Document
from lxml import etree
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from alignment_probe.codex_transport import call_codex, configured_route
from alignment_probe.probe import SOURCE, source_manifest, write_json
from cfbench.upstream import patcher, verifier

X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def prepare(out, prior):
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "source_manifest.json", source_manifest())
    native = out / "input"
    native.mkdir()
    for name in ["unit_review.xlsx", "management_discussion.docx"]:
        shutil.copy2(SOURCE / name, native / name)
    p = patcher()
    book_path = native / "unit_review.xlsx"
    p.patch_xlsx(book_path, [{"type": "xlsx_cell", "sheet": "Review", "cell": "B4", "value": prior}])
    with ZipFile(book_path) as z:
        part = p._xlsx_sheet_paths(z)["Review"]
        tree = etree.fromstring(z.read(part))
    for cell in tree.findall(f".//{{{X}}}c"):
        if cell.find(f"{{{X}}}f") is not None:
            for v in cell.findall(f"{{{X}}}v"):
                cell.remove(v)
    p._rewrite_zip(book_path, {part: etree.tostring(tree, encoding="UTF-8", xml_declaration=True)})
    calculated = verifier().office_recalc(book_path)
    if calculated is None:
        raise RuntimeError("Native LibreOffice recalculation failed")
    # Independent fraction arithmetic; never take the oracle from the model.
    source = load_workbook(SOURCE / "unit_review.xlsx", data_only=True)
    current = Fraction(str(source["Facts"]["I2"].value)) / 1_000_000
    growth = (current - Fraction(str(prior))) / Fraction(str(prior))
    if not math.isclose(calculated["Review"]["B5"].value, float(growth), abs_tol=1e-12, rel_tol=1e-12):
        raise ValueError("Independent arithmetic disagrees with native calculation")
    for cell in tree.findall(f".//{{{X}}}c"):
        if cell.find(f"{{{X}}}f") is not None:
            etree.SubElement(cell, f"{{{X}}}v").text = str(calculated["Review"][cell.get("r")].value)
    p._rewrite_zip(book_path, {part: etree.tostring(tree, encoding="UTF-8", xml_declaration=True)})
    formulas = load_workbook(book_path, data_only=False)
    values = load_workbook(book_path, data_only=True)
    table = []
    for row in formulas["Review"]:
        table.append([{"cell": c.coordinate, "content": c.value,
                       "calculated_value": values["Review"][c.coordinate].value if c.data_type == "f" else None}
                      for c in row])
    paragraphs = [p.text for p in Document(native / "management_discussion.docx").paragraphs]
    # The only task-specific selection supplied to inference is the Review sheet;
    # paragraph IDs are all presented, with no target paragraph list.
    user = (f"最新批准：模拟上期比较数由 2300 改为 {prior} 百万美元，其余基础事实未变。"
            "下面的 Review 工作表已重新计算。请据此找到并修订 Word 中所有失效的现行金额引用、"
            "同比数值及方向性解释。尽量保留原句结构，无关段落保持原文。"
            "附录中的签发说明是上一轮历史记录，必须保留，不按本轮数字改写。"
            "不要修改公开事实、公式、单位或新增无依据的业务结论。\n"
            "只返回 JSON：{\"updates\":[{\"paragraph\":段落编号,\"text\":完整替换文本,"
            "\"evidence_cells\":[\"工作表!单元格\"]}]}。没有变化的段落不要列出。\n\n"
            + "Review 工作表：\n" + json.dumps(table, ensure_ascii=False)
            + "\nWord 全部段落（编号从0开始）：\n"
            + json.dumps([{"paragraph": i, "text": t} for i, t in enumerate(paragraphs)], ensure_ascii=False))
    request = {"id": "counterfactual_word_edit", "messages": [
        {"role": "system", "content": "根据给定表格修订既有办公文本。只输出要求的JSON，不调用工具。"},
        {"role": "user", "content": user}]}
    oracle = {"prior": prior, "current_millions": float(current), "growth_fraction": str(growth),
              "growth": float(growth), "direction": "下降" if growth < 0 else "增长" if growth > 0 else "持平",
              "percent_magnitude": round(abs(float(growth)) * 100, 1),
              "editable_paragraphs": [3, 18, 19],
              "basis": "Original paragraphs 18/20 and source formulas; developer-audited expected fields, not supplied to inference"}
    write_json(out / "public_request.json", request)
    write_json(out / "private/oracle.json", oracle)
    return request, oracle, paragraphs


def evaluate(text, oracle, paragraphs, directory):
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    updates = json.loads(raw)["updates"]
    if not isinstance(updates, list):
        raise ValueError("updates is not a list")
    observed = list(paragraphs)
    indices = set()
    ops = []
    for update in updates:
        i = update["paragraph"]
        if type(i) is not int or not 0 <= i < len(paragraphs) or i in indices:
            raise ValueError("Invalid or duplicate paragraph ID")
        if not isinstance(update["text"], str) or not paragraphs[i]:
            raise ValueError("Expected replacement text for an existing paragraph")
        indices.add(i)
        ops.append({"type": "docx_replace", "old": paragraphs[i], "new": update["text"]})
        observed[i] = update["text"]
    submission = directory / "management_discussion.docx"
    shutil.copy2(SOURCE / "management_discussion.docx", submission)
    patcher().patch_docx(submission, ops)
    actual = [p.text for p in Document(submission).paragraphs]
    checks = []

    def check(name, passed, observed_value=None):
        checks.append({"id": name, "passed": bool(passed), "observed": observed_value})

    numbers = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", actual[3])]
    check("summary_current_and_prior", numbers == [oracle["current_millions"], oracle["prior"]], actual[3])
    for i in [18, 19]:
        percentages = [float(n) for n in re.findall(r"([+-]?\d+(?:\.\d+)?)\s*%", actual[i])]
        check(f"paragraph_{i}_percent", len(percentages) == 1 and
              math.isclose(abs(percentages[0]), oracle["percent_magnitude"], abs_tol=1e-9), actual[i])
    # Current-comparison clause supports the expected phrasing and its minimal edits.
    match = re.search(r"当前值[^，。；%]{0,25}(?:模拟上期|上期)[^，。；%]{0,12}?(增长|下降|持平)", actual[19])
    check("current_semantic_direction", match is not None and match.group(1) == oracle["direction"], actual[19])
    numeric = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", actual[18])
    signed = float(numeric.group(1)) if numeric else None
    check("percentage_direction", signed is not None and
          ((signed < 0 and oracle["growth"] < 0) or
           (signed > 0 and oracle["growth"] > 0) or
           (signed > 0 and oracle["growth"] < 0 and "下降" in actual[18]) or
           (signed == 0 and oracle["growth"] == 0)), actual[18])
    for i, old in enumerate(paragraphs):
        if i not in oracle["editable_paragraphs"]:
            check(f"protected_paragraph_{i}", actual[i] == old)
    check("model_proposals_applied_exactly", actual == observed)
    with ZipFile(SOURCE / "management_discussion.docx") as a, ZipFile(submission) as b:
        check("native_package_members", a.namelist() == b.namelist() and b.testzip() is None)
        for part in a.namelist():
            if part != "word/document.xml":
                check("protected_part_" + part, a.read(part) == b.read(part))
        before = etree.fromstring(a.read("word/document.xml"))
        after = etree.fromstring(b.read("word/document.xml"))
        for root in [before, after]:
            ps = root.findall(f"{{{W}}}body/{{{W}}}p")
            for i in oracle["editable_paragraphs"]:
                for t in ps[i].iter(f"{{{W}}}t"):
                    t.text = "EDITABLE"
        check("paragraph_structure_and_formatting", etree.tostring(before, method="c14n") == etree.tostring(after, method="c14n"))
    return {"passed": all(c["passed"] for c in checks), "checks": checks,
            "selected_paragraphs": sorted(indices),
            "changes": [{"paragraph": i, "before": paragraphs[i], "after": actual[i]} for i in sorted(indices)],
            "limits": "Given the Review table and full Word text; mechanical patch application; not an autonomous Office agent or a generic semantic oracle"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prior", type=float, default=2500)
    parser.add_argument("--api-base-url", default="https://ai.ailink1.com/v1")
    parser.add_argument("--api-model", default="gpt-5.5")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--backends", nargs="+", choices=["codex-relay", "third-party-api"],
                        default=["codex-relay", "third-party-api"])
    args = parser.parse_args()
    out = args.out.resolve()
    request, oracle, paragraphs = prepare(out, args.prior)
    route = configured_route()
    key = args.api_key_file.read_text().strip() if args.api_key_file else os.environ.get("OPENAI_API_KEY")
    effort = route["effort"] or "xhigh"
    write_json(out / "comparison_setup.json", {"same_input": True, "parallel_calls": len(args.backends) > 1,
               "requested_reasoning_effort": effort, "request_sha256": hashlib.sha256(json.dumps(request, ensure_ascii=False).encode()).hexdigest(),
               "codex_model": route["model"], "api_model": args.api_model, "api_base_url": args.api_base_url,
               "limit": "Different model configurations: this does not isolate transport speed"})

    def run(backend):
        directory = out / backend
        directory.mkdir()
        started = time.perf_counter()
        try:
            if backend == "codex-relay":
                text, metadata = call_codex(request, route["model"], effort, directory / "transport")
            else:
                from openai import OpenAI
                if not key:
                    raise ValueError("Third-party API key is missing")
                # Installed openai 3.8 uses httpx2; its Brotli decoder is incompatible
                # with the local Brotli package. Avoid compression for this small probe
                # instead of changing shared Python dependencies.
                client = OpenAI(base_url=args.api_base_url, api_key=key, timeout=90, max_retries=0,
                                default_headers={"Accept-Encoding": "identity"})
                response = client.responses.create(model=args.api_model, input=request["messages"],
                           reasoning={"effort": effort}, max_output_tokens=5000, store=False)
                text = response.output_text
                metadata = {"requested_model": args.api_model, "reported_model": response.model,
                            "response_status": response.status, "usage": response.usage.model_dump() if response.usage else None,
                            "reasoning_effort_requested": effort, "response_id": response.id}
                if response.status != "completed":
                    metadata["incomplete_details"] = response.incomplete_details.model_dump() if response.incomplete_details else None
            elapsed = time.perf_counter() - started
            write_json(directory / "response.json", {"raw_response": text, "elapsed_seconds": elapsed, **metadata})
            report = evaluate(text, oracle, paragraphs, directory)
            report.update(backend=backend, elapsed_seconds=elapsed, model=metadata.get("reported_model"))
        except Exception as exc:
            report = {"backend": backend, "passed": False, "error_type": type(exc).__name__,
                      "http_status": getattr(exc, "status_code", None), "elapsed_seconds": time.perf_counter() - started}
            # Persist safe diagnostics without response bodies or supplied credentials.
            reason = str(exc.__cause__ or exc)
            if key:
                reason = reason.replace(key, "[REDACTED]")
            report["diagnostic"] = reason[:400]
        write_json(directory / "report.json", report)
        print(json.dumps({k: v for k, v in report.items() if k not in {"checks", "changes"}}, ensure_ascii=False), flush=True)
        return report

    reports = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run, name) for name in args.backends]):
            reports.append(future.result())
            write_json(out / "summary.json", {"reports": reports})
    if json.loads((out / "source_manifest.json").read_text()) != source_manifest():
        raise ValueError("Upstream sources changed")
    write_json(out / "summary.json", {"reports": reports, "upstream_unchanged": True,
                                      "limit": "Single call per different model; latency includes request overhead and generation"})


if __name__ == "__main__":
    main()
