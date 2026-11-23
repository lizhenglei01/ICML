"""Run identical generator on two upstream schemas and a metadata-free XLSX."""
import argparse
import json
from pathlib import Path

from openpyxl import Workbook

from medical_cf.run import save
from medical_cf.upstream import REPO
from .input import load
from .run import build


def plain_xlsx(case, output):
    """Only table values and headers; no schema, private bindings or truth embedded."""
    dataset = load(case)
    wb = Workbook()
    wb.remove(wb.active)
    for name, records in dataset.rows.items():
        ws = wb.create_sheet(name)
        columns = [c["name"] for c in dataset.schemas[name]["columns"]]
        ws.append(columns)
        for row in records:
            ws.append([row[c] for c in columns])
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path(__file__).with_name("artifacts"))
    args = p.parse_args()
    root = args.out.resolve()
    inputs = {"medical": REPO/"dataset/medical/case1", "sports": REPO/"dataset/sports/case1"}
    plain_xlsx(inputs["sports"], root/"inputs/sports_tables_only.xlsx")
    inputs["plain_xlsx"] = root/"inputs/sports_tables_only.xlsx"
    results = {}
    for name, source in inputs.items():
        print("BUILD", name, source, flush=True)
        r = build(source, root/name, task_count=3, max_candidates=120)
        results[name] = {"tables": r["tables"], "rows": r["rows"], "views": r["views"],
                         "candidate_statuses": r["candidate_statuses"], "pass": r["pass"],
                         "tasks": [{k:v for k,v in t.items() if k != "tests"} for t in r["tasks"]]}
        for t in r["tasks"]:
            print(t["id"], t["table"], t["column"], repr(t["before"]), "->", repr(t["after"]), "joins",t["max_join_hops"], "reference PASS", flush=True)
            for test, report in t["tests"].items():
                print(" ",test, "PASS" if report["pass"] else "FAIL (expected)", "failed checks", ",".join(report["failed_checks"][:5]) or "none", flush=True)
    save(root/"cross_input_results.json", results)
    print("Detailed results:",root/"cross_input_results.json",flush=True)


if __name__ == "__main__":
    main()
