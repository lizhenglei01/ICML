"""Independent SQL execution of saved plans; no calls to the Python statistic engine."""
from __future__ import annotations

import sqlite3

from .input import token


def q(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def verify(rows, schemas, views, computed):
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    for table, s in schemas.items():
        columns = [q(c["name"]) + (" NOT NULL" if not c["nullable"] else "") for c in s["columns"]]
        if s["primary_key"]:
            columns.append("PRIMARY KEY (" + ",".join(q(c) for c in s["primary_key"]) + ")")
        for column, target in s["foreign_keys"].items():
            t, key = target.split(".")
            columns.append(f"FOREIGN KEY ({q(column)}) REFERENCES {q(t)}({q(key)}) DEFERRABLE INITIALLY DEFERRED")
        db.execute(f"CREATE TABLE {q(table)} (" + ",".join(columns) + ")")
    db.execute("BEGIN")
    for table, records in rows.items():
        cols = [c["name"] for c in schemas[table]["columns"]]
        db.executemany(f"INSERT INTO {q(table)} (" + ",".join(q(c) for c in cols) + ") VALUES (" + ",".join("?" for _ in cols) + ")",
                       [[r[c] for c in cols] for r in records])
    db.commit()
    checks = [{"id": "sql:foreign_keys", "pass": not db.execute("PRAGMA foreign_key_check").fetchall()}]
    sql_queries = []
    for view in views:
        source = q(view["source"])
        aggregate = "COUNT(*)" if view["aggregate"] == "count" else f"COUNT(DISTINCT a0.{q(view['distinct_column'])})"
        joins = []
        for i, edge in enumerate(view["path"]):
            joins.append(f"JOIN {q(edge['target'])} a{i+1} ON a{i}.{q(edge['column'])}=a{i+1}.{q(edge['target_column'])}")
        if view["path"]:
            grouping = f"a{len(view['path'])}.{q(view['path'][-1]['target_column'])}"
        else:
            grouping = f"a0.{q(view['group_column'])}"
        condition = ""
        args = []
        if view["filter"]:
            condition = f" AND a0.{q(view['filter']['column'])}=?"
            args.append(view["filter"]["equals"])
        sql = f"SELECT {aggregate} FROM {source} a0 " + " ".join(joins) + f" WHERE {grouping}=?" + condition
        sql_queries.append({"view_id": view["id"], "sql": sql, "filter_parameters": args})
        counts = [(g, db.execute(sql, [g] + args).fetchone()[0]) for g in view["group_values"]]
        # Window ranking is executed in SQLite, not the generator's comparison loop.
        db.execute("CREATE TEMP TABLE cf_count_results (group_index INTEGER, n INTEGER)")
        db.executemany("INSERT INTO cf_count_results VALUES (?,?)", [(i, n) for i, (_, n) in enumerate(counts)])
        ranks = dict(db.execute("SELECT group_index,RANK() OVER(ORDER BY n DESC) FROM cf_count_results"))
        leaders = []
        for i, (g, n) in enumerate(counts):
            prefix = view["id"] + "/group/" + token(g)
            expected = {"count": n, "rank": ranks[i], "leader": ranks[i] == 1, "present": n > 0}
            if ranks[i] == 1:
                leaders.append(g)
            for metric, val in expected.items():
                fid = prefix + "/" + metric
                actual = computed[fid]["value"]
                checks.append({"id": "sql:" + fid, "expected": val, "actual": actual, "pass": actual == val})
        fid = view["id"] + "/leaders"
        checks.append({"id": "sql:" + fid, "expected": leaders, "actual": computed[fid]["value"], "pass": computed[fid]["value"] == leaders})
        db.execute("DROP TABLE cf_count_results")
    db.close()
    result = {"pass": all(c["pass"] for c in checks), "checks": checks, "queries": sql_queries,
              "method": "Independent SQL JOIN/COUNT/COUNT DISTINCT + window RANK, with structural FK checks"}
    if not result["pass"]:
        raise AssertionError([c for c in checks if not c["pass"]])
    return result
