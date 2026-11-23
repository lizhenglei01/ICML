from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from medical_cf.upstream import REPO
from medical_cf.oracle import evaluate
from .input import load, validate
from .engine import analyze, apply, propose, select, facts, values
from .independent import verify
from .demo import plain_xlsx
from .run import build


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("schema-cf")
    results = {}
    for name in ("medical", "sports"):
        results[name] = build(REPO/"dataset"/name/"case1", root/name, task_count=2, max_candidates=100)
    plain_xlsx(REPO/"dataset/sports/case1", root/"plain.xlsx")
    results["plain"] = build(root/"plain.xlsx", root/"plain", task_count=2, max_candidates=60)
    return root, results


def test_two_schemas_and_plain_xlsx(built):
    _, r = built
    assert r["medical"]["tables"] == 6 and r["sports"]["tables"] == 4
    assert all(v["pass"] and len(v["tasks"]) == 2 for v in r.values())
    assert r["sports"]["tasks"][0]["max_join_hops"] == 2
    assert {t["operator"] for t in r["sports"]["tasks"]} == {"foreign_key_reassign", "category_replace"}


@pytest.mark.parametrize("variant", ["correct", "excel_only", "stale_semantics", "wrong_number", "unrelated_content", "broken_formula", "broken_structure"])
def test_generated_oracle_faults(built, variant):
    root, results = built
    for name, result in results.items():
        for task in result["tasks"]:
            report = json.loads((root/name/"tasks"/task["id"]/"private/reports"/(variant+".json")).read_text())
            assert report["expectation_met"]
            if variant == "correct":
                assert report["pass"]
            elif variant != "stale_semantics":
                assert not report["pass"]


def test_unknown_business_not_auto_approved(built):
    root, results = built
    for name, result in results.items():
        for t in result["tasks"]:
            status = json.loads((root/name/"tasks"/t["id"]/"status.json").read_text())
            assert status["status"] == "draft_needs_business_review"
            assert status["numerical_truth"] == "independently_verified"


def test_inferred_links_not_silently_used(built):
    root, _ = built
    a = json.loads((root/"plain/analysis.json").read_text())
    assert a["inferred_relationships_not_used"]
    assert all(not v["path"] for v in a["views"])
    assert all(s["schema"]["foreign_keys"] == {} for s in a["tables"].values())


def test_no_private_ids_in_public_native_files(built):
    root, results = built
    for name, result in results.items():
        for t in result["tasks"]:
            public = root/name/"tasks"/t["id"]/"public"
            assert {str(p.relative_to(public)) for p in public.rglob("*") if p.is_file()} == {"instruction.md", "input/data.xlsx", "input/report.docx"}
            for file in (public/"input").iterdir():
                with ZipFile(file) as z:
                    for member in z.namelist():
                        data = z.read(member)
                        assert b'"dependencies"' not in data
                        import re
                        assert not re.search(rb"v_[0-9a-f]{14}", data)


def test_sql_catches_wrong_python_truth():
    ds = load(REPO/"dataset/sports/case1")
    a = analyze(ds)
    computed = facts(ds.rows, ds.schemas, a["views"])
    fid = next(f for f, n in computed.items() if n["rule"] == "count")
    computed[fid]["value"] += 999
    with pytest.raises(AssertionError):
        verify(ds.rows, ds.schemas, a["views"], computed)


def test_primary_key_constraint_filters_candidates(tmp_path):
    # A relation-table key collision must be rejected, rather than overwritten.
    schema = [
      {"name":"Groups", "fields":[{"name":"id","type":"INTEGER","constraints":{"primary_key":True}}]},
      {"name":"Links", "fields":[{"name":"actor","type":"INTEGER","constraints":{"primary_key":True}},
       {"name":"group_id","type":"INTEGER","constraints":{"primary_key":True,"foreign_key":{"table":"Groups","column":"id"}}}]}]
    (tmp_path/"tables").mkdir()
    (tmp_path/"schema.json").write_text(json.dumps(schema))
    (tmp_path/"tables/Groups.json").write_text('[{"id":1},{"id":2}]')
    (tmp_path/"tables/Links.json").write_text('[{"actor":7,"group_id":1},{"actor":7,"group_id":2}]')
    ds = load(tmp_path)
    p = propose(ds, analyze(ds))
    assert p["candidates"] and all(c["status"] == "rejected_constraint" for c in p["candidates"])


