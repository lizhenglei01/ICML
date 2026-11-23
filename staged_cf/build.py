"""Reuse source tables and query engine; package only uncomputed source facts publicly."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.styles import Font

from medical_cf.run import save
from medical_cf.upstream import REPO as DOC2DB_REPO
from schema_cf.input import load, token
from schema_cf.engine import analyze, apply, facts, values
from schema_cf.independent import verify, q
from schema_cf.demo import plain_xlsx
from schema_cf.upstream_office import normalize_generated_workbook, patcher, verify_diff, provenance

DEFAULT_CASE = DOC2DB_REPO / "dataset/sports/case1"
DEFAULT_PROFILE = Path(__file__).with_name("sports.json")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def public_goals(profile):
    return [{k:g[k] for k in ("id", "question", "type")} for g in profile["goals"]]


def match_view(goal, views):
    spec = goal["plan"]
    found = [v for v in views if v["source"] == spec["source"] and v["path"]
             and v["path"][-1]["target"] == spec["target"] and v["aggregate"] == spec["aggregate"]
             and v["distinct_column"] == spec.get("distinct_column") and v["filter"] == spec.get('filter')]
    if len(found) != 1:
        raise ValueError(f"Ambiguous or missing query for {goal['id']}: {len(found)} candidates")
    return found[0]


def goal_truth(rows, profile, views):
    stats = values(rows, views)
    result, dependencies = {}, {}
    for g in profile["goals"]:
        if "total" in g:
            result[g["id"]] = len(rows[g["total"]["table"]])
            dependencies[g["id"]] = {"operator": "row_count", "table": g["total"]["table"]}
        else:
            view = match_view(g, views)
            fid = view["id"] + "/leaders" if g.get("result") == "leaders" else view["id"] + "/group/" + token(g["group"]) + "/count"
            result[g["id"]] = stats[fid]
            dependencies[g["id"]] = {"fact_id": fid, "view": view}
    return result, dependencies


def independent_goals(rows, schemas, profile, dependencies, expected):
    """Goal projection checked independently via SQL, including ungrouped totals."""
    db = sqlite3.connect(":memory:")
    for table, s in schemas.items():
        cols = [c["name"] for c in s["columns"]]
        db.execute(f"CREATE TABLE {q(table)} ("+",".join(q(c) for c in cols)+")")
        db.executemany(f"INSERT INTO {q(table)} VALUES ("+",".join("?" for c in cols)+")", [[r[c] for c in cols] for r in rows[table]])
    checks = []
    for g in profile["goals"]:
        if "total" in g:
            sql = "SELECT COUNT(*) FROM " + q(g["total"]["table"])
            actual = db.execute(sql).fetchone()[0]
        else:
            v = dependencies[g["id"]]["view"]
            joins = " ".join(f"JOIN {q(e['target'])} a{i+1} ON a{i}.{q(e['column'])}=a{i+1}.{q(e['target_column'])}" for i,e in enumerate(v["path"]))
            end = f"a{len(v['path'])}.{q(v['path'][-1]['target_column'])}"
            measure = "COUNT(*)" if v["aggregate"] == "count" else f"COUNT(DISTINCT a0.{q(v['distinct_column'])})"
            extra = f" AND a0.{q(v['filter']['column'])}=?" if v['filter'] else ''
            sql = f"SELECT {measure} FROM {q(v['source'])} a0 {joins} WHERE {end}=?"+extra
            counts = [(x,db.execute(sql,[x]+([v['filter']['equals']] if v['filter'] else [])).fetchone()[0]) for x in v["group_values"]]
            if g.get("result") == "leaders":
                best = max(n for _,n in counts)
                actual = [x for x,n in counts if n == best]
            else:
                actual = next(n for x,n in counts if x == g["group"])
        checks.append({"goal_id":g["id"],"sql":sql,"actual":actual,"expected":expected[g["id"]],"pass":actual==expected[g["id"]]})
    db.close()
    if not all(c["pass"] for c in checks):
        raise AssertionError(checks)
    return {"pass":True,"checks":checks,"method":"Independent SQLite execution of goals, no Python query statistic loop reused"}


def intervention(rows, schemas, profile):
    p = profile["intervention"]
    indexes = [i for i,r in enumerate(rows[p["table"]]) if all(r[k] == v for k,v in p["row_key"].items())]
    if len(indexes) != 1:
        raise ValueError("Intervention must identify one source row")
    ri = indexes[0]
    return {"table":p["table"],"column":p["column"],"row_index":ri,"row_key":p["row_key"],
            "before":rows[p["table"]][ri][p["column"]],"after":p["value"],"operator":"foreign_key_reassign"}


def answer_contract(stage):
    value = {"stage":stage,"answers":{"<每个目标ID>":{"value":"整数或对象编号列表","evidence":"简述依据，不要只说计算得到"}}}
    if stage == 2:
        value["answers"]["<每个目标ID>"]["changed"] = "相对原始资料真实答案是否变化，JSON布尔值"
        value["source_edits"] = [{"table":"源表名称","row_key":{"主键列":"键值"},"column":"待改列","value":"新值"}]
    return value


def stage1_instruction(profile):
    lines = [profile["title"],"",profile["scenario"],"", "业务目标（所有目标均须回答）："]
    lines += [f"{g['id']}. {g['question']}" for g in profile["goals"]]
    lines += ["","口径说明："] + profile["business_assumptions"]
    lines += ["", "附件只有原始记录。自行分析资料关系和计算方式，不能将资料中未出现的信息当作事实。请提交一次 JSON 对象；用 goal ID 作 answers 的键。整数用 JSON 整数，对象名单用不重复的整数数组；evidence 使用自然语言说明。不要提供额外文件操作。",json.dumps(answer_contract(1),ensure_ascii=False,indent=2)]
    return "\n".join(lines)+"\n"


def format_source(path):
    """Small adapter over the existing exporter: readable layout and explicit nullable cells."""
    wb=load_workbook(path,data_only=False)
    for ws in wb:
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for c in ws[1]:c.font=Font(bold=True)
        for column in ws.columns:
            for c in column:
                # Keep logical NULL cells explicit for strict per-cell native structure checks.
                if c.value is None:c.number_format='@'
            width=max(len(str(c.value)) if c.value is not None else 0 for c in column)+2
            ws.column_dimensions[column[0].column_letter].width=min(38,max(12,width))
    wb.save(path)


def build(output, case=DEFAULT_CASE, profile_path=DEFAULT_PROFILE):
    output = Path(output).resolve()
    ds = load(case)
    profile = json.loads(Path(profile_path).read_text())
    analysis = analyze(ds)
    before, deps = goal_truth(ds.rows, profile, analysis["views"])
    c = intervention(ds.rows, ds.schemas, profile)
    after_rows = apply(ds.rows, ds.schemas, c)
    after, after_deps = goal_truth(after_rows, profile, analysis["views"])
    changed = {g:before[g] != after[g] for g in before}
    if sum(changed.values()) < 3 or sum(not v for v in changed.values()) < 2:
        raise ValueError("Task must include at least 3 changed and 2 unchanged goals")
    stage1, private = output/"stage1", output/"private"
    stage1.mkdir(parents=True,exist_ok=True)
    # Reuse the existing source-only table exporter. No new narrative generator.
    plain_xlsx(case,stage1/"source.xlsx")
    format_source(stage1/'source.xlsx')
    normalize_generated_workbook(stage1/"source.xlsx")
    (stage1/"instruction.md").write_text(stage1_instruction(profile))
    save(stage1/"goals.json",public_goals(profile))
    save(private/"profile.json",profile)
    save(private/"before_data.json",ds.rows)
    save(private/"after_data.json",after_rows)
    save(private/"schemas.json",ds.schemas)
    save(private/"intervention.json",c)
    save(private/"oracle.json",{"goals":public_goals(profile),"before":before,"after":after,"changed":changed})
    save(private/"dependencies.json",deps)
    save(private/"input_manifest.json",ds.provenance)
    save(private/"upstream_reuse.json",provenance())
    reuse_files=[DOC2DB_REPO/'data_construction/src/utils/input_loader.py',DOC2DB_REPO/'data_construction/src/models.py']
    save(private/'reuse_inventory.json',{
        'doc2db':{'repository':'https://github.com/SetonLiang/Doc2DB-Bench',
            'commit':subprocess.check_output(['git','-C',str(DOC2DB_REPO),'rev-parse','HEAD'],text=True).strip(),
            'files':[{'path':str(p),'sha256':digest(p)} for p in reuse_files],
            'actual_calls':['load_database_from_directory','Pydantic schema/table models'],
            'not_executed':['original Refiner/Writer/LLM document generation pipeline']},
        'local_reuse':['schema_cf.engine.analyze/apply/facts/values','schema_cf.independent.verify',
            'schema_cf.demo.plain_xlsx','schema_cf.upstream_office.patcher.patch_xlsx',
            'medical_cf.oracle.structural_snapshot','alignment_probe.codex_transport.relay_class/configured_route'],
        'new':['two-stage goal profile/projector','release/submission locking','per-goal evaluation and conditional preservation',
               'same-thread two-turn model adapter and audit','oracle controls and reproducible runner'],
        'business_status':'Explicit hypothetical source-reference correction; no unprovided capacity/eligibility constraint is asserted.',
        'provenance_note':'OfficeEditBench helpers are CC BY 4.0 as recorded separately; retain source dataset attribution. No complete Doc2DB redistribution license was established in this experiment.'})
    save(private/"independent_before.json",independent_goals(ds.rows,ds.schemas,profile,deps,before))
    save(private/"independent_after.json",independent_goals(after_rows,ds.schemas,profile,after_deps,after))
    for label,rows in [("before",ds.rows),("after",after_rows)]:
        computed = facts(rows,ds.schemas,analysis["views"])
        save(private/f"query_check_{label}.json",verify(rows,ds.schemas,analysis["views"],computed))
    response1={"stage":1,"answers":{g:{"value":v,"evidence":"评测端参考；原始数据与独立SQL核对"} for g,v in before.items()}}
    response2={"stage":2,"answers":{g:{"value":v,"changed":changed[g],"evidence":"评测端参考；反事实数据与独立SQL核对"} for g,v in after.items()},
               "source_edits":[dict(profile["intervention"])]}
    save(private/"reference_stage1.json",response1)
    save(private/"reference_stage2.json",response2)
    hidden2 = profile["stage2_instruction"]+"\n\n业务目标完全沿用第一阶段，不增加或删除目标。提交 JSON；每个目标须填写 value、changed 和 evidence。source_edits 只列需要改变的原始记录，不修改其他源数据。\n"+json.dumps(answer_contract(2),ensure_ascii=False,indent=2)+"\n"
    (private/"stage2_instruction.md").write_text(hidden2)
    audit = audit_public(output)
    save(private/"public_audit.json",audit)
    summary={"goals":len(before),"changed_goals":sum(changed.values()),"unchanged_goals":sum(not v for v in changed.values()),"pass":audit["pass"],
             "scope":"Source-only two-stage analysis; natural report prose is optional and not semantically scored; stage2 source edits are evaluated natively."}
    save(output/"build_summary.json",summary)
    if not audit["pass"] or load(case).provenance != ds.provenance:
        raise AssertionError("Public audit or input preservation failed")
    return summary


def audit_public(package):
    package = Path(package)
    public = package/"stage1"
    actual = {str(p.relative_to(public)) for p in public.rglob('*') if p.is_file()}
    checks=[{"id":"only_allowlisted_files","pass":actual=={"source.xlsx","goals.json","instruction.md"}}]
    rows=json.loads((package/"private/before_data.json").read_text())
    schemas=json.loads((package/"private/schemas.json").read_text())
    wb=load_workbook(public/"source.xlsx",data_only=False)
    checks.append({"id":"only_source_sheets","pass":set(wb.sheetnames)==set(rows)})
    for table,records in rows.items():
        cols=[c['name'] for c in schemas[table]['columns']]
        ws=wb[table]
        observed=[dict(zip(cols,r)) for r in ws.iter_rows(min_row=2,values_only=True)]
        checks.append({"id":"raw_records:"+table,"pass":observed==records and ws.max_column==len(cols)})
    private_keys={"before","after","changed","plan","dependencies","intervention","stage2_instruction"}
    goals=json.loads((public/"goals.json").read_text())
    checks.append({"id":"public_goals_no_private_fields","pass":all(set(g)=={"id","question","type"} and not set(g)&private_keys for g in goals)})
    for part in (public/"instruction.md",public/"goals.json"):
        text=part.read_text()
        checks.append({"id":"no_reveal:"+part.name,"pass":not any(x in text for x in ["Metrics","JOIN ","COUNT(","Stadium_ID","World Master","Nou Mestalla","迁到","调整为","changed_goals",'"expected"'])})
    return {"pass":all(c['pass'] for c in checks),"checks":checks,"policy":"Exact raw data equality and public-file whitelist; counts themselves may of course be derived from the supplied data."}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=Path(__file__).with_name('artifacts')/'package')
    p.add_argument('--case',type=Path,default=DEFAULT_CASE)
    p.add_argument('--profile',type=Path,default=DEFAULT_PROFILE)
    a=p.parse_args();print(json.dumps(build(a.out,a.case,a.profile),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
