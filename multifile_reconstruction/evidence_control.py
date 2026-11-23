"""Controlled 2x2 compilation, native readback, proof audit and change oracle.

No LLM calls. New, explicitly registered procurement case. Reuses Worldloom
renderers and the previous native snapshot adapter, not a new Office framework.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from fractions import Fraction
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile

from .build import save, digest
from .evidence_graph import scenario, values, rules, shortest_proof, evidence_subsets, verify_rewrites
from .indirect import replace_span
from .revision import snapshot
from .upstreams import ROOT, THIRD

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from worldloom.models import ArtifactIR, ArtifactSection, Table, Row, Column, Cell, Chart, ChartKind
from worldloom.presentation import READER
from worldloom.render import xlsx, docx, pptx

FILES = ("采购底稿.xlsx", "审批说明.docx", "风险简报.pptx")
LABELS = {
    "invoice_1": "第一批货款 / Batch one amount", "invoice_2": "第二批货款 / Batch two amount",
    "advance": "到账预付款 / Received advance", "factor": "计提系数 / Reserve factor",
    "insured": "保险有效 / Insurance valid", "clear_title": "无质押 / Clear title",
    "reserve": "核定准备金 / Certified reserve", "gross": "货款合计 / Gross amount",
    "net": "未结款 / Net balance", "money_ok": "准备金条件 / Reserve condition",
    "decision": "修订后放行 / Revised release",
    "owner": "复核负责人 / Review owner", "archive": "归档周期 / Archive cycle",
    "currency": "结算币种 / Settlement currency", "retention": "凭证留存 / Retention"}
CONTEXT = {"owner": "林女士 / Ms Lin", "archive": "每周 / Weekly", "currency": "人民币 / CNY", "retention": "三年 / Three years"}
UNMATERIALIZED = "按明细复算 / Recompute from records"
POLICY = (
    "审批口径：准备金＝（两批货款之和－到账预付款）×计提系数。计提系数大于零且不超过一，预付款不超过货款合计。"
    "仅当准备金不超过现行上限、保险有效且无质押三项同时满足时可放行。\n"
    "Policy: reserve = (batch one + batch two - received advance) × reserve factor. "
    "The factor is positive and at most one; the advance cannot exceed gross purchases. "
    "Release requires reserve within the active cap, valid insurance AND clear title. "
    "Amounts are in CNY 10k / 金额单位：万元。")
STATUS = {True: "可放行 / Release", False: "需复核 / Hold"}


def surface(value):
    if isinstance(value, bool):
        return "是 / Yes" if value else "否 / No"
    return str(float(value)).rstrip("0").rstrip(".") if isinstance(value, (float, Fraction)) else str(value)


def table(key, title, rows, column="记录 / Recorded"):
    return Table(key=key, title=title.replace(" / ", " · "), columns=[Column(key="value", label=column)],
                 rows=[Row(key="row_"+str(i), label=label, cells={"value": Cell(value=value)})
                       for i, (label, value) in enumerate(rows)])


def source_plan(world, *, dispersed, shallow):
    bundles = {f: [] for f in FILES}
    for project, base in world["projects"].items():
        v = values(base, world["cap_before"], world["cap_after"])
        for node in base:
            file = FILES[0] if not dispersed or node.startswith("invoice") else FILES[1] if node in {"advance", "insured"} else FILES[2]
            bundles[file].append({"project": project, "node": node, "value": v[node]})
        for node, value in CONTEXT.items():
            file = FILES[0] if dispersed else FILES[1] if node in {"owner", "archive"} else FILES[2]
            bundles[file].append({"project": project, "node": node, "value": value})
        bundles[FILES[0]].append({"project": project, "node": "reserve", "value": v["reserve"] if shallow else UNMATERIALIZED})
    return bundles


def label(project, node):
    return project + " · " + LABELS[node]


def render(package, world, bundles):
    public = package / "public"
    public.mkdir(parents=True, exist_ok=True)
    old = {p: values(b, world["cap_before"], world["cap_after"])["old_decision"] for p, b in world["projects"].items()}
    metadata = {"worldloom_created": "2026-01-01T00:00:00+00:00", "note": "Synthetic procurement review / 合成采购审查案例"}
    dashboard = table("decision", "审批快照 / Release snapshot", [(p, STATUS[x]) for p, x in old.items()])
    policy_table = table("cap", "现行政策 / Active policy", [("准备金上限 / Reserve cap", world["cap_before"])])
    workbook_sources = [(label(b["project"], b["node"]), surface(b["value"])) for b in bundles[FILES[0]]]
    source_table = table("inputs", "核验底稿 / Working evidence", workbook_sources)
    source_table = source_table.model_copy(update={"note": POLICY})
    allowance = table("office", "独立管理预算 / Independent office budget",
                      [("办公用品 / Stationery", 5), ("邮寄费用 / Postage", 7), ("管理预算合计 / Office total", 12)])
    wb_ir = ArtifactIR(id="procurement-workbook", intent_id="procurement", title="采购放行底稿 / Procurement workbook",
        sections=[ArtifactSection(heading="审批 / Review", table=dashboard),
                  ArtifactSection(heading="政策 / Policy", table=policy_table),
                  ArtifactSection(heading="证据 / Evidence", table=source_table),
                  ArtifactSection(heading="管理 / Administration", table=allowance)], metadata=metadata)
    (public / FILES[0]).write_bytes(xlsx.render(wb_ir))
    # A real, unrelated formula provides a preservation target. It is not a
    # hidden answer to any approval question.
    wb = load_workbook(public / FILES[0])
    for ws in wb:
        lookup = {c.value: c.row for c in ws["A"] if isinstance(c.value, str)}
        if "管理预算合计 / Office total" in lookup:
            ws.cell(lookup["管理预算合计 / Office total"], 2).value = f'=SUM(B{lookup["办公用品 / Stationery"]}:B{lookup["邮寄费用 / Postage"]})'
    wb.save(public / FILES[0])
    summaries = [f"{p} 项目当前结论 / Current decision: {STATUS[x]}。" for p, x in old.items()]
    cap_text = f"现行准备金上限 / Active reserve cap: {world['cap_before']} 万元 / CNY 10k."
    prose = [f"{label(b['project'], b['node'])}：{surface(b['value'])}。" for b in bundles[FILES[1]]]
    doc_ir = ArtifactIR(id="procurement-note", intent_id="procurement", title="采购审查说明 / Procurement review note",
        sections=[ArtifactSection(heading="执行摘要 / Executive note", body="\n\n".join(summaries+[cap_text])),
                  ArtifactSection(heading="审查口径 / Review policy", body=POLICY),
                  ArtifactSection(heading="付款与保险凭据 / Payment and insurance", body="\n\n".join(prose) or
                      "本版凭据集中存放于采购底稿。 / Supporting inputs are consolidated in the workbook."),
                  ArtifactSection(heading="独立安排 / Unaffected arrangements", body="审查负责人为林女士，归档周期为每周。 / Ms Lin owns the review; archive weekly.")], metadata=metadata)
    (public / FILES[1]).write_bytes(docx.render(doc_ir, presentation=READER))
    chart_table = table("release", "放行状态 / Release indicator", [(p, int(x)) for p, x in old.items()], "可放行=1，复核=0 / Release=1, hold=0")
    deck_source = table("source", "风险核验 / Risk evidence",
                        [(label(b["project"], b["node"]), surface(b["value"])) for b in bundles[FILES[2]]])
    sections = [ArtifactSection(heading="审查结论 / Review outcome", table=chart_table,
        charts=[Chart(key="release", title="项目放行 / Project release", kind=ChartKind.BAR, table="release", series=["value"], value_axis="Release=1 / Hold=0")]),
        ArtifactSection(heading="本批上限 / Active cap", body=cap_text),
        ArtifactSection(heading="审批依据 / Review basis", body=POLICY)]
    if bundles[FILES[2]]:
        sections.append(ArtifactSection(heading="核验记录 / Checks", table=deck_source))
    else:
        sections.append(ArtifactSection(heading="核验记录 / Checks", body="核验数据请参照采购底稿；图中仅记录当前放行状态。 / Consult the workbook for supporting data; the chart records current decisions only."))
    ppt_ir = ArtifactIR(id="procurement-brief", intent_id="procurement", title="采购风险简报 / Procurement risk briefing", sections=sections, metadata=metadata)
    (public / FILES[2]).write_bytes(pptx.render(ppt_ir, profile=READER, artifact_type="procurement_review"))
    (public / "instruction.md").write_text(
        f"财务委员会将准备金上限由{world['cap_before']}万元调整为{world['cap_after']}万元。请按文件中已有口径重新审查Alpha与Beta项目，"
        "更新三份文件中的现行上限与放行结论，并同步原生图表及内嵌数据；货款、预付款、系数、保险和权属事实没有变化。"
        "保留无关信息、公式和原生结构。请直接修改原文件，状态整理格式由你自行决定。\n\n"
        f"The committee changes the reserve cap from {world['cap_before']} to {world['cap_after']} (CNY 10k). "
        "Reassess Alpha and Beta using the existing policy, update the cap and decisions in all three native files, including editable chart data. "
        "Underlying invoices, advances, factors, insurance and title facts are unchanged. Preserve unrelated content, formulas and structure.\n")
    for name, ir in (("workbook", wb_ir), ("note", doc_ir), ("brief", ppt_ir)):
        save(package / "private" / (name+".ir.json"), ir.model_dump(mode="json"))


def atom_value(node, raw):
    if node in CONTEXT:
        return raw
    if node in {"insured", "clear_title", "decision", "old_decision"}:
        return raw in {True, 1, "是 / Yes", STATUS[True]}
    return Fraction(str(raw))


def extract(directory):
    """Discover registered evidence labels from native objects, including caches.

    This controlled grammar does not interpret arbitrary natural language.
    Unrecognized surface edits invalidate the compiled digest certificate.
    """
    directory = Path(directory)
    lookup = {label(p, n): (p, n) for p in ("Alpha", "Beta") for n in LABELS}
    observations = []
    def add(file, project, node, raw, location):
        if raw == UNMATERIALIZED:
            return
        observations.append({"fact_id": project+"."+node, "evidence_id": "e"+str(len(observations)+1),
            "project": project, "node": node, "value": atom_value(node, raw), "file": file, "location": location})
    wb = load_workbook(directory / FILES[0], data_only=False)
    for ws in wb:
        for row in ws.iter_rows():
            key, raw = row[0].value, row[1].value if len(row)>1 else None
            if key in lookup:
                p, n = lookup[key]; add(FILES[0], p, n, raw, {"sheet": ws.title, "cell": row[1].coordinate})
            elif key in {"Alpha", "Beta"} and raw in STATUS.values():
                add(FILES[0], key, "old_decision", raw, {"sheet": ws.title, "cell": row[1].coordinate})
    doc = Document(directory / FILES[1])
    for i, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text
        for key, (p, n) in lookup.items():
            if text.startswith(key+"："):
                raw = text[len(key)+1:].removesuffix("。");add(FILES[1], p, n, raw, {"paragraph": i, "text": text})
        for p in ("Alpha", "Beta"):
            prefix = p+" 项目当前结论 / Current decision: "
            if text.startswith(prefix):
                add(FILES[1], p, "old_decision", text[len(prefix):].removesuffix("。"), {"paragraph": i})
    deck = Presentation(directory / FILES[2])
    for si, slide in enumerate(deck.slides):
        for sh, shape in enumerate(slide.shapes):
            if shape.has_table:
                for ri, row in enumerate(shape.table.rows):
                    key, raw = row.cells[0].text, row.cells[1].text
                    if key in lookup:
                        p, n = lookup[key];add(FILES[2], p, n, raw, {"slide": si, "shape": sh, "row": ri, "column": 1})
                    elif key in {"Alpha", "Beta"} and raw in {"0", "1"}:
                        add(FILES[2], key, "old_decision", int(raw), {"slide": si, "shape": sh, "row": ri})
            if shape.has_chart:
                chart = shape.chart
                for point, category in enumerate(chart.plots[0].categories):
                    raw = chart.series[0].values[point]
                    if category.label in lookup:
                        p, n = lookup[category.label];add(FILES[2], p, n, raw, {"slide": si, "shape": sh, "point": point})
                    elif category.label in {"Alpha", "Beta"}:
                        add(FILES[2], category.label, "old_decision", int(raw), {"slide": si, "shape": sh, "point": point})
                embedded = load_workbook(BytesIO(chart.part.chart_workbook.xlsx_part.blob), data_only=False)
                for ws in embedded:
                    for row in ws.iter_rows():
                        if row[0].value in lookup:
                            p, n = lookup[row[0].value];add(FILES[2], p, n, row[1].value, {"embedded_sheet": ws.title, "cell": row[1].coordinate})
                        elif row[0].value in {"Alpha", "Beta"}:
                            add(FILES[2], row[0].value, "old_decision", int(row[1].value), {"embedded_sheet": ws.title, "cell": row[1].coordinate})
    return observations


def jsonable(value):
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def difficulty(package, world, *, expected_files, expected_depth):
    obs = extract(Path(package) / "public")
    alpha = values(world["projects"]["Alpha"], world["cap_before"], world["cap_after"])
    for o in obs:
        wanted = CONTEXT[o["node"]] if o["node"] in CONTEXT else values(world["projects"][o["project"]], world["cap_before"], world["cap_after"])[o["node"]]
        if o["value"] != wanted:
            raise AssertionError("Rendered evidence differs from hidden business truth")
    selected = [o for o in obs if o["project"] == "Alpha" and o["node"] not in CONTEXT]
    proofs = shortest_proof(selected, alpha)
    subsets = evidence_subsets(selected, alpha, world["cap_before"], world["cap_after"], FILES)
    surface_hashes = {f: digest(Path(package)/"public"/f) for f in FILES}
    manifest=Path(package)/"private/compiled_surface.json"
    compiled=json.loads(manifest.read_text()) if manifest.exists() else None
    surface_matches=compiled is not None and compiled["sha256"]==surface_hashes
    certificate = {"target": "Alpha counterfactual release decision", "expected_files": expected_files,
        "expected_depth": expected_depth, "business_graph_depth": 5, "shortest_registered_proof": proofs,
        "file_necessity": subsets, "surface_sha256": surface_hashes,
        "surface_matches_compiled_manifest":surface_matches,
        "pass": surface_matches and proofs["minimum_depth"] == expected_depth and subsets["minimum_office_files_in_audit_domain"] == expected_files,
        "limitations": ["Shortest proof is relative to the declared operators/rewrites, not arbitrary reasoning.",
                       "Native source text uses a controlled bilingual grammar. Semantic faithfulness of arbitrary prose is not certified.",
                       "Domain enumeration supplies explicit ambiguity countermodels; it is not an unrestricted theorem prover.",
                       "Independent human review has not been performed."]}
    save(Path(package)/"private/evidence_map.json", jsonable(obs))
    save(Path(package)/"private/difficulty.json", jsonable(certificate))
    if not certificate["pass"]:
        raise AssertionError("Actual registered complexity does not match requested layout")
    return certificate


def reference(package, world):
    folder = package / "private/reference"
    folder.mkdir(parents=True, exist_ok=True)
    for file in FILES:
        shutil.copy2(package/"public"/file, folder/file)
    old, new = world["cap_before"], world["cap_after"]
    v = {p: values(b, old, new) for p,b in world["projects"].items()}
    wb = load_workbook(folder/FILES[0])
    for ws in wb:
        for row in ws.iter_rows():
            if row[0].value == "准备金上限 / Reserve cap": row[1].value = new
            if row[0].value in v and row[1].value in STATUS.values(): row[1].value = STATUS[v[row[0].value]["decision"]]
    wb.save(folder/FILES[0])
    doc = Document(folder/FILES[1])
    for paragraph in doc.paragraphs:
        text = paragraph.text
        changed = text.replace(f"cap: {old} ",f"cap: {new} ")
        for p, state in v.items():
            if text.startswith(p+" 项目当前结论"):
                changed = text.replace(STATUS[state["old_decision"]],STATUS[state["decision"]])
        if changed != text: replace_span(paragraph,0,len(text),changed)
    doc.save(folder/FILES[1])
    deck = Presentation(folder/FILES[2])
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text=paragraph.text; changed=text.replace(f"cap: {old} ",f"cap: {new} ")
                    if changed!=text: replace_span(paragraph,0,len(text),changed)
            if shape.has_table:
                for row in shape.table.rows:
                    if row.cells[0].text in v and row.cells[1].text in {"0","1"}:
                        row.cells[1].text=str(int(v[row.cells[0].text]["decision"]))
            if shape.has_chart:
                codes=shape.chart._chartSpace.xpath(".//c:val/c:numRef/c:numCache/c:formatCode")
                chart_data=CategoryChartData(number_format=codes[0].text if codes else "General")
                chart_data.categories=list(v)
                chart_data.add_series(shape.chart.series[0].name,[int(s["decision"]) for s in v.values()])
                shape.chart.replace_data(chart_data)
    deck.save(folder/FILES[2])
    before,after=snapshot(package/"public",FILES),snapshot(folder,FILES)
    oracle={"checks":[{"object":k,"group":"required" if before[k]!=after[k] else "protected","expected":after[k]} for k in before],
            "files":list(FILES),"semantic_truth":{p:{"before":s["old_decision"],"after":s["decision"]} for p,s in v.items()},
            "expected_cap":new,
            "contract":"Local edits; whitespace-equivalent prose accepted; native source data and unrelated formula are protected."}
    if set(before)!=set(after): raise AssertionError("Reference changed native object inventory")
    save(package/"private/oracle.json",oracle)
    return oracle


def grade(directory, oracle):
    try:
        actual=snapshot(directory,tuple(oracle["files"]))
        checks=[{**c,"observed":actual.get(c["object"]),"pass":c["object"] in actual and actual[c["object"]]==c["expected"]} for c in oracle["checks"]]
        extras=set(actual)-{c["object"] for c in checks}
        checks += [{"object":k,"group":"protected","pass":False,"observed":actual[k],"expected":"absent"} for k in sorted(extras)]
        expected={f:{"cap":oracle["expected_cap"],"Alpha":oracle["semantic_truth"]["Alpha"]["after"],
                     "Beta":oracle["semantic_truth"]["Beta"]["after"]} for f in FILES}
        observed=semantic_state(directory)
        for file, state in expected.items():
            for key, value in state.items():
                checks.append({"object":file+":"+key,"group":"semantic","expected":value,
                    "observed":observed[file].get(key),"pass":type(value) is type(observed[file].get(key)) and value==observed[file].get(key)})
    except Exception as exc:
        checks=[{"object":"native_parse","group":"structure","pass":False,"error":str(exc)}]
    semantic={}
    for target in ("cap","Alpha","Beta"):
        subset=[c for c in checks if c["group"]=="semantic" and c["object"].endswith(":"+target)]
        semantic[target]={"correct_locations":sum(c["pass"] for c in subset),"total_locations":len(FILES),
                          "pass":len(subset)==len(FILES) and all(c["pass"] for c in subset)}
    return {"pass":bool(checks) and all(c["pass"] for c in checks),"semantic_targets":semantic,"checks":checks}


def control_submissions(package, oracle):
    root=package/"private/controls"; results={}
    for name in ("correct","xlsx_only","damaged_formula","changed_unrelated_project","chart_cache_mismatch","broken_package"):
        directory=root/name; directory.mkdir(parents=True,exist_ok=True)
        for file in FILES:shutil.copy2(package/"private/reference"/file,directory/file)
        if name=="xlsx_only":
            for file in FILES[1:]:shutil.copy2(package/"public"/file,directory/file)
        elif name=="damaged_formula":
            wb=load_workbook(directory/FILES[0]); cell=next(c for ws in wb for row in ws for c in row if c.data_type=="f")
            cell.value=999;wb.save(directory/FILES[0])
        elif name=="changed_unrelated_project":
            doc=Document(directory/FILES[1])
            p=next(p for p in doc.paragraphs if p.text.startswith("Beta 项目当前结论"))
            replace_span(p,0,len(p.text),p.text.replace(STATUS[True],STATUS[False]));doc.save(directory/FILES[1])
        elif name=="chart_cache_mismatch":
            from lxml import etree
            path=directory/FILES[2]
            with ZipFile(path) as z:parts={n:z.read(n) for n in z.namelist()}
            key=next(n for n in parts if n.startswith("ppt/charts/chart") and n.endswith(".xml"))
            tree=etree.fromstring(parts[key]);ns={"c":"http://schemas.openxmlformats.org/drawingml/2006/chart"}
            tree.xpath("//c:val/c:numRef/c:numCache/c:pt/c:v",namespaces=ns)[0].text="0"
            parts[key]=etree.tostring(tree)
            with ZipFile(path,"w") as z:
                for n,data in parts.items():z.writestr(n,data)
        elif name=="broken_package":(directory/FILES[1]).write_bytes(b"corrupt")
        report=grade(directory,oracle);save(root/(name+".json"),report)
        if report["pass"]!=(name=="correct"):raise AssertionError("Bad oracle control: "+name)
        results[name]={"pass":report["pass"],"semantic_targets":report["semantic_targets"],
                       "failed_objects":[c["object"] for c in report["checks"] if not c["pass"]]}
    save(root/"summary.json",results)
    return results


def semantic_state(directory):
    """Read common business targets independently of native snapshot equality."""
    directory=Path(directory); result={f:{} for f in FILES}
    wb=load_workbook(directory/FILES[0],data_only=False)
    for ws in wb:
        for row in ws.iter_rows():
            key,raw=row[0].value,row[1].value if len(row)>1 else None
            if key=="准备金上限 / Reserve cap":result[FILES[0]]["cap"]=int(raw)
            if key in {"Alpha","Beta"} and raw in STATUS.values():result[FILES[0]][key]=raw==STATUS[True]
    doc=Document(directory/FILES[1]); text="\n".join(p.text for p in doc.paragraphs)
    cap=re.findall(r"Active reserve cap: (\d+)",text)
    if len(cap)==1:result[FILES[1]]["cap"]=int(cap[0])
    for p in ("Alpha","Beta"):
        found=re.findall(re.escape(p)+r" 项目当前结论 / Current decision: (.*?)。",text)
        if len(found)==1 and found[0] in STATUS.values():result[FILES[1]][p]=found[0]==STATUS[True]
    deck=Presentation(directory/FILES[2]); text="\n".join(s.text for sl in deck.slides for s in sl.shapes if s.has_text_frame)
    cap=re.findall(r"Active reserve cap: (\d+)",text)
    if len(cap)==1:result[FILES[2]]["cap"]=int(cap[0])
    for sl in deck.slides:
        for shape in sl.shapes:
            if shape.has_chart:
                chart=shape.chart
                for i,category in enumerate(chart.plots[0].categories):
                    if category.label in {"Alpha","Beta"}:
                        result[FILES[2]][category.label]=chart.series[0].values[i]==1
    return result


def build(root):
    root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    world=scenario(); rewrite=verify_rewrites()
    save(root/"private/scenario.json",world)
    states={p:values(b,world["cap_before"],world["cap_after"]) for p,b in world["projects"].items()}
    save(root/"private/truth.json",jsonable(states))
    save(root/"private/graph.json",{"rules":[asdict(r) for r in rules(states["Alpha"])],"rewrite_verification":rewrite,
         "manual_registration":"Business formula, conjunction policy, legal domains and equivalent rewrites are explicitly authored; not automatically discovered from arbitrary documents."})
    summary={"case":world["case"],"variants":{},"model_runs":False,"human_review":False}
    for shallow,dispersed in ((True,False),(True,True),(False,False),(False,True)):
        depth,files=(2 if shallow else 4),(3 if dispersed else 1)
        key=f"D{depth}-F{files}"; p=root/key
        save(p/"private/scenario.json",world)
        save(p/"private/truth.json",jsonable(states))
        bundles=source_plan(world,dispersed=dispersed,shallow=shallow)
        render(p,world,bundles)
        save(p/"private/compiled_surface.json",{"sha256":{f:digest(p/"public"/f) for f in FILES},
            "surface_language":"Fixed bilingual templates, no model-authored prose. Unexpected surface edits invalidate the certificate rather than being interpreted as harmless."})
        cert=difficulty(p,world,expected_files=files,expected_depth=depth)
        oracle=reference(p,world)
        correct=grade(p/"private/reference",oracle)
        if not correct["pass"]: raise AssertionError("Reference rejected")
        save(p/"private/reference_evaluation.json",correct)
        controls=control_submissions(p,oracle)
        summary["variants"][key]={"minimum_registered_depth":cert["shortest_registered_proof"]["minimum_depth"],
            "minimum_office_files":cert["file_necessity"]["minimum_office_files_in_audit_domain"],
            "reference_pass":correct["pass"],"office_files":len(FILES),
            "oracle_controls":{k:v["pass"] for k,v in controls.items()},
            "source_slots":{f:len(b) for f,b in bundles.items()},
            "source_fact_count":sum(x["node"] not in CONTEXT and x["value"] != UNMATERIALIZED for b in bundles.values() for x in b),
            "business_truth_sha256":digest(p/"private/truth.json"),
            "instruction_sha256":digest(p/"public/instruction.md"),
            "artifact_sizes":{f:(p/"public"/f).stat().st_size for f in FILES}}
    if len({v["business_truth_sha256"] for v in summary["variants"].values()})!=1:raise AssertionError("Truth changed across variants")
    if len({v["instruction_sha256"] for v in summary["variants"].values()})!=1:raise AssertionError("Business event changed across variants")
    if len({json.dumps(v["source_slots"],sort_keys=True) for v in summary["variants"].values()})!=1:raise AssertionError("Source slot counts differ")
    summary["scope"]="Registered-rule and finite-countermodel validation only. Business truth/event/source-slot counts match; exact text lengths and reading difficulty are not fully matched. No causal model-performance claim."
    save(root/"summary.json",summary)
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=ROOT/"multifile_reconstruction/artifacts"/("evidence_control_"+datetime.now().strftime("%Y%m%d_%H%M%S")))
    args=parser.parse_args(); print(json.dumps(build(args.out),ensure_ascii=False,indent=2))


if __name__=="__main__": main()
