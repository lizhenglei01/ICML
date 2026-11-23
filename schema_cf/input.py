"""Load schema-backed cases or plain JSON/CSV/XLSX. Never promote inferred FKs silently."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from openpyxl import load_workbook


@dataclass
class Dataset:
    rows: dict
    schemas: dict
    provenance: dict
    inferred_relations: list


def token(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def sort_values(values):
    return sorted(set(values), key=token)


def _clean_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numeric input is unsupported")
    return value


def _unwrap(payload):
    if isinstance(payload, dict):
        payload = payload.get("rows")
    if not isinstance(payload, list) or any(not isinstance(r, dict) for r in payload):
        raise ValueError("Each table must be an array of row objects (or {rows: [...]})")
    return payload


def _infer(rows):
    schemas = {}
    for table, records in rows.items():
        columns = list(dict.fromkeys(c for row in records for c in row))
        fields = []
        for col in columns:
            values = [r.get(col) for r in records]
            present = [v for v in values if v is not None]
            if present and all(type(v) is bool for v in present):
                dtype = "BOOLEAN"
            elif present and all(type(v) is int for v in present):
                dtype = "INTEGER"
            elif present and all(type(v) in (int, float) for v in present):
                dtype = "REAL"
            else:
                dtype = "TEXT"
            fields.append({"name": col, "dtype": dtype, "nullable": not values or any(v is None for v in values)})
        # A unique, non-null ID-named column is an observed key, not a proven business key.
        keys = [c for c in columns if (c.lower() == "id" or c.lower() == table.lower()+"_id")
                and records and all(r.get(c) is not None for r in records)
                and len({token(r[c]) for r in records}) == len(records)]
        schemas[table] = {"table_name": table, "columns": fields, "primary_key": keys[:1],
                          "foreign_keys": {}, "key_origin": "observed_unique_id" if keys else "none"}
        for r in records:
            for c in columns:
                r.setdefault(c, None)
    return schemas


def _suggest_links(rows, schemas):
    suggestions = []
    for source, s in schemas.items():
        for col in (c["name"] for c in s["columns"]):
            if col in s["foreign_keys"]:
                continue
            values = {token(r[col]) for r in rows[source] if r[col] is not None}
            if not values:
                continue
            for target, ts in schemas.items():
                if source == target or len(ts["primary_key"]) != 1:
                    continue
                key = ts["primary_key"][0]
                normalized = re.sub(r"[^a-z0-9]", "", col.lower())
                expected = {re.sub(r"[^a-z0-9]", "", target.lower().rstrip("s") + "id"),
                            re.sub(r"[^a-z0-9]", "", target.lower()+"id")}
                # Column name alone or overlapping numbers are not enough for a confirmed relationship.
                if normalized not in expected:
                    continue
                keys = {token(r[key]) for r in rows[target]}
                if values <= keys:
                    suggestions.append({"source": source, "column": col, "target": target, "target_column": key,
                                        "status": "needs_confirmation", "evidence": "name similarity + value inclusion + observed unique target key"})
    return suggestions


def load(path):
    path = Path(path).resolve()
    source_files = []
    schema_backed = path.is_dir() and (path / "schema.json").exists()
    if schema_backed:
        from medical_cf.upstream import load as upstream_load
        rows, schemas = upstream_load(path)
        source_files = [path / "schema.json"] + sorted((path / "tables").glob("*.json"))
        for s in schemas.values():
            s["key_origin"] = "declared_schema"
    else:
        rows = {}
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in (".json", ".csv", ".xlsx")) if path.is_dir() else [path]
        for file in files:
            source_files.append(file)
            if file.suffix.lower() == ".xlsx":
                wb = load_workbook(file, data_only=False)
                for ws in wb:
                    records = list(ws.iter_rows(values_only=True))
                    if not records:
                        continue
                    if any(v is None or not isinstance(v, str) for v in records[0]) or len(set(records[0])) != len(records[0]):
                        raise ValueError("XLSX requires a unique, nonempty text header in the first row")
                    if ws.title in rows:
                        raise ValueError("Duplicate table name")
                    rows[ws.title] = [dict(zip(records[0], r)) for r in records[1:] if any(v is not None for v in r)]
            elif file.suffix.lower() == ".json":
                rows[file.stem] = _unwrap(json.loads(file.read_text(encoding="utf-8")))
            elif file.suffix.lower() == ".csv":
                with file.open(encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                        raise ValueError("CSV requires unique column headers")
                    rows[file.stem] = [{k: v if v != "" else None for k, v in r.items()} for r in reader]
            else:
                raise ValueError("Supported input: schema/tables case, XLSX, CSV, JSON, or directory of these")
        schemas = _infer(rows)
    if not rows:
        raise ValueError("No input tables found")
    for records in rows.values():
        for r in records:
            for col, value in r.items():
                value = _clean_value(value)
                if type(value) not in (str, int, float, bool, type(None)):
                    raise ValueError("Only scalar cells are supported")
                if isinstance(value, str) and value.startswith("="):
                    raise ValueError("Formula inputs need an explicit formula adapter; not treated as plain values")
                r[col] = value
    for table, s in schemas.items():
        for col, target in s["foreign_keys"].items():
            if target.count(".") != 1:
                raise ValueError("Only single-column table.column foreign keys are supported")
            t, c = target.split(".")
            if t not in rows or c not in {x["name"] for x in schemas[t]["columns"]}:
                raise ValueError("Declared foreign-key target is missing")
    provenance = {"input": str(path), "schema_backed": schema_backed,
        "constraint_scope": "declared schema" if schema_backed else "observed keys/types only; actual business constraints unknown",
        "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in source_files],
        "upstream_calls": ["Doc2DB load_database_from_directory + schema models"] if schema_backed else ["new plain-table adapter"]}
    return Dataset(rows, schemas, provenance, _suggest_links(rows, schemas))


def validate(rows, schemas):
    """Only supplied structural constraints; no hidden medical invariants."""
    for table, schema in schemas.items():
        seen = set()
        for row in rows[table]:
            for col in schema["columns"]:
                if col["name"] not in row or (row[col["name"]] is None and not col["nullable"]):
                    raise ValueError(f"missing/non-null constraint: {table}.{col['name']}")
            key = tuple(token(row[c]) for c in schema["primary_key"])
            if schema["primary_key"]:
                if any(row[c] is None for c in schema["primary_key"]) or key in seen:
                    raise ValueError(f"primary-key constraint: {table}")
                seen.add(key)
            for c, dest in schema["foreign_keys"].items():
                if row[c] is None:
                    continue
                t, tc = dest.split(".")
                if not any(type(r[tc]) is type(row[c]) and r[tc] == row[c] for r in rows[t]):
                    raise ValueError(f"foreign-key constraint: {table}.{c}")
