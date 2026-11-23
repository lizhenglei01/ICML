"""Look for literal concealed net revenue anywhere in the generated Office package."""

from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree
from zipfile import ZipFile, is_zipfile


OFFICE_SUFFIXES = {".xlsx", ".xlsm", ".docx", ".docm", ".pptx", ".pptm", ".zip"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm"}
XML_SUFFIXES = {".xml", ".rels", ".vml", ".svg"}
PROSE_CONTAINERS = {"p", "si", "is"}
PROSE_TEXT = {"t", "instrText", "delText"}
PROSE_BREAKS = {"br", "cr", "tab"}

AMOUNT = re.compile(
    r"(?<![0-9A-Za-z.])"
    r"(?P<number>[+-]?(?:\d{1,3}(?:[, '’]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:e[+-]?\d{1,3})?)"
    r"(?:[ \t]*(?P<scale>millions?|billions?|thousands?|mn|bn|[kmb]|[千萬万億亿])"
    r"(?![A-Za-z]))?"
    r"(?![0-9A-Za-z]|\.\d)",
    re.IGNORECASE | re.ASCII,
)
SCALES = {
    None: 1,
    "k": 1_000, "thousand": 1_000, "thousands": 1_000, "千": 1_000,
    "万": 10_000, "萬": 10_000,
    "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000, "millions": 1_000_000,
    "亿": 100_000_000, "億": 100_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000, "billions": 1_000_000_000,
}
STRIP_GROUPING = str.maketrans("", "", ", '’")
SCOPE = (
    "Exact literals in instructions, OOXML XML text and attributes, and supported text and zip attachments. "
    "Includes numeric caches, formulas as written, properties and notes. Excludes OCR, opaque binary "
    "embeddings, arithmetic inference and units implied only by surrounding labels."
)


def normalize(text):
    return unicodedata.normalize("NFKC", text).replace("−", "-")


def local_name(name):
    return name.rsplit("}", 1)[-1]


def joined_prose(element):
    """Combine Office runs so a value split across formatting is visible."""
    fragments = []
    endpoints = []
    length = 0
    for child in element.iter():
        tag = local_name(child.tag)
        if tag in PROSE_TEXT and child.text:
            fragment = normalize(child.text)
        elif tag in PROSE_BREAKS:
            fragment = "\n"
        else:
            continue
        fragments.append(fragment)
        length += len(fragment)
        endpoints.append(length)
    return "".join(fragments), endpoints[:-1]


def xml_strings(element, path):
    if element.text and element.text.strip():
        yield path + "/text()", normalize(element.text), ()
    if element.tail and element.tail.strip():
        yield path + "/tail()", normalize(element.tail), ()
    for name, value in element.attrib.items():
        yield path + "/@" + name, normalize(value), ()
    if local_name(element.tag) in PROSE_CONTAINERS:
        prose, seams = joined_prose(element)
        if seams:
            yield path + "/joined-text()", prose, seams
    counts = Counter()
    for child in element:
        tag = local_name(child.tag)
        counts[tag] += 1
        yield from xml_strings(child, f"{path}/{tag}[{counts[tag]}]")


def package_strings(source, prefix=""):
    """Read nested zip parts in memory without extracting them to disk."""
    with ZipFile(source) as package:
        for entry in sorted(package.infolist(), key=lambda item: item.filename):
            if entry.is_dir():
                continue
            part = prefix + entry.filename
            data = package.read(entry)
            attached = BytesIO(data)
            if is_zipfile(attached):
                attached.seek(0)
                yield from package_strings(attached, part + "!/")
                continue
            suffix = Path(entry.filename).suffix.lower()
            if suffix in XML_SUFFIXES:
                root = ElementTree.fromstring(data)
                for location, value, seams in xml_strings(root, f"/{local_name(root.tag)}[1]"):
                    yield part, location, value, seams
            elif suffix in TEXT_SUFFIXES:
                yield part, "text", normalize(data.decode("utf-8-sig")), ()


def visible_strings(path):
    if path.suffix.lower() in OFFICE_SUFFIXES:
        yield from package_strings(path)
    else:
        yield path.name, "text", normalize(path.read_text(encoding="utf-8-sig")), ()


def audit_concealed_values(public_dir, expected_net_by_entity):
    """Flag visible occurrences equal to a concealed net amount, with native locations.

    This literal audit complements the dependency and old-report shortcut audits.
    It cannot prove that visible facts do not jointly reveal another value. A
    coincidental literal identical to a net amount is still reported for review.
    """
    concealed = defaultdict(list)
    for entity, value in sorted(expected_net_by_entity.items()):
        if isinstance(value, bool):
            raise ValueError("Concealed net amounts must be numeric")
        amount = Fraction(str(value))
        concealed[amount].append(entity)

    findings = []
    seen = set()
    for file in sorted(Path(public_dir).iterdir()):
        if not file.is_file() or not (file.name == "instruction.md" or file.suffix.lower() in OFFICE_SUFFIXES):
            continue
        for part, location, text, seams in visible_strings(file):
            for match in AMOUNT.finditer(text):
                if seams and not any(match.start() < seam < match.end() for seam in seams):
                    continue
                decimal = Decimal(match["number"].translate(STRIP_GROUPING))
                scale = match["scale"].lower() if match["scale"] else None
                amount = Fraction(decimal) * SCALES[scale]
                identity = (file.name, part, location, amount)
                if amount in concealed and identity not in seen:
                    seen.add(identity)
                    findings.append({
                        "file": file.name, "part": part, "location": location,
                        "literal": match.group(), "value": str(amount), "entities": concealed[amount],
                    })

    return {"passed": not findings, "findings": findings, "scope": SCOPE}
