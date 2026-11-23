"""Actual, offline calls into the unchanged upstream checkout."""
from __future__ import annotations

import contextlib
import hashlib
import io
import importlib
import json
from pathlib import Path
import random
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "三方依赖代码/Doc2DB-Bench"
CASE = REPO / "dataset/medical/case1"
sys.path.insert(0, str(REPO))

from data_construction.src.config import SynthesisConfig, OrderingStrategy
from data_construction.src.models import (
    CellRef, EvidenceFragment, EvidencePool, SerializerInput, DocumentBlock,
    Table, TableSchema, ColumnSchema, ValidatorInput,
)
from data_construction.src.utils.input_loader import load_database_from_directory
from data_construction.src.agents.serializer import SerializerAgent
from data_construction.src.agents.validator import ValidatorAgent
from data_construction.src.pipeline import DocumentSynthesisPipeline


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(case=CASE):
    files = [REPO / "README.md"] + sorted((REPO / "data_construction/src").rglob("*.py"))
    files += [Path(case) / "schema.json"] + sorted((Path(case) / "tables").glob("*.json"))
    files += sorted((Path(case) / "docs").glob("*.md"))
    return {
        "repository": "https://github.com/SetonLiang/Doc2DB-Bench",
        "commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "status": subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True),
        "case": str(Path(case).resolve()),
        "files": [{"path": str(p.resolve()), "sha256": sha(p)} for p in files],
        "licensing": "No root LICENSE located in this checkout. Public availability is not a verified redistribution license; retain Doc2DB/Spider attribution and resolve before redistribution.",
        "source_docs_usage": "Five released Markdown documents inventoried, not rewritten or used as new native Office reference files.",
    }


def load(case=CASE):
    db = load_database_from_directory(case)
    rows = {t.table_id: t.rows for t in db.tables}
    schemas = {t.table_id: t.schema.model_dump() for t in db.tables}
    return rows, schemas


def audit_entrypoints(case=CASE):
    """Exercise original routing without network calls, output generation or source writes."""
    db = load_database_from_directory(case)
    cfg = SynthesisConfig()
    cfg.runtime.if_cached = False
    pipeline = DocumentSynthesisPipeline(cfg, None, None, None, SimpleNamespace(), None)
    # Avoid optional profiler/config-discovery side effects; leave original table loop unchanged.
    pipeline._maybe_prepare_reference_guide = lambda: None
    pipeline._load_dataset_config = lambda root: {}
    pipeline._build_anchor_map = lambda config: {}
    visited = []
    def record(table, **kwargs):
        visited.append(table.table_id)
        raise AssertionError("Unexpected run_table; medical tables should be filtered out in this checkout")
    pipeline.run_table = record
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        result = pipeline.run_database(db)
    if visited or result.table_results:
        raise AssertionError("Original routing behavior changed; re-audit extension")
    try:
        importlib.import_module("data_construction.run_demo")
        demo = "import succeeded"
    except ModuleNotFoundError as e:
        demo = type(e).__name__ + ": " + str(e)
    return {"medical_input_tables": [t.table_id for t in db.tables],
            "original_run_database_tables_processed": list(result.table_results),
            "original_loop_stdout": log.getvalue(), "run_demo_import": demo,
            "execution_scope": "Original run_database loop with profiler/config disabled and no LLM agents; no original full synthesis executed.",
            "actual_extension_calls": [
                "utils.input_loader.load_database_from_directory (schema + 6 tables)",
                "models.CellRef/EvidenceFragment/EvidencePool/Table/DocumentBlock/ValidatorInput",
                "SerializerAgent.run (orders/chunks evidence used for native Word layout)",
                "DocumentSynthesisPipeline._assemble_document (joins private tagged evidence blocks)",
                "ValidatorAgent.run (hard provenance coverage only; no soft LLM validation)"],
            "new_components": ["registered medical fact/rule engine", "deterministic evidence writer", "XLSX/DOCX renderer and automatic location recording",
                               "local OOXML counterfactual patcher", "Oracle spec generation/evaluation", "independent SQLite/native-file/LibreOffice checks"],
            "not_integrated": ["original LLM LabelingAgent/RefinerAgent/WriterAgent and soft verifier", "PPT", "learned alignment", "EditCoherenceBench code", "other upstream benchmark code"]}


def serialize_and_check(facts):
    """Use original serializer + hard provenance validator, with a NEW deterministic writer.

    Derived source_cell points to the synthetic Facts table, not falsely to a DB cell.
    Actual DB sources and rule lineage live in each fact's sources/dependencies.
    """
    ordered = list(facts.values())
    rows = [{"id": f["id"], "value": f["value"]} for f in ordered]
    table = Table(table_id="Facts", schema=TableSchema(table_name="Facts", columns=[
        ColumnSchema(name="id", dtype="str"), ColumnSchema(name="value", dtype="object")
    ], primary_key=["id"]), rows=rows)
    fragments = [EvidenceFragment(
        fragment_id=f"cell_r{i}_c1_frag0", table_id="Facts",
        source_cell=CellRef(row_index=i, column_name="value", value=f["value"]),
        text=f["id"] + " = " + json.dumps(f["value"], ensure_ascii=False),
    ) for i, f in enumerate(ordered)]
    pool = EvidencePool(table_id="Facts", fragments=fragments)
    cfg = SynthesisConfig()
    cfg.strategy.ordering_strategy = OrderingStrategy.ROW_MAJOR
    cfg.strategy.chunk_size = 20
    cfg.runtime.enable_soft_validation = False
    # Upstream serializer draws random tie breakers; isolate its global RNG use.
    rng_state = random.getstate()
    try:
        random.seed(0)
        queue = SerializerAgent().run(SerializerInput(evidence_pool=pool, config=cfg))
    finally:
        random.setstate(rng_state)
    blocks = []
    ids = {}
    for b in queue.blocks:
        texts = []
        for item in b.items:
            tag = ValidatorAgent._cell_tag_from_fragment_id(item.fragment_id, item.source_cell.row_index, "value")
            texts.append(f"<{tag}>{item.text}</{tag}>")
            ids[item.fragment_id] = ordered[item.source_cell.row_index]["id"]
        blocks.append(DocumentBlock(block_id=b.block_id, block_index=b.block_index,
                                    text="\n".join(texts), used_fragment_ids=[x.fragment_id for x in b.items]))
    # This upstream method does not access self. No original LLM pipeline is executed.
    document = DocumentSynthesisPipeline._assemble_document(None, "Facts", blocks)
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        report = ValidatorAgent(llm=None).run(ValidatorInput(
            table=table, evidence_pool=pool, document=document, config=cfg))
    if not report.hard_pass:
        raise ValueError("Upstream provenance coverage failed")
    return {"pool": pool.model_dump(mode="json"), "queue": queue.model_dump(mode="json"),
            "fragment_to_fact": ids, "document": document.model_dump(mode="json"),
            "validation": report.model_dump(mode="json"), "log": log.getvalue(),
            "soft_validation_executed": False,
            "writer": "new deterministic evidence writer; original LLM writer/refiner/labeler NOT executed"}
