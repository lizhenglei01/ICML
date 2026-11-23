"""Scoped OOXML preservation fingerprints, including native formatting/objects."""
from zipfile import ZipFile

from lxml import etree

from .upstream import FILES, patcher, sha256

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"w": W, "x": X}


def normalized_part(name, data, filename, sheet_paths, editable_paragraphs):
    if name not in {"word/document.xml", *sheet_paths.values()}:
        return data
    root = etree.fromstring(data)
    if filename == FILES[1] and name == "word/document.xml":
        paras = root.findall("w:body/w:p", NS)
        for index in editable_paragraphs:
            for t in paras[index].iter(f"{{{W}}}t"):
                t.text = "__EDITABLE_TEXT__"
    if filename == FILES[0]:
        if name == sheet_paths["Approval"]:
            c = root.find(".//x:c[@r='B4']", NS)
            c.attrib.pop("t", None)
            for e in list(c):
                if e.tag in {f"{{{X}}}v", f"{{{X}}}is"}:
                    c.remove(e)
        # Caches may be absent/updated, but formulas and cell formats are protected.
        for c in root.findall(".//x:c", NS):
            if c.find("x:f", NS) is not None:
                for v in c.findall("x:v", NS):
                    c.remove(v)
    return etree.tostring(root, method="c14n")


def fingerprints(directory, editable_paragraphs=None):
    if editable_paragraphs is None:
        editable_paragraphs = [3, 18, 19]
    output = {}
    for filename in FILES:
        with ZipFile(directory / filename) as z:
            if z.testzip() is not None or len(z.namelist()) != len(set(z.namelist())):
                raise ValueError("Invalid or duplicate ZIP members")
            paths = patcher()._xlsx_sheet_paths(z) if filename == FILES[0] else {}
            for name in z.namelist():
                data = z.read(name)
                if name.endswith((".xml", ".rels")):
                    etree.fromstring(data)  # all XML parts must parse
                output[f"{filename}:{name}"] = sha256(normalized_part(name, data, filename, paths, editable_paragraphs))
    return output


def set_yoy_cache(path, value):
    p = patcher()
    with ZipFile(path) as z:
        part = p._xlsx_sheet_paths(z)["Review"]
        root = etree.fromstring(z.read(part))
    cell = root.find(".//x:c[@r='B5']", NS)
    for e in cell.findall("x:v", NS):
        cell.remove(e)
    if value is not None:
        etree.SubElement(cell, f"{{{X}}}v").text = str(value)
    p._rewrite_zip(path, {part: etree.tostring(root, encoding="UTF-8", xml_declaration=True)})
