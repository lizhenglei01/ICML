"""RevProp facts -> constrained Doc2DB prose -> Worldloom Office renderers.

The allocation is a registered travel-day layout, not a discovered universal
layout. Fact-to-object addresses are recorded while compiling/rendering.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .upstreams import ROOT, THIRD, doc2db_modules
from .evaluate import leaves

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from orgsmith.schemas import Fact
from orgsmith.render.resolve import resolve_text
from worldloom.models import ArtifactIR, ArtifactSection, Table, Row, Column, Cell, Chart, ChartKind
from worldloom.presentation import READER
from worldloom.render import xlsx, docx, pptx

SOURCE = THIRD / "llm-revision-propagation/benchmark/data/annotated/travel_itinerary_L100_06.json"
PPT_GROUPS = {"destination", "day_start_time", "transition_gap_minutes", "summary",
              "party", "season_context", "lodging", "budget_guidance"}
STAMP = "2026-01-01T00:00:00+00:00"


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_schema(value):
    if isinstance(value, dict):
        return {"type": "object", "properties": {k: json_schema(v) for k, v in value.items()},
                "required": list(value), "additionalProperties": False}
    if isinstance(value, list):
        # Shape and list length are public output contracts, never field values.
        return {"type": "array", "prefixItems": [json_schema(v) for v in value],
                "minItems": len(value), "maxItems": len(value)}
    return {"type": "boolean" if isinstance(value, bool) else
            "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string"}


def surface(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def allocation(path):
    parts = path.strip("/").split("/")
    if parts[0] == "stops":
        if parts[-1] in {"name", "start_time"}:
            return "day_schedule.xlsx"
        if parts[-1] == "duration_minutes":
            return "travel_brief.pptx"
        return "visitor_notes.docx"
    return "travel_brief.pptx" if parts[0] in PPT_GROUPS else "visitor_notes.docx"


def description(path, truth):
    parts = path.strip("/").split("/")
    if parts[0] == "stops":
        name = truth["stops"][int(parts[1])]["name"]
        if parts[-1] == "end_time":
            return f"The scheduled finish time for {name} is"
        topic = parts[-2].replace("_", " ")
        return f"For {name}, {topic}: {parts[-1].replace('_', ' ')} is"
    return f"Regarding {' / '.join(p.replace('_', ' ') for p in parts[:-1]) or 'the trip'}, {parts[-1].replace('_', ' ')} is"


class RelayWriter:
    """Transport injection into the original Doc2DB WriterAgent."""
    def __init__(self, out):
        self.out = Path(out)

    def generate(self, prompt, *, system_prompt=None, **kwargs):
        from alignment_probe.codex_transport import call_codex, configured_route
        route = configured_route()
        constraints = (
            "Write a practical visitor briefing in coherent paragraphs. Group related activities and logistics. "
            "Every supplied XML fact tag and {{fact:...}} placeholder must appear exactly once, unchanged; "
            "each placeholder must remain inside its corresponding tag and keep its original subject/attribute. "
            "Do not invent facts, numbers, locations, or recommendations. Do not mention databases, JSON, "
            "field names, provenance tags, or benchmark construction in the prose. Tags will be removed later. "
            "Use blank lines between paragraphs. Return the tagged prose only.")
        request = {"messages": [{"role": "system", "content": (system_prompt or "") + "\n" + constraints},
                                {"role": "user", "content": prompt}]}
        save(self.out / "request.json", request)
        text, metadata = call_codex(request, route["model"], route["effort"] or "high", self.out / "transport")
        (self.out / "response.txt").write_text(text)
        save(self.out / "call.json", metadata)
        return text


def author_notes(truth, facts, out, mode):
    models, config, writer = doc2db_modules()
    items = []
    for index, fact in enumerate(facts, 1):
        fid = fact["id"]
        items.append(models.TaskItem(fragment_id=f"cell_r{index}_c1_frag1",
            source_cell=models.CellRef(row_index=index, column_name=fact["path"], value=None),
            text=description(fact["path"], truth) + " {{fact:" + fid + "}}."))
        fact["tag"] = f"r{index}_c1"
    cfg = config.SynthesisConfig()
    cfg.base.use_injected_parameters = False
    cfg.base.reference_guide_path = ""
    request = models.WriterInput(block=models.TaskBlock(block_id="visitor_brief", block_index=0, items=items),
                                history=models.HistorySummary(), config=cfg)
    engine = writer.WriterAgent(llm=RelayWriter(out) if mode == "model" else None)
    # Frozen authoring can be replayed without another model call.
    saved = out / "accepted_tagged.txt"
    if saved.exists():
        tagged = saved.read_text()
    else:
        tagged = engine.run(request).text
    expected = {f["id"] for f in facts}
    refs = re.findall(r"\{\{fact:([^}]+)\}\}", tagged)
    if Counter(refs) != Counter({x: 1 for x in expected}):
        raise ValueError("Writer lost, duplicated or invented fact placeholders")
    for fact in facts:
        matches = re.findall(fr"<{fact['tag']}>(.*?)</{fact['tag']}>", tagged, re.S)
        if len(matches) != 1 or "{{fact:" + fact["id"] + "}}" not in matches[0]:
            raise ValueError("Writer changed a fact-tag association: " + fact["path"])
    out.mkdir(parents=True, exist_ok=True)
    saved.write_text(tagged)
    # Strip construction tags before rendering. Only placeholders remain here.
    without_tags = re.sub(r"</?r\d+_c\d+>", "", tagged)
    resolved_facts = {f["id"]: Fact(id=f["id"], kind="text", value=surface(f["value"]),
                                  rendered=surface(f["value"])) for f in facts}
    paragraphs = []
    for tagged_template in re.split(r"\n\s*\n", tagged.strip()):
        tagged_template = tagged_template.strip()
        template = re.sub(r"</?r\d+_c\d+>", "", tagged_template)
        if not template:
            continue
        resolved = resolve_text(template, resolved_facts, "visitor brief")
        spans = []
        for match in re.finditer(r"\{\{fact:([^}]+)\}\}", template):
            prefix = resolve_text(template[:match.start()], resolved_facts, "prefix")
            value = resolved_facts[match[1]].rendered
            spans.append({"id": match[1], "start": len(prefix), "end": len(prefix) + len(value)})
        for span in spans:
            fact = next(f for f in facts if f["id"] == span["id"])
            claim = re.search(fr"<{fact['tag']}>(.*?)</{fact['tag']}>", tagged_template, re.S)
            if claim is None:
                raise ValueError("Fact claim must remain within one paragraph")
            prefix = re.sub(r"</?r\d+_c\d+>", "", tagged_template[:claim.start()])
            claim_text = resolve_text(claim[1], resolved_facts, "claim")
            span.update(claim_start=len(resolve_text(prefix, resolved_facts, "prefix")),
                        claim_end=len(resolve_text(prefix, resolved_facts, "prefix")) + len(claim_text))
        paragraphs.append({"text": resolved, "spans": spans})
    save(out / "compiled_paragraphs.json", paragraphs)
    return paragraphs


def build(output, *, author="model", source=SOURCE):
    output = Path(output).resolve()
    public, private = output / "public", output / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    sample = json.loads(source.read_text())
    truth = deepcopy(sample["current_json"])
    if sample.get("theme") != "Removing a stop from the day's schedule":
        raise ValueError("This registered layout supports the remove-stop travel scenario only")
    source_digest = digest(source)
    facts = [{"id": f"f:trip.{i}", "path": p, "value": v, "file": allocation(p)}
             for i, (p, v) in enumerate(leaves(truth), 1)]
    prior = private / "truth.json"
    if prior.exists() and json.loads(prior.read_text()) != truth:
        raise ValueError("Refusing to reuse authoring with different facts")
    save(prior, truth)
    save(private / "source_sample.json", sample)
    word_facts = [f for f in facts if f["file"].endswith("docx")]
    authoring_replayed = (private / "authoring/accepted_tagged.txt").exists()
    paragraphs = author_notes(truth, word_facts, private / "authoring", author)
    metadata = {"worldloom_created": STAMP, "note": "Controlled synthetic travel materials."}
    schedule = Table(key="visits", title="Day schedule",
                     columns=[Column(key="start", label="Start time")],
                     rows=[Row(key=f"stop_{i}", label=s["name"], cells={"start": Cell(value=s["start_time"])})
                           for i, s in enumerate(truth["stops"])],
                     note="Listed in visit order. Activity lengths are in the travel briefing; visitor notes give finish times.")
    wb_ir = ArtifactIR(id="travel-schedule", intent_id="travel", title="Daily departures",
                       sections=[ArtifactSection(heading="Schedule", table=schedule)], metadata=metadata)
    (public / "day_schedule.xlsx").write_bytes(xlsx.render(wb_ir))
    word_ir = ArtifactIR(id="visitor-notes", intent_id="travel", title="Visitor notes and practical arrangements",
                         sections=[ArtifactSection(heading="Visits and arrangements",
                                   body="\n\n".join(p["text"] for p in paragraphs))], metadata=metadata)
    (public / "visitor_notes.docx").write_bytes(docx.render(word_ir, presentation=READER))
    durations = Table(key="duration", title="Time allocated to each visit",
                      columns=[Column(key="minutes", label="Minutes", number_format="0")],
                      rows=[Row(key=f"stop_{i}", label=s["name"], cells={"minutes": Cell(value=s["duration_minutes"])})
                            for i, s in enumerate(truth["stops"])])
    ppt_facts = [f for f in facts if f["file"].endswith("pptx") and not f["path"].startswith("/stops/")]
    # Three short briefing sections, rather than printing the state tree.
    overview = [f for f in ppt_facts if f["path"].split("/")[1] in {"destination", "day_start_time", "transition_gap_minutes", "summary"}]
    group = [f for f in ppt_facts if f not in overview]
    def brief(items):
        return "\n\n".join(description(f["path"], truth) + " " + surface(f["value"]) + "." for f in items)
    deck_ir = ArtifactIR(id="travel-brief", intent_id="travel", title="Day trip briefing",
        sections=[ArtifactSection(heading="At a glance", body=brief(overview)),
                  ArtifactSection(heading="Travel party and planning preferences", body=brief(group)),
                  ArtifactSection(heading="Activity time budget", table=durations,
                                  charts=[Chart(key="durations", title="Minutes allocated to each visit", kind=ChartKind.BAR,
                                                table="duration", series=["minutes"], value_axis="Minutes")])], metadata=metadata)
    # This is a travel briefing, not the renderer's default executive-summary
    # genre (whose mandatory argument roles do not describe an itinerary).
    (public / "travel_brief.pptx").write_bytes(pptx.render(deck_ir, profile=READER, artifact_type="travel_brief"))
    # Locators are discovered from the rendered objects using known compiler
    # identities/text, never manually entered Office coordinates.
    bindings = []
    wb = load_workbook(public / "day_schedule.xlsx")
    positions = {cell.value: cell.row for cell in wb.active["A"] if cell.value in {s["name"] for s in truth["stops"]}}
    for fact in facts:
        if fact["file"].endswith("xlsx"):
            parts = fact["path"].split("/")
            stop = truth["stops"][int(parts[2])]
            bindings.append({**fact, "sheet": wb.active.title,
                             "cell": ("A" if parts[-1] == "name" else "B") + str(positions[stop["name"]])})
    document = Document(public / "visitor_notes.docx")
    word_by_id = {f["id"]: f for f in word_facts}
    for paragraph in paragraphs:
        candidates = [i for i, p in enumerate(document.paragraphs) if p.text == paragraph["text"]]
        if len(candidates) != 1:
            raise ValueError("Cannot uniquely locate rendered paragraph")
        for span in paragraph["spans"]:
            bindings.append({**word_by_id[span["id"]], "paragraph": candidates[0],
                             "start": span["start"], "end": span["end"],
                             "claim_start": span["claim_start"], "claim_end": span["claim_end"]})
    deck = Presentation(public / "travel_brief.pptx")
    for fact in ppt_facts:
        needle = description(fact["path"], truth) + " " + surface(fact["value"]) + "."
        found = [(i, j, shape.text) for i, slide in enumerate(deck.slides) for j, shape in enumerate(slide.shapes)
                 if shape.has_text_frame and needle in shape.text]
        if len(found) != 1:
            raise ValueError("Cannot uniquely locate deck fact " + fact["path"])
        i, j, text = found[0]
        start = text.index(needle) + len(description(fact["path"], truth)) + 1
        bindings.append({**fact, "slide": i, "shape": j, "start": start, "end": start + len(surface(fact["value"]))})
    charts = [(i, j, s.chart) for i, slide in enumerate(deck.slides) for j, s in enumerate(slide.shapes) if s.has_chart]
    if len(charts) != 1:
        raise ValueError("Expected exactly one native activity chart")
    si, sh, chart = charts[0]
    for index, stop in enumerate(truth["stops"]):
        fact = next(f for f in facts if f["path"] == f"/stops/{index}/duration_minutes")
        if chart.series[0].values[index] != stop["duration_minutes"]:
            raise ValueError("Native chart value differs from source")
        bindings.append({**fact, "slide": si, "shape": sh, "series": 0, "point": index})
    if Counter(b["path"] for b in bindings) != Counter({p: 1 for p, _ in leaves(truth)}):
        raise ValueError("Incomplete or duplicated primary fact bindings")
    # The standard organization, list length and per-position field inventories
    # are evaluator-only. They must never be mounted in the reader's workspace.
    save(private / "reconstruction.schema.json", json_schema(truth))
    legacy_schema = public / "reconstruction.schema.json"
    if legacy_schema.exists():
        legacy_schema.unlink()
    save(private / "input_protocol.json", {
        "version": 2, "schema_provided": False,
        "initial_state_format": "Model chooses its own JSON organization from native evidence",
        "initial_state_scoring": "NOT_SCORED: semantic alignment to private facts is not implemented",
        "revision_scoring": "Native file oracle; free-form JSON validity checked, semantic content not scored"})
    (public / "instruction.md").write_text(
        "# Understand the existing trip / 理解现有行程\n\n"
        "Read day_schedule.xlsx, visitor_notes.docx and travel_brief.pptx. "
        "Understand the current trip and its practical arrangements so you can maintain it when requirements change. "
        "Organize the information supported by the materials into a JSON object using your own structure and field names. "
        "Do not invent information. At this stage only understand the existing materials; do not edit them. "
        "You may inspect native file objects with Python. Return only your JSON object.\n\n"
        "阅读这三份现有文件，理解行程及执行安排，便于后续需求变更时维护。"
        "请自行设计JSON结构和字段名，整理材料支持的信息，不得编造；本轮只理解资料，不修改文件。"
        "允许用Python读取原生文件对象，仅返回你自己组织的JSON对象。\n")
    save(private / "bindings.json", bindings)
    for name, ir in [("schedule", wb_ir), ("notes", word_ir), ("brief", deck_ir)]:
        save(private / f"{name}.artifact_ir.json", ir.model_dump(mode="json"))
    components = ["Worldloom", "OrgSmith", "Doc2DB-Bench", "DRBench", "SynthDocQA", "llm-revision-propagation"]
    records = []
    for name in components:
        repo = THIRD / name
        license_paths = [p for p in repo.iterdir() if p.is_file() and p.name.lower().startswith("license")]
        records.append({"name": name, "path": str(repo),
            "commit": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
            "license_files": [{"file": p.name, "sha256": digest(p)} for p in license_paths]})
    save(private / "sources.json", {"repositories": records, "sample": str(source), "sample_sha256": source_digest,
        "actual_calls": ([] if authoring_replayed else ["Doc2DB.WriterAgent.run" if author == "model" else "Doc2DB.WriterAgent.run (literal fallback)"]) +
                         ["OrgSmith.resolve_text", "Worldloom.xlsx.render", "Worldloom.docx.render", "Worldloom.pptx.render"],
        "cached_component_outputs": ["Previous accepted Doc2DB.WriterAgent.run output; see private/authoring/cache_origin.json where present"] if authoring_replayed else [],
        "reference_only": ["DRBench insight allocation design", "SynthDocQA visual/ground-truth design"],
        "new_code": "multifile_reconstruction: travel layout adapter, locators, auditing, reconstruction scoring and model runner"})
    if digest(source) != source_digest:
        raise AssertionError("Upstream sample changed")
    summary = {"source_case": sample["id"], "facts": len(facts), "primary_allocation": dict(Counter(f["file"] for f in facts)),
               "native_charts": len(charts), "word_paragraphs": len(document.paragraphs), "slides": len(deck.slides),
               "authoring": author, "scope": "Registered travel layout; reconstruction only; no counterfactual editing or general dependency discovery."}
    save(output / "build_summary.json", summary)
    return summary
