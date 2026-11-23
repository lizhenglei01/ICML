"""Registered hospital semantics; reusable for valid instances of this schema."""
from __future__ import annotations

from copy import deepcopy

RULES = {
    "primary_department": {"origin": "author-registered business interpretation of Affiliated_With.PrimaryAffiliation",
        "sources": ["schema.json:Affiliated_With foreign keys", "tables/Affiliated_With.json"],
        "condition": "Exactly one primary row per physician; PrimaryAffiliation is 0 or 1.",
        "expression": "Department where Physician=employee_id AND PrimaryAffiliation=1"},
    "primary_count": {"origin": "author-added aggregate, not a pre-existing source statistic",
        "sources": ["Affiliated_With.Physician", "Affiliated_With.Department", "Affiliated_With.PrimaryAffiliation"],
        "condition": "Count primary affiliation only; include departments with zero primary physicians.",
        "expression": "COUNT(physician WHERE primary_department=department_id)"},
    "competition_rank": {"origin": "author-added ranking convention",
        "sources": ["primary_count for every department"],
        "condition": "Competition ranks: tied counts share rank; e.g. 4,4,1 => 1,1,3.",
        "expression": "1 + number_of_departments_with_strictly_greater_count"},
    "leader": {"origin": "author-added semantic predicate",
        "sources": ["competition_rank"], "condition": "Every rank-1 department is a leader, including all ties.",
        "expression": "rank == 1"},
    "leader_set": {"origin": "author-added aggregate statement",
        "sources": ["leader for every department"], "condition": "Nonempty Department table; sort IDs for stable ties.",
        "expression": "sorted({department_id | leader(department_id)})"},
}


def validate(rows, schemas):
    for table, schema in schemas.items():
        seen = set()
        for row in rows[table]:
            for col in schema["columns"]:
                if col["name"] not in row:
                    raise ValueError(f"missing column {table}.{col['name']}")
                if not col["nullable"] and row[col["name"]] is None:
                    raise ValueError(f"NULL {table}.{col['name']}")
            if schema["primary_key"]:
                key = tuple(row[c] for c in schema["primary_key"])
                if None in key or key in seen:
                    raise ValueError(f"duplicate/null PK {table} {key}")
                seen.add(key)
            for col, target in schema["foreign_keys"].items():
                if row[col] is None:
                    continue
                target_table, target_col = target.split(".")
                if not any(r[target_col] == row[col] for r in rows[target_table]):
                    raise ValueError(f"broken FK {table}.{col}={row[col]}")
    if not rows["Department"]:
        raise ValueError("Department must not be empty")
    primary = {}
    for r in rows["Affiliated_With"]:
        if r["PrimaryAffiliation"] not in (0, 1):
            raise ValueError("PrimaryAffiliation must be 0/1")
        if r["PrimaryAffiliation"] == 1:
            primary[r["Physician"]] = primary.get(r["Physician"], 0) + 1
    if any(primary.get(r["EmployeeID"], 0) != 1 for r in rows["Physician"]):
        raise ValueError("each physician must have exactly one primary affiliation")


def intervene(rows, schemas, physician, department):
    result = deepcopy(rows)
    validate(result, schemas)
    if not any(r["DepartmentID"] == department for r in result["Department"]):
        raise ValueError("unknown target department")
    matches = [r for r in result["Affiliated_With"] if r["Physician"] == physician and r["PrimaryAffiliation"] == 1]
    if len(matches) != 1:
        raise ValueError("unknown physician or invalid primary affiliation")
    row = matches[0]
    if row["Department"] == department:
        raise ValueError("no-op intervention")
    if any(r["Physician"] == physician and r["Department"] == department for r in result["Affiliated_With"]):
        raise ValueError("target already has an affiliation: this MVP rejects duplicate-key transfers")
    row["Department"] = department
    validate(result, schemas)
    return result


def raw_id(table, row_index, column):
    return f"db:{table}:{row_index}:{column}"


def compute(rows, schemas):
    validate(rows, schemas)
    facts = {}

    def add(fid, value, rule=None, deps=(), sources=()):
        facts[fid] = {"id": fid, "value": value, "rule": rule,
                      "dependencies": list(deps), "sources": list(sources)}

    for table in sorted(rows):
        for i, row in enumerate(rows[table]):
            for column, value in row.items():
                add(raw_id(table, i, column), value, sources=[{
                    "table": table, "row_index": i, "column": column,
                    "primary_key": {c: row[c] for c in schemas[table]["primary_key"]},
                    "path": f"tables/{table}.json",
                }])
    # Conservative dependencies include all affiliation cells: a different row could become primary.
    affiliation_deps = [f for f in facts if f.startswith("db:Affiliated_With:")]
    primary = {r["Physician"]: r["Department"] for r in rows["Affiliated_With"] if r["PrimaryAffiliation"] == 1}
    for p in sorted(rows["Physician"], key=lambda r: r["EmployeeID"]):
        pid = p["EmployeeID"]
        add(f"physician:{pid}:primary", primary[pid], "primary_department", affiliation_deps)
    primary_ids = [f for f in facts if f.startswith("physician:")]
    departments = sorted(r["DepartmentID"] for r in rows["Department"])
    counts = {d: sum(v == d for v in primary.values()) for d in departments}
    for d in departments:
        add(f"department:{d}:count", counts[d], "primary_count", primary_ids)
    count_ids = [f"department:{d}:count" for d in departments]
    for d in departments:
        rank = 1 + sum(c > counts[d] for c in counts.values())
        add(f"department:{d}:rank", rank, "competition_rank", count_ids)
        add(f"department:{d}:leader", rank == 1, "leader", [f"department:{d}:rank"])
    add("departments:leaders", [d for d in departments if counts[d] == max(counts.values())],
        "leader_set", [f"department:{d}:leader" for d in departments])
    return facts
