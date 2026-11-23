"""Schema-guided operators and explicit query plans, with no domain/table-name branches."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .input import token, sort_values, validate


def uid(value):
    return hashlib.sha256(token(value).encode()).hexdigest()[:14]


def categorical_columns(rows, schemas, table):
    schema = schemas[table]
    for c in schema["columns"]:
        col = c["name"]
        if col in schema["primary_key"] or col in schema["foreign_keys"]:
            continue
        vals = sort_values(r[col] for r in rows[table] if r[col] is not None)
        boolean = c["dtype"].upper() in ("BOOLEAN", "BOOL") or (vals and all(type(v) is bool for v in vals))
        if boolean and vals and all(v in (0, 1, True, False) for v in vals):
            if all(type(v) is bool for v in vals):
                vals = [False, True]
            else:
                vals = [0, 1]
            yield col, vals, "boolean_domain"
        elif 2 <= len(vals) <= 12 and len(vals) < len(rows[table]) and all(type(v) is str for v in vals):
            if "DATE" not in c["dtype"].upper() and "TIME" not in c["dtype"].upper():
                if not all(re.fullmatch(r"[\d\s:./+%\-]+", v) for v in vals):
                    yield col, vals, "observed_repeated_categories"


def analyze(dataset, max_views=48):
    rows, schemas = dataset.rows, dataset.schemas
    validate(rows, schemas)
    fields, views = [], []
    diagnostics = []

    def add_view(spec):
        spec = dict(spec)
        if "group_values" in spec:
            pass
        elif spec["path"]:
            last = spec["path"][-1]
            spec["group_values"] = sort_values(r[last["target_column"]] for r in rows[last["target"]])
        else:
            spec["group_values"] = sort_values(r[spec["group_column"]] for r in rows[spec["source"]] if r[spec["group_column"]] is not None)
        spec["id"] = "v_" + uid(spec)
        if len(views) < max_views and not any(v["id"] == spec["id"] for v in views):
            views.append(spec)

    edges = {}
    for table, schema in schemas.items():
        edges[table] = []
        for col, target in sorted(schema["foreign_keys"].items()):
            t, key = target.split(".")
            keys = [r[key] for r in rows[t]]
            if len({token(v) for v in keys}) != len(keys) or any(v is None for v in keys):
                diagnostics.append({"table": table, "column": col, "skipped": "referenced column is not unique/non-null in input"})
                continue
            edge = {"source": table, "column": col, "target": t, "target_column": key}
            edges[table].append(edge)
            # Entity identities are not reassigned. Composite relation-table keys may change if still unique.
            if schema["primary_key"] != [col]:
                fields.append({"table": table, "column": col, "operator": "foreign_key_reassign",
                               "domain": sort_values(keys), "evidence": "declared_foreign_key", "target": target})
        for col, values, evidence in categorical_columns(rows, schemas, table):
            fields.append({"table": table, "column": col, "operator": "category_replace", "domain": values, "evidence": evidence})

    # Derive one- and two-hop plans from declared FK paths. Group zero-reference targets too.
    for source in sorted(schemas):
        paths = [[edge] for edge in edges[source]]
        for first in edges[source]:
            for second in edges[first["target"]]:
                if second["target"] != source:
                    paths.append([first, second])
        for path in paths:
            base = {"source": source, "path": path, "group_column": None, "filter": None,
                    "aggregate": "count", "distinct_column": None}
            add_view(base)
            # Distinct relation endpoints use known FK columns, not guessed IDs or arbitrary numeric fields.
            for other in sorted(schemas[source]["foreign_keys"]):
                if other != path[0]["column"]:
                    add_view(dict(base, aggregate="count_distinct", distinct_column=other))
            for col, vals, evidence in categorical_columns(rows, schemas, source):
                if evidence == "boolean_domain":
                    for val in vals:
                        add_view(dict(base, filter={"column": col, "equals": val}))
    for field in fields:
        if field["operator"] == "category_replace":
            add_view({"source": field["table"], "path": [], "group_column": field["column"],
                      "filter": None, "aggregate": "count", "distinct_column": None, "group_values": field["domain"]})
    return {"tables": {t: {"rows": len(rows[t]), "schema": schemas[t]} for t in sorted(rows)},
            "mutable_fields": fields, "views": views, "diagnostics": diagnostics,
            "inferred_relationships_not_used": dataset.inferred_relations,
            "semantics": "Explicit row-reference/category substitutions and defined SQL aggregates. Business feasibility is not inferred.",
            "limits": {"max_join_hops": 2, "max_views": max_views,
                       "unsupported": ["automatic sums of arbitrary numeric IDs", "implicit business invariants", "insert/delete", "composite foreign keys"]}}


def groups(rows, view):
    if "group_values" in view:
        return view["group_values"]
    if view["path"]:
        edge = view["path"][-1]
        return sort_values(r[edge["target_column"]] for r in rows[edge["target"]])
    return sort_values(r[view["group_column"]] for r in rows[view["source"]] if r[view["group_column"]] is not None)


def values(rows, views):
    result = {}
    for view in views:
        group_values = groups(rows, view)
        buckets = {token(g): [] for g in group_values}
        for row in rows[view["source"]]:
            predicate = view["filter"]
            if predicate and row[predicate["column"]] != predicate["equals"]:
                continue
            current = row
            group = row.get(view["group_column"]) if not view["path"] else None
            for edge in view["path"]:
                reference = current[edge["column"]]
                current = next((r for r in rows[edge["target"]] if reference is not None and r[edge["target_column"]] == reference), None)
                if current is None:
                    break
                group = current[edge["target_column"]]
            if current is None or group is None:
                continue
            buckets[token(group)].append(row)
        counts = {}
        for g, records in buckets.items():
            if view["aggregate"] == "count_distinct":
                col = view["distinct_column"]
                counts[g] = len({token(r[col]) for r in records if r[col] is not None})
            else:
                counts[g] = len(records)
        for g, count in counts.items():
            prefix = view["id"] + "/group/" + g
            rank = 1 + sum(c > count for c in counts.values())
            result[prefix + "/count"] = count
            result[prefix + "/rank"] = rank
            result[prefix + "/leader"] = rank == 1
            result[prefix + "/present"] = count > 0
        result[view["id"] + "/leaders"] = [g for g in group_values if counts[token(g)] == max(counts.values())] if counts else []
    return result


def raw_id(table, index, col):
    return "raw/" + token([table, index, col])


def facts(rows, schemas, views):
    result = {}
    for table in sorted(rows):
        for i, row in enumerate(rows[table]):
            for col, value in row.items():
                fid = raw_id(table, i, col)
                result[fid] = {"id": fid, "value": value, "rule": "source_cell", "dependencies": [],
                    "sources": [{"table": table, "row_index": i, "column": col,
                                 "primary_key": {c: row[c] for c in schemas[table]["primary_key"]}}]}
    stats = values(rows, views)
    for view in views:
        vid = view["id"]
        dependencies = [f for f, node in result.items() if node["rule"] == "source_cell" and
                        node["sources"][0]["table"] in {view["source"], *(e["target"] for e in view["path"])}]
        count_ids = [f for f in stats if f.startswith(vid + "/") and f.endswith("/count")]
        for fid, val in stats.items():
            if not fid.startswith(vid + "/"):
                continue
            metric = fid.rsplit("/", 1)[1]
            if metric == "count":
                deps = dependencies
            elif metric == "rank":
                deps = count_ids
            elif metric == "leaders":
                deps = [f for f in stats if f.startswith(vid+"/") and f.endswith("/leader")]
            else:
                deps = [fid.rsplit("/", 1)[0] + ("/rank" if metric == "leader" else "/count")]
            result[fid] = {"id": fid, "value": val, "rule": metric,
                           "view_id": vid, "dependencies": deps, "sources": [], "query_plan": view}
    return result


def apply(rows, schemas, candidate):
    out = deepcopy(rows)
    if out[candidate["table"]][candidate["row_index"]][candidate["column"]] != candidate["before"]:
        raise ValueError("intervention precondition mismatch")
    out[candidate["table"]][candidate["row_index"]][candidate["column"]] = candidate["after"]
    validate(out, schemas)
    return out


def propose(dataset, analysis, max_candidates=240):
    rows, schemas, views = dataset.rows, dataset.schemas, analysis["views"]
    baseline = values(rows, views)
    # Round-robin across fields to avoid spending the entire budget on the first relation.
    per_field = []
    for field in analysis["mutable_fields"]:
        local = []
        for i, row in enumerate(rows[field["table"]]):
            for new in field["domain"]:
                old = row[field["column"]]
                if type(old) is type(new) and old == new:
                    continue
                c = {k: field[k] for k in ("table", "column", "operator", "evidence")}
                c.update(row_index=i, row_key={k: row[k] for k in schemas[field["table"]]["primary_key"]}, before=old, after=new)
                c["id"] = "cf_" + uid(c)
                local.append(c)
        per_field.append(local)
    pool = []
    for offset in range(max((len(p) for p in per_field), default=0)):
        for local in per_field:
            if offset < len(local):
                pool.append(local[offset])
                if len(pool) >= max_candidates:
                    break
        if len(pool) >= max_candidates:
            break
    evaluated = []
    for c in pool:
        c = dict(c)
        try:
            changed_rows = apply(rows, schemas, c)
            after = values(changed_rows, views)
        except ValueError as e:
            c.update(status="rejected_constraint", reason=str(e))
            evaluated.append(c)
            continue
        changed = [f for f in baseline if baseline[f] != after[f]]
        changed_views = {f.split("/")[0] for f in changed}
        semantics = [f for f in changed if f.endswith(("/leader", "/leaders", "/present"))]
        hops = max((len(v["path"]) for v in views if v["id"] in changed_views), default=0)
        if not changed:
            c.update(status="rejected_no_derived_effect", reason="No registered aggregate output changes")
        else:
            c.update(status="valid_structural_candidate", changed_facts=changed,
                     changed_views=len(changed_views), semantic_changes=len(semantics), max_join_hops=hops,
                     score=100*hops + 5*len(semantics) + len(changed),
                     business_status="needs_business_review", numeric_truth_status="deterministic_under_explicit_query_plans")
        evaluated.append(c)
    return {"budget": max_candidates, "enumerated_pool_size": sum(len(p) for p in per_field),
            "evaluated": len(evaluated), "candidates": evaluated,
            "selection_policy": "Prefer deeper affected joins, semantic changes and changed outputs; diversify source fields/operators."}


def select(proposals, count):
    valid = sorted((c for c in proposals["candidates"] if c["status"] == "valid_structural_candidate"), key=lambda c: (-c["score"], c["id"]))
    selected, seen = [], set()
    for operator in dict.fromkeys(c["operator"] for c in valid):
        c = next(c for c in valid if c["operator"] == operator)
        selected.append(c)
        seen.add((c["table"], c["column"], c["operator"]))
        if len(selected) == count:
            return selected
    for c in valid:
        identity = (c["table"], c["column"], c["operator"])
        if identity not in seen:
            selected.append(c)
            seen.add(identity)
        if len(selected) == count:
            return selected
    for c in valid:
        if c not in selected:
            selected.append(c)
        if len(selected) == count:
            break
    return selected
