"""Load upstream components without executing their unrelated demo pipelines."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIRD = ROOT / "三方依赖代码"
for directory in (ROOT, THIRD / "Worldloom/src", THIRD / "OrgSmith"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


def doc2db_modules():
    # Its __init__ imports the entire pipeline (including unrelated clients).
    # Namespace packages let us import the unmodified writer and typed models.
    source = THIRD / "Doc2DB-Bench/data_construction/src"
    for suffix in ("", ".agents", ".llm"):
        name = "reconstruction_doc2db" + suffix
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(source.joinpath(*suffix.strip(".").split("."))) if suffix else str(source)]
            sys.modules[name] = module
    return tuple(importlib.import_module("reconstruction_doc2db." + x)
                 for x in ("models", "config", "agents.writer"))
