"""Independent relational arithmetic using SQLite, never importing facts.compute."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import subprocess
import tempfile

from docx import Document
from openpyxl import load_workbook


def verify(rows, schemas, facts):
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    # Build first, then insert in a transaction with deferred FK checking.
    for table, s in schemas.items():
        cols = [f'"{c["name"]}"' + (" NOT NULL" if not c["nullable"] else "") for c in s["columns"]]
        if s["primary_key"]:
            cols.append("PRIMARY KEY (" + ",".join('"'+c+'"' for c in s["primary_key"]) + ")")
        for col, target in s["foreign_keys"].items():
            t, c = target.split(".")
            cols.append(f'FOREIGN KEY ("{col}") REFERENCES "{t}"("{c}") DEFERRABLE INITIALLY DEFERRED')
        con.execute(f'CREATE TABLE "{table}" (' + ",".join(cols) + ")")
    con.execute("BEGIN")
    for table, s in schemas.items():
        cols = [c["name"] for c in s["columns"]]
        names = ",".join('"'+c+'"' for c in cols)
        slots = ",".join("?" for _ in cols)
        con.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({slots})', [[r[c] for c in cols] for r in rows[table]])
    con.commit()
    checks = [{"id": "sql:foreign_keys", "pass": not con.execute("PRAGMA foreign_key_check").fetchall()}]
    invalid = con.execute('SELECT p.EmployeeID FROM Physician p LEFT JOIN Affiliated_With a ON a.Physician=p.EmployeeID GROUP BY p.EmployeeID HAVING SUM(CASE WHEN a.PrimaryAffiliation=1 THEN 1 ELSE 0 END)<>1').fetchall()
    checks.append({"id": "sql:one_primary_per_physician", "pass": not invalid})
    con.execute('CREATE VIEW counts AS SELECT d.DepartmentID AS id, COUNT(a.Physician) AS n FROM Department d LEFT JOIN Affiliated_With a ON a.Department=d.DepartmentID AND a.PrimaryAffiliation=1 GROUP BY d.DepartmentID')
    expected = {}
    for pid, dept in con.execute('SELECT Physician,Department FROM Affiliated_With WHERE PrimaryAffiliation=1 ORDER BY Physician'):
        expected[f"physician:{pid}:primary"] = dept
    count_map = {}
    for dept, count, rank in con.execute('SELECT id,n,RANK() OVER(ORDER BY n DESC) FROM counts ORDER BY id'):
        count_map[dept] = count
        expected[f"department:{dept}:count"] = count
        expected[f"department:{dept}:rank"] = rank
        expected[f"department:{dept}:leader"] = rank == 1
    leaders = [r[0] for r in con.execute('SELECT id FROM counts WHERE n=(SELECT MAX(n) FROM counts) ORDER BY id')]
    expected["departments:leaders"] = leaders
    for fid, value in expected.items():
        actual = facts[fid]["value"]
        checks.append({"id": "sql:" + fid, "expected": value, "actual": actual, "pass": actual == value})
    for table, rs in rows.items():
        for i, r in enumerate(rs):
            for col, val in r.items():
                fid = f"db:{table}:{i}:{col}"
                checks.append({"id": "source:" + fid, "expected": val, "actual": facts[fid]["value"], "pass": facts[fid]["value"] == val})
    con.close()
    result = {"pass": all(c["pass"] for c in checks), "counts": count_map, "leaders": leaders,
              "checks": checks, "method": "SQLite FK constraints + LEFT JOIN/COUNT + window RANK; no generator arithmetic imported"}
    if not result["pass"]:
        raise AssertionError(result)
    return result


def verify_files(directory, rows, sql_result):
    """Read final native artifacts independently of bindings, renderer and oracle."""
    directory = Path(directory)
    wb = load_workbook(directory / "hospital.xlsx", data_only=False)
    text = [p.text for p in Document(directory / "review.docx").paragraphs]
    checks = []

    def equal(label, actual, expected):
        checks.append({"id": label, "actual": actual, "expected": expected, "pass": actual == expected})

    departments = {r["DepartmentID"]: r["Name"] for r in rows["Department"]}
    doctors = {r["EmployeeID"]: r["Name"] for r in rows["Physician"]}
    counts = sql_result["counts"]
    for r in rows["Affiliated_With"]:
        if r["PrimaryAffiliation"] == 1:
            sentence = f"{doctors[r['Physician']]} has primary department {departments[r['Department']]} ."
            # Check content against independent source join, not stored bindings.
            expected = sentence.replace(" .", ".")
            equal(f"native:physician:{r['Physician']}", expected in text, True)
    sheet_rows = {r[0]: r for r in wb["Summary"].iter_rows(min_row=2, max_row=len(departments)+1, values_only=True)}
    for dept, name in departments.items():
        count = counts[dept]
        rank = sorted(counts.values(), reverse=True).index(count) + 1  # independent rank formulation
        equal(f"native:count:{dept}", sheet_rows[dept][2], count)
        equal(f"native:rank:{dept}", sheet_rows[dept][3], rank)
        equal(f"native:leader:{dept}", sheet_rows[dept][4], dept in sql_result["leaders"])
        equal(f"native:word_count:{dept}", f"{name} has {count} primary physicians." in text, True)
        equal(f"native:word_rank:{dept}", f"{name} is ranked {rank} by primary physician count." in text, True)
        verb = "is among" if dept in sql_result["leaders"] else "is not among"
        equal(f"native:word_predicate:{dept}", f"{name} {verb} the departments with the most primary physicians." in text, True)
    leaders = ", ".join(departments[d] for d in sql_result["leaders"])
    status = " (tied)" if len(sql_result["leaders"]) > 1 else " (sole leader)"
    equal("native:word_leader_set", f"Departments with the most primary physicians: {leaders}{status}." in text, True)
    equal("native:total_formula", wb["Summary"]["G2"].value, f"=SUM(C2:C{len(departments)+1})")
    # Native raw worksheets must reproduce every (including protected) source record.
    for table, records in rows.items():
        columns = [c.value for c in wb[table][1]]
        actual = [{c: wb[table].cell(i+2, j+1).value for j, c in enumerate(columns)} for i in range(len(records))]
        equal("native:raw:" + table, actual, records)
    if not all(c["pass"] for c in checks):
        raise AssertionError([c for c in checks if not c["pass"]])
    return {"pass": True, "method": "Read native XLSX/DOCX; use SQL/source results, not binding/Oracle specifications", "checks": checks}


def verify_recalculation(directory, expected_total):
    """Independent LibreOffice formula execution, on disposable copies only."""
    with tempfile.TemporaryDirectory(prefix="medical-cf-lo-") as temp:
        temp = Path(temp)
        result = subprocess.run([
            "libreoffice", "-env:UserInstallation=" + (temp / "profile").as_uri(),
            "--headless", "--convert-to", "xlsx", "--outdir", str(temp),
            str(Path(directory).resolve() / "hospital.xlsx")
        ], capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not (temp / "hospital.xlsx").exists():
            raise AssertionError("LibreOffice recalculation failed: " + result.stdout + result.stderr)
        actual = load_workbook(temp / "hospital.xlsx", data_only=True)["Summary"]["G2"].value
        if actual != expected_total:
            raise AssertionError((actual, expected_total))
        return {"pass": True, "actual": actual, "expected": expected_total,
                "method": "LibreOffice headless on temporary copy; output not substituted for reference",
                "stdout": result.stdout, "stderr": result.stderr}
