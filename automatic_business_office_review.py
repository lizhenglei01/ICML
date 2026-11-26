#!/usr/bin/env python3
"""Inspect frozen Office task packages without building tasks or calling models.

Examples (run from ICM2027):

    python automatic_business_office_review.py --source artifacts/RUN/verified \
        --out artifacts/office_review
    python automatic_business_office_review.py --source artifacts/RUN/verified \
        --out artifacts/office_review --render

The first command parses all public assets and records potential layout issues.
The second also exports PDFs using independent LibreOffice profiles, extracts
PDF text from every page, and creates selected page previews. Hash-verified
renderings from this script are reused. Original Office files are never saved.

Neither successful parsing nor rendering establishes natural business language
or visual correctness. Open the PNG/PDF files for human/assistant inspection.
An optional --visual-review-json records an explicitly supplied review, including
which pages were actually viewed; it is never inferred from file generation.
The JSON should contain reviewer, scope, and pages (task/file/page) fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation


OFFICE_NAMES = ("review.xlsx", "policy.docx", "briefing.pptx")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def packages(source: Path):
    """Accept the verified root, one topic folder, or one case folder."""
    found = []
    for instruction in sorted(source.rglob("public/instruction.md")):
        public = instruction.parent
        package = public.parent
        if package.parent.name != "layouts":
            continue
        if not all((public / name).is_file() for name in OFFICE_NAMES):
            raise ValueError(f"Missing native Office input in {public}")
        if any((public / name).is_symlink() for name in (*OFFICE_NAMES, "instruction.md")):
            raise ValueError(f"Symlinked public asset in {public}")
        topic = package.parent.parent.parent.name
        found.append((f"{topic}/{package.name}", package))
    if not found:
        raise ValueError(f"No Office task packages under {source}")
    if len({key for key, _ in found}) != len(found):
        raise ValueError("Duplicate topic/layout identifiers; select a single frozen source root")
    return found


def extract(public: Path) -> dict:
    workbook = load_workbook(public / "review.xlsx", data_only=False)
    excel, potential_clipping = {}, []
    for sheet in workbook:
        cells = []
        for row in sheet:
            for cell in row:
                if cell.value is None:
                    continue
                cells.append({"cell": cell.coordinate, "value": cell.value,
                              "number_format": cell.number_format,
                              "wrap_text": bool(cell.alignment.wrap_text)})
                if isinstance(cell.value, str) and len(cell.value) > 30 and not cell.alignment.wrap_text:
                    potential_clipping.append({"sheet": sheet.title, "cell": cell.coordinate,
                                               "characters": len(cell.value),
                                               "column_width": sheet.column_dimensions[cell.column_letter].width})
        excel[sheet.title] = {"dimension": sheet.calculate_dimension(), "state": sheet.sheet_state,
                              "cells": cells,
                              "column_widths": {k: v.width for k, v in sheet.column_dimensions.items()}}
    workbook.close()
    document = Document(public / "policy.docx")
    word = {"paragraphs": [{"index": i, "text": p.text, "style": p.style.name}
                            for i, p in enumerate(document.paragraphs)],
            "tables": [[[cell.text for cell in row.cells] for row in table.rows]
                       for table in document.tables]}
    ppt = Presentation(public / "briefing.pptx")
    slides = []
    for number, slide in enumerate(ppt.slides, 1):
        shapes = []
        for shape in slide.shapes:
            record = {"name": shape.name, "type": str(shape.shape_type),
                      "bounds_emu": [shape.left, shape.top, shape.width, shape.height]}
            if shape.has_text_frame:
                record["text"] = shape.text
            if shape.has_table:
                record["table"] = [[cell.text for cell in row.cells] for row in shape.table.rows]
            if shape.has_chart:
                record["chart"] = {
                    "categories": [category.label for category in shape.chart.plots[0].categories],
                    "series": [{"name": series.name, "values": list(series.values)}
                               for series in shape.chart.series],
                }
            shapes.append(record)
        slides.append({"slide": number, "shapes": shapes})
    return {"instruction": (public / "instruction.md").read_text(encoding="utf-8"),
            "excel": excel, "word": word, "ppt": slides,
            "potential_unwrapped_long_cells": potential_clipping}


def render(source: Path, destination: Path, old: dict, *, timeout: int) -> dict:
    import fitz

    destination.mkdir(parents=True, exist_ok=True)
    pdf = destination / f"{source.stem}.pdf"
    source_hash = digest(source)
    reused = (old.get("source_sha256") == source_hash and pdf.is_file()
              and old.get("pdf_sha256") == digest(pdf))
    item = {"source": str(source), "source_sha256": source_hash,
            "pdf": str(pdf), "reused_verified_render": reused}
    start = time.monotonic()
    if not reused:
        executable = shutil.which("libreoffice")
        if not executable:
            return {**item, "success": False, "error": "LibreOffice executable unavailable"}
        with tempfile.TemporaryDirectory(prefix="office-review-profile-") as profile:
            fresh_output = Path(profile) / "export"
            fresh_output.mkdir()
            try:
                result = subprocess.run(
                    [executable, f"-env:UserInstallation={Path(profile).as_uri()}",
                     "--headless", "--convert-to", "pdf", "--outdir", str(fresh_output), str(source)],
                    capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return {**item, "success": False, "error": "LibreOffice conversion timed out",
                        "seconds": time.monotonic() - start}
            item.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            fresh_pdf = fresh_output / pdf.name
            if result.returncode != 0 or not fresh_pdf.is_file():
                return {**item, "success": False, "error": "Conversion did not produce a fresh PDF"}
            shutil.copyfile(fresh_pdf, pdf)
    try:
        with fitz.open(pdf) as document:
            # These are generated previews, not a claim that anyone viewed them.
            if source.suffix == ".xlsx":
                preview_pages = [0]
            elif source.suffix == ".docx":
                preview_pages = list(range(len(document)))
            else:
                preview_pages = sorted({0, min(2, len(document) - 1), max(0, len(document) - 2)})
            pages = []
            for i, page in enumerate(document):
                record = {"page": i + 1, "text": page.get_text()}
                if i in preview_pages:
                    preview = destination / f"{source.stem}_page_{i + 1}.png"
                    page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(preview)
                    record["preview"] = str(preview)
                pages.append(record)
    except Exception as exc:
        return {**item, "success": False, "error": f"PDF read failed: {type(exc).__name__}: {exc}"}
    return {**item, "success": True, "seconds": time.monotonic() - start,
            "pdf_sha256": digest(pdf), "page_count": len(pages), "pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--render", action="store_true", help="Export PDF and preview pages with LibreOffice")
    parser.add_argument("--timeout", type=int, default=60, help="Seconds allowed for each Office export")
    parser.add_argument("--visual-review-json", type=Path,
                        help="Optional explicitly supplied human/assistant page inspection record")
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out == source or out.is_relative_to(source) or source.is_relative_to(out):
        parser.error("Use a separate output folder outside the frozen source tree")
    selected = packages(source)
    out.mkdir(parents=True, exist_ok=True)
    render_log = out / "render_log.json"
    prior = json.loads(render_log.read_text(encoding="utf-8")) if render_log.is_file() else []
    cache = {row["source"]: row for row in prior}
    extracts, inventory, renders = {}, [], []
    for key, package in selected:
        public = package / "public"
        names = (*OFFICE_NAMES, "instruction.md")
        before = {name: digest(public / name) for name in names}
        data = extract(public)
        extracts[key] = data
        manifest = package / "private/native_manifest.json"
        original = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
        item = {"task": key, "public": str(public), "public_sha256": before,
                "sheets": len(data["excel"]), "slides": len(data["ppt"]),
                "formulas": sum(str(cell["value"]).startswith("=")
                                for sheet in data["excel"].values() for cell in sheet["cells"]),
                "charts": sum("chart" in shape for slide in data["ppt"] for shape in slide["shapes"]),
                "potential_unwrapped_long_cells": data["potential_unwrapped_long_cells"],
                "manifest_matches": ({name: before[name] for name in OFFICE_NAMES}
                                     == original["public_sha256"]) if original else None}
        if args.render:
            for name in OFFICE_NAMES:
                src = public / name
                rendered = render(src, out / "office_previews" / key,
                                  cache.get(str(src), {}), timeout=args.timeout)
                rendered["task"] = key
                renders.append(rendered)
        after = {name: digest(public / name) for name in names}
        item["source_hashes_unchanged"] = before == after
        inventory.append(item)
        print(f"Inspected {key}: {item['sheets']} sheets, {item['slides']} slides", flush=True)
    supplied_review = None
    if args.visual_review_json:
        supplied_review = json.loads(args.visual_review_json.read_text(encoding="utf-8"))
        if not isinstance(supplied_review, dict) or not all(k in supplied_review for k in ("reviewer", "scope", "pages")):
            parser.error("Visual review JSON must contain reviewer, scope, and pages")
    summary = {
        "source": str(source), "packages": inventory,
        "coverage": {"instructions_parsed": len(selected), "office_files_parsed": len(selected) * 3,
                     "render_requested": args.render,
                     "render_successes": sum(r["success"] for r in renders),
                     "render_failures": sum(not r["success"] for r in renders),
                     "pdf_pages_text_extracted": sum(r.get("page_count", 0) for r in renders),
                     "preview_pages_generated": sum("preview" in p for r in renders for p in r.get("pages", []))},
        "explicit_visual_review": supplied_review,
        "limitations": [
            "Long unwrapped cells are potential clipping candidates; confirm actual clipping in the UI/PDF.",
            "PDF generation and text extraction do not certify visual correctness or natural language quality.",
            "Generated preview pages are not automatically marked as visually inspected.",
            "Only an explicitly supplied visual review records pages viewed; other pages remain unreviewed.",
            "This audit does not validate industry definitions, final GT, or all possible reasoning shortcuts.",
            "No Agent or LLM is called; original Office files and benchmark code are not modified.",
        ],
    }
    save(out / "office_extracted.json", extracts)
    save(out / "office_inventory.json", summary)
    if args.render:
        save(render_log, renders)
    lines = ["# Office 文件解析与渲染记录", "", f"源目录：`{source}`", "",
             "此报告记录机器检查范围。材料是否自然、图表是否清楚、文字是否被遮挡，仍须人或助手实际看图。",
             "生成预览不等于已经完成视觉审查。", "",
             "|任务|工作表|公式|PPT页|图表|长文本未换行候选|源哈希未变|", "|---|---:|---:|---:|---:|---:|---|"]
    for row in inventory:
        lines.append(f"|{row['task']}|{row['sheets']}|{row['formulas']}|{row['slides']}|{row['charts']}|"
                     f"{len(row['potential_unwrapped_long_cells'])}|{row['source_hashes_unchanged']}|")
    lines += ["", "## 覆盖范围", "", json.dumps(summary["coverage"], ensure_ascii=False), "",
              "## 实际视觉核对", "", json.dumps(supplied_review, ensure_ascii=False, indent=2)
              if supplied_review else "未提供逐页视觉核对记录。本脚本不能声称已看过任何一页。", "",
              "## 局限", ""] + ["- " + item for item in summary["limitations"]]
    (out / "office_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = any(not row["source_hashes_unchanged"] or row["manifest_matches"] is False for row in inventory)
    return int(bad or any(not row["success"] for row in renders))


if __name__ == "__main__":
    raise SystemExit(main())
