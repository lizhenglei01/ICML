"""Bounded Refiner integration: temporal evidence with independently known semantics.

The model selects a wording from two registered, equivalent temporal statements.
It does not invent the arithmetic rule. Unsupported free-form evidence is rejected.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta
import importlib
import json
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from .build import save
from .upstreams import doc2db_modules


def replace_span(paragraph, start, end, replacement):
    """Change text in existing runs, retaining paragraph/run properties."""
    if not 0 <= start <= end <= len(paragraph.text):
        raise ValueError("Invalid paragraph text span")
    offset = 0
    runs = list(paragraph.runs)
    for run in runs:
        old = run.text
        left, right = max(start - offset, 0), min(end - offset, len(old))
        if right > left:
            run.text = old[:left] + (replacement if offset <= start < offset + len(old) else "") + old[right:]
        offset += len(old)


def temporal_forms(name):
    return [
        f"{name} finishes when the activity length shown in the briefing chart has elapsed from its listed start time in the spreadsheet.",
        f"For {name}, calculate the finish time by adding its briefing-chart activity length to its spreadsheet start time.",
    ]


class RefinerRelay:
    def __init__(self, directory, forms):
        self.directory, self.forms = directory, forms

    def generate(self, prompt, *, system_prompt=None, **kwargs):
        from alignment_probe.codex_transport import call_codex, configured_route
        route = configured_route()
        request = {"messages": [
            {"role": "system", "content": (system_prompt or "") +
             "\nFor this controlled experiment choose exactly one of the following verified evidence sentences. "
             "Return it unchanged as the only evidence_pool content. No additional clauses or fragments. " + json.dumps(self.forms)},
            {"role": "user", "content": prompt}]}
        save(self.directory / "request.json", request)
        raw, metadata = call_codex(request, route["model"], route["effort"] or "high", self.directory / "transport")
        (self.directory / "response.txt").write_text(raw)
        save(self.directory / "call.json", metadata)
        return raw


def add_indirect_evidence(package, *, model=True):
    package = Path(package)
    directory = package / "private/refiner"
    directory.mkdir(parents=True, exist_ok=True)
    truth = json.loads((package / "private/truth.json").read_text())
    bindings = json.loads((package / "private/bindings.json").read_text())
    path = "/stops/5/end_time"
    target = next(b for b in bindings if b["path"] == path)
    if target.get("rule"):
        return json.loads((directory / "integration.json").read_text())
    stop = truth["stops"][5]
    forms = temporal_forms(stop["name"])
    cache = directory / "evidence_pool.json"
    replayed = cache.exists()
    if cache.exists():
        pool_data = json.loads(cache.read_text())
    elif model:
        models, config, _ = doc2db_modules()
        # The namespace loader deliberately skips unrelated pipeline imports.
        client_module = importlib.import_module("reconstruction_doc2db.llm.openai_client")
        llm_module = importlib.import_module("reconstruction_doc2db.llm")
        llm_module.OpenAILLMClient = client_module.OpenAILLMClient
        refiner = importlib.import_module("reconstruction_doc2db.agents.refiner")
        table = models.Table(table_id="visits", schema=models.TableSchema(
            table_name="visits", primary_key=["name"], table_type="entity", columns=[
                models.ColumnSchema(name="name", dtype="string"),
                models.ColumnSchema(name="start_time", dtype="string"),
                models.ColumnSchema(name="duration_minutes", dtype="integer"),
                models.ColumnSchema(name="end_time", dtype="string",
                                    description="End equals start plus duration. Refer to the spreadsheet start and the native briefing chart duration.")]),
            rows=[{k: stop[k] for k in ("name", "start_time", "duration_minutes", "end_time")}])
        cfg = config.SynthesisConfig()
        cfg.base.use_injected_parameters = False
        cfg.base.reference_guide_path = ""
        cfg.runtime.max_validation_retries = 0
        request = models.RefinerInput(table=table, config=cfg, capability_matrix=models.CapabilityMatrix(
            table_id="visits", cells=[models.AnnotatedCell(table_id="visits",
                ref=models.CellRef(row_index=0, column_name="end_time", value=stop["end_time"]),
                labels=[models.CapabilityLabel.RI_TC], hide_final_value=True)]))
        save(directory / "input.json", request.model_dump(mode="json"))
        relay = RefinerRelay(directory, forms)
        with (directory / "upstream.log").open("w") as log, redirect_stdout(log):
            pool = refiner.RefinerAgent(llm=relay, verification_llm=relay).run(request)
        pool_data = pool.model_dump(mode="json")
        save(cache, pool_data)
    else:
        pool_data = {"table_id": "visits", "fragments": [{"text": forms[0]}]}
        save(cache, pool_data)
    fragments = pool_data["fragments"]
    if len(fragments) != 1 or fragments[0]["text"] not in forms:
        raise ValueError("Refiner evidence is outside the verified temporal grammar; refusing to use it as truth")
    sentence = fragments[0]["text"]
    if stop["end_time"] in sentence:
        raise AssertionError("Indirect evidence disclosed the result")
    # This calculation is independent of the model and performed before rendering.
    expected = (datetime.strptime(stop["start_time"], "%H:%M") + timedelta(minutes=stop["duration_minutes"])).strftime("%H:%M")
    if expected != stop["end_time"]:
        raise AssertionError("Temporal source facts are inconsistent")
    document = Document(package / "public/visitor_notes.docx")
    paragraph = document.paragraphs[target["paragraph"]]
    start, end = target["claim_start"], target["claim_end"]
    delta = len(sentence) - (end - start)
    replace_span(paragraph, start, end, sentence)
    for b in bindings:
        if b["file"] != "visitor_notes.docx" or b["paragraph"] != target["paragraph"] or b is target:
            continue
        if b["start"] >= end:
            for key in ("start", "end", "claim_start", "claim_end"):
                b[key] += delta
    target.update(start=start, end=start + len(sentence), claim_start=start, claim_end=start + len(sentence),
                  rule="finish_equals_start_plus_chart_duration", stop_name=stop["name"],
                  evidence_sentence=sentence,
                  operands=["/stops/5/start_time", "/stops/5/duration_minutes"])
    document.save(package / "public/visitor_notes.docx")
    save(package / "private/bindings.json", bindings)
    report = {"actual_call": ("Previous accepted Doc2DB.RefinerAgent output replay; not invoked this build" if replayed else "Doc2DB.RefinerAgent.run") if model else "offline controlled grammar (no Refiner model)",
              "target": path, "source": "RevProp current_json stop 5; registered time arithmetic rule",
              "rule": "finish = spreadsheet start + briefing native-chart duration (minutes)",
              "independent_result": expected, "sentence": sentence,
              "limitation": "One registered temporal rule and two approved wordings; not free-form recoverability verification or arbitrary evidence generation.",
              "upstream_caveat": "Refiner._generate_with_llm returns before its verification code; this adapter instead rejects evidence outside the verified grammar."}
    save(directory / "integration.json", report)
    sources = json.loads((package / "private/sources.json").read_text())
    sources.setdefault("cached_component_outputs", []).append(report["actual_call"]) if replayed else sources["actual_calls"].append(report["actual_call"])
    save(package / "private/sources.json", sources)
    return report


def read_indirect(binding, public):
    """Read both native operands afresh, rather than using hidden source values."""
    public = Path(public)
    wb = load_workbook(public / "day_schedule.xlsx")
    rows = [row for row in wb.active.iter_rows() if row[0].value == binding["stop_name"]]
    if len(rows) != 1:
        raise ValueError("Activity start is not uniquely recoverable")
    start = rows[0][1].value
    deck = Presentation(public / "travel_brief.pptx")
    charts = [s.chart for sl in deck.slides for s in sl.shapes if s.has_chart]
    if len(charts) != 1:
        raise ValueError("Expected one activity chart")
    chart = charts[0]
    names = [c.label for c in chart.plots[0].categories]
    index = names.index(binding["stop_name"])
    minutes = chart.series[0].values[index]
    return (datetime.strptime(start, "%H:%M") + timedelta(minutes=minutes)).strftime("%H:%M")