def test_schema_renaming_needs_no_domain_code(tmp_path):
    ds = load(REPO/"dataset/sports/case1")
    a = analyze(ds)
    first = select(propose(ds, a, 80), 1)[0]
    changed = apply(ds.rows, ds.schemas, first)
    assert verify(changed, ds.schemas, a["views"], facts(changed, ds.schemas, a["views"]))["pass"]
    # Rewrite every table/column name, including FK references, while preserving only structure.
    rename_t = {t: f"Entity{i}" for i,t in enumerate(ds.rows)}
    rename_c = {t:{c["name"]:f"field{j}" for j,c in enumerate(ds.schemas[t]["columns"])} for t in ds.rows}
    rows, schemas = {}, {}
    for t, records in ds.rows.items():
        nt = rename_t[t]
        rows[nt] = [{rename_c[t][c]:v for c,v in r.items()} for r in records]
        s = deepcopy(ds.schemas[t])
        s["table_name"] = nt
        s["columns"] = [dict(c, name=rename_c[t][c["name"]]) for c in s["columns"]]
        s["primary_key"] = [rename_c[t][c] for c in s["primary_key"]]
        s["foreign_keys"] = {rename_c[t][c]:rename_t[v.split('.')[0]]+'.'+rename_c[v.split('.')[0]][v.split('.')[1]] for c,v in s["foreign_keys"].items()}
        schemas[nt] = s
    ds.rows, ds.schemas = rows, schemas
    na = analyze(ds)
    c = select(propose(ds, na, 80), 1)[0]
    out = apply(rows, schemas, c)
    assert verify(out, schemas, na["views"], facts(out, schemas, na["views"]))["pass"]
    assert all(t.startswith("Entity") for t in na["tables"])


def test_zero_group_retained_after_last_member_moves(tmp_path):
    p = tmp_path/"values.json"
    p.write_text('[{"id":1,"category":"A"},{"id":2,"category":"B"},{"id":3,"category":"B"}]')
    ds = load(p)
    a = analyze(ds)
    p = propose(ds, a)
    c = next(c for c in p["candidates"] if c["row_index"] == 0 and c["after"] == "B")
    out = apply(ds.rows, ds.schemas, c)
    stats = values(out, a["views"])
    assert any(f.endswith('/group/"A"/count') and v == 0 for f,v in stats.items())
    assert verify(out,ds.schemas,a["views"],facts(out,ds.schemas,a["views"]))["pass"]


def test_observed_boolean_domain_allows_zero_count_state(tmp_path):
    p = tmp_path/"flags.json"
    p.write_text('[{"id":1,"flag":true},{"id":2,"flag":true}]')
    ds = load(p)
    a = analyze(ds)
    proposals = propose(ds,a)
    assert any(c["status"] == "valid_structural_candidate" for c in proposals["candidates"])


def test_numeric_time_strings_not_categories(tmp_path):
    p = tmp_path/"times.json"
    p.write_text('[{"id":1,"time":"1:20.00"},{"id":2,"time":"1:21.00"},{"id":3,"time":"1:20.00"}]')
    ds = load(p)
    assert not analyze(ds)["mutable_fields"]


def test_upstream_editors_actually_called(built):
    root, r = built
    task = r["sports"]["tasks"][0]
    reuse = json.loads((root/"sports/tasks"/task["id"]/"private/upstream_calls.json").read_text())
    assert reuse["calls"]["patch_xlsx_operations"] > 0 and reuse["calls"]["patch_docx_operations"] > 0
    assert reuse["diff"]["pass"]


def test_standalone_existing_oracle_evaluator(built):
    root, r = built
    task = root/"sports/tasks"/r["sports"]["tasks"][0]["id"]
    assert evaluate(task/"private/reference",task/"private/oracle.json")["pass"]


def test_rejects_formula_table_input(tmp_path):
    wb=Workbook();ws=wb.active;ws.append(["id","x"]);ws.append([1,"=1+1"]);p=tmp_path/"formula.xlsx";wb.save(p)
    with pytest.raises(ValueError,match="Formula"):
        load(p)
