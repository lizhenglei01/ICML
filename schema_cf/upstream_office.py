"""Direct use of unchanged OfficeEditBench patching and workbook-diff functions."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
from zipfile import ZipFile

from lxml import etree as E
from openpyxl import load_workbook

REPO = Path(__file__).resolve().parents[1] / "三方依赖代码/OfficeEditBench"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATCH_PATH = REPO / "hard/patch_office.py"
VERIFY_PATH = REPO / "medium/cohorts/cross_media/verify_task.py"
patcher = _module("schema_cf_obm_patch", PATCH_PATH)
verifier = _module("schema_cf_obm_verify", VERIFY_PATH)


def provenance():
    return {"repository": "https://github.com/Aniriswu/OfficeEditBench",
        "commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "license": "CC BY 4.0; see OfficeEditBench/LICENSE.txt",
        "functions": ["patch_xlsx", "patch_docx", "_xlsx_sheet_paths", "_rewrite_zip", "workbook_diff"],
        "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (PATCH_PATH, VERIFY_PATH, REPO / "LICENSE.txt")],
        "scope": "Actual helpers called; original task-specific gold/check_workbook/check_doc are NOT used for new counterfactuals."}


def normalize_generated_workbook(path):
    """Compatibility: upstream regex patcher expects prefixed worksheet cell tags.

    Normalize ONLY our newly rendered workbook, before baseline snapshots. No source input is changed.
    This avoids appending duplicate cells when upstream encounters unprefixed XML.
    """
    replacements = {}
    with ZipFile(path) as z:
        mapping = patcher._xlsx_sheet_paths(z)
        for part in mapping.values():
            old = E.fromstring(z.read(part))
            namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            root = E.Element(old.tag, nsmap={"x": namespace})
            root.attrib.update(old.attrib)
            root.text = old.text
            for child in list(old):
                root.append(child)
            replacements[part] = E.tostring(root)
    patcher._rewrite_zip(Path(path), replacements)


def edit(directory, changes, old_values):
    xlsx, docx = [], []
    for binding, value in changes:
        if binding["kind"] == "xlsx":
            xlsx.append({"type": "xlsx_cell", "sheet": binding["sheet"], "cell": binding["cell"], "value": value})
        else:
            docx.append({"type": "docx_replace", "old": old_values[binding["id"]], "new": str(value), "count": 1})
    patcher.patch_xlsx(Path(directory) / "data.xlsx", xlsx)
    patcher.patch_docx(Path(directory) / "report.docx", docx)
    return {"patch_xlsx_operations": len(xlsx), "patch_docx_operations": len(docx)}


def verify_diff(before, after, expected):
    left, right = load_workbook(before, data_only=False), load_workbook(after, data_only=False)
    approximate = verifier.workbook_diff(left, right)
    # Upstream uses isclose even for integer identifiers: e.g. 100000001 -> 100000002
    # may be missed. Retain its result but supplement with exact cell comparisons.
    actual = set(approximate)
    for sheet in left.sheetnames:
        for coordinate in left[sheet]._cells.keys() | right[sheet]._cells.keys():
            a = left[sheet]._cells.get(coordinate)
            b = right[sheet]._cells.get(coordinate)
            if (a.value if a is not None else None) != (b.value if b is not None else None):
                from openpyxl.utils import get_column_letter
                actual.add((sheet, get_column_letter(coordinate[1])+str(coordinate[0])))
    wanted = {(b["sheet"], b["cell"]) for b, _ in expected if b["kind"] == "xlsx"}
    if actual != wanted:
        raise AssertionError({"unexpected": sorted(actual-wanted), "missing": sorted(wanted-actual)})
    return {"pass": True, "function": "OfficeEditBench.workbook_diff + exact-value adapter",
            "upstream_approximate": sorted(approximate), "exact_supplement": sorted(actual-approximate),
            "actual": sorted(actual), "expected": sorted(wanted)}
