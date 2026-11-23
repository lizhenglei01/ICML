"""Deterministic bilingual evidence rendering; the writer never receives answers."""
import html
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET


def narrative_spec(graph):
    """Capability boundary: omit data values, desired memberships and answers."""
    return {"topic": graph["metadata"]["topic"], "label": graph["metadata"]["label"],
            "selection": graph["selection"], "outputs": graph["outputs"],
            "nodes": [{k: v for k, v in n.items() if k not in {"unit", "threshold_unit", "rule_source"}}
                      for n in graph["nodes"]],
            "metric_labels": {m["id"]: m["label"] for m in graph["metadata"]["metrics"]}}


def write_narrative(spec):
    nodes = {n["id"]: n for n in spec["nodes"]}
    aliases, lines = {}, []
    for n in spec["nodes"]:
        op = n["op"]
        if op == "FACT":
            aliases[n["id"]] = spec["metric_labels"][n["metric"]]
            continue
        if op in {"COUNT", "AVG"}: continue
        alias = f"R{len(lines)+1}"
        aliases[n["id"]] = alias
        args = [aliases[i] for i in n["inputs"]]
        if op in {"GE", "LE"}:
            sign = ">=" if op == "GE" else "<="
            content = f"指标 [{args[0]}] {sign} {n['threshold']} / Metric [{args[0]}] {sign} {n['threshold']}."
        elif op in {"MAX", "MIN"}:
            word = "maximum" if op == "MAX" else "minimum"
            content = f"指标取{'最大' if op == 'MAX' else '最小'}值，含并列 / Metric [{args[0]}] attains the {word}; include all ties."
        elif op == "AND":
            content = f"同时满足 {args[0]} 与 {args[1]} / Satisfy both {args[0]} and {args[1]}."
        elif op == "OR":
            content = f"满足 {args[0]} 或 {args[1]} 中至少一项 / Satisfy at least one of {args[0]} or {args[1]} (inclusive OR)."
        else:
            sign = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/"}[op]
            content = f"按实体分别计算 / For each entity: [{args[0]}] {sign} [{args[1]}]."
        lines.append({"node_id": n["id"], "alias": alias, "text": f"{alias}: {content}"})
    definition = f"满足 {aliases[spec['selection']]} 的实体构成“{spec['label']}” / Entities satisfying {aliases[spec['selection']]} form the designated group."
    average = nodes[spec["outputs"][-1]]
    average_label = aliases[average["inputs"][1]]
    questions = ["哪些实体属于该组？ / Which entities belong to the designated group?",
                 "该组共有多少个实体？ / How many entities belong to that group?",
                 f"该组的 [{average_label}] 平均值是多少？ / What is their mean [{average_label}]?"]
    return {"rules": lines, "definition": definition, "questions": questions}


def render_chart(path, title, values, evidence_id):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    fontpath = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    font = FontProperties(fname=str(fontpath)) if fontpath.exists() else FontProperties(family="DejaVu Sans")
    with plt.rc_context({"svg.fonttype": "none", "svg.hashsalt": evidence_id}):
        fig, ax = plt.subplots(figsize=(8, 3.6))
        names = list(values)
        bars = ax.bar(names, [values[e] for e in names], color="#356a9b")
        ax.bar_label(bars, labels=[str(values[e]) for e in names], fontsize=8)
        ax.set_title(title, fontproperties=font, fontsize=11)
        ax.tick_params(axis="x", labelrotation=35, labelsize=8)
        fig.tight_layout()
        fig.savefig(path, format="svg", metadata={"Date": None, "Description": evidence_id})
        plt.close(fig)


def audit_rule_semantics(spec, rules):
    """Independent parser of the controlled English clauses, not the formatter.

    Checks operators, operand order, thresholds and inclusive/tied semantics. This
    certifies only this finite grammar, not arbitrary LLM-written business prose.
    """
    nodes = {n["id"]: n for n in spec["nodes"]}
    names = {spec["metric_labels"][n["metric"]]: n["id"] for n in spec["nodes"] if n["op"]=="FACT"}
    checks=[]
    for entry in rules:
        expected=nodes[entry["node_id"]]
        text=entry["text"]
        alias_match=re.match(r"(R\d+): ",text)
        parsed=None
        try:
            m=re.search(r"Metric \[(.*?)\] (>=|<=) (-?\d+(?:\.\d+)?)\.",text)
            if m:
                parsed={"op":"GE" if m[2]==">=" else "LE","inputs":[names[m[1]]],"threshold":float(m[3])}
            m=re.search(r"Metric \[(.*?)\] attains the (maximum|minimum); include all ties\.",text)
            if m: parsed={"op":"MAX" if m[2]=="maximum" else "MIN","inputs":[names[m[1]]]}
            m=re.search(r"Satisfy both (R\d+) and (R\d+)\.",text)
            if m: parsed={"op":"AND","inputs":[names[m[1]],names[m[2]]]}
            m=re.search(r"Satisfy at least one of (R\d+) or (R\d+) \(inclusive OR\)\.",text)
            if m: parsed={"op":"OR","inputs":[names[m[1]],names[m[2]]]}
            m=re.search(r"For each entity: \[(.*?)\] ([+*/-]) \[(.*?)\]\.",text)
            if m: parsed={"op":{'+':'ADD','-':'SUB','*':'MUL','/':'DIV'}[m[2]],"inputs":[names[m[1]],names[m[3]]]}
            passed=bool(alias_match) and parsed=={k:expected[k] for k in ('op','inputs','threshold') if k in expected}
            if alias_match: names[alias_match[1]]=expected['id']
        except KeyError: passed=False
        checks.append({"name":"rule_semantic_roundtrip:"+expected['id'],"passed":passed})
    expected_ids={n['id'] for n in nodes.values() if n['op'] not in {'FACT','COUNT','AVG'}}
    checks.append({"name":"all_required_rules_expressed","passed":{r['node_id'] for r in rules}==expected_ids and len(rules)==len(expected_ids)})
    return checks


def render_bundle(directory, graph, observations=None, *, render_charts=True):
    """Publish only observed facts plus necessary rules; private graph stays outside."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    observations = graph["facts"] if observations is None else observations
    spec = narrative_spec(graph)
    narrative = write_narrative(spec)
    nodes = {n["id"]: n for n in graph["nodes"]}
    labels = {m["id"]: m["label"] for m in graph["metadata"]["metrics"]}
    rules_text = "\n".join([spec["topic"], *(r["text"] for r in narrative["rules"]), narrative["definition"], *narrative["questions"]]) + "\n"
    (directory / "rules.txt").write_text(rules_text, encoding="utf-8")
    evidence = []
    fragments = ["<!doctype html><meta charset='utf-8'><title>Evidence review</title>",
                 "<style>body{max-width:1000px;margin:2em auto;font:17px sans-serif;line-height:1.6}img{width:100%}section{break-inside:avoid}</style>",
                 "<h1>" + html.escape(spec["topic"]) + "</h1>"]
    for rule in narrative["rules"]:
        eid = "rule_" + rule["node_id"]
        fragments.append(f"<p id='{eid}'>" + html.escape(rule["text"]) + "</p>")
        evidence.append({"evidence_id": eid, "node_id": rule["node_id"], "kind": "rule",
                         "location": "report.html#" + eid, "text": rule["text"]})
    fragments.append("<p>" + html.escape(narrative["definition"]) + "</p>")
    for i, (key, values) in enumerate(observations.items(), 1):
        node = nodes[key]
        title = labels.get(node.get("metric"), node.get("label", key))
        eid = "chart_" + key
        filename = f"chart_{i:02d}.svg"
        if render_charts: render_chart(directory / filename, title, values, eid)
        fragments.append(f"<section id='{eid}'><h2>" + html.escape(title) + f"</h2><img src='{filename}' alt='" + html.escape(title, quote=True) + "'></section>")
        evidence.append({"evidence_id": eid, "node_id": key, "kind": "chart", "location": filename,
                         "title": title, "values": values, "entity_order": list(values)})
    fragments.append("<h2>问题 / Questions</h2>" + "".join("<p>" + html.escape(q) + "</p>" for q in narrative["questions"]))
    (directory / "report.html").write_text("\n".join(fragments), encoding="utf-8")
    return {"evidence": evidence, "narrative_spec": spec, "narrative": narrative}


def audit_public(directory, graph, mapping, *, charts=True):
    """Read rendered artifacts back; no claims about unrestricted NLP inference."""
    directory = Path(directory)
    text = (directory / "rules.txt").read_text(encoding="utf-8")
    page = (directory / "report.html").read_text(encoding="utf-8")
    checks = []
    checks.extend(audit_rule_semantics(mapping["narrative_spec"], mapping["narrative"]["rules"]))
    for entry in mapping["evidence"]:
        if entry["kind"] == "rule":
            checks.append({"name": "rule_present:" + entry["node_id"],
                           "passed": entry["text"] in text and html.escape(entry["text"]) in page})
        elif charts:
            path = directory / entry["location"]
            try:
                root = ET.fromstring(path.read_text(encoding="utf-8"))
                rendered = ["".join(element.itertext()) for element in root.iter() if element.tag.endswith("}text")]
                passed = all(e in rendered for e in entry["entity_order"]) and all(str(v) in rendered for v in entry["values"].values())
                passed &= entry["title"] in rendered
                # Check actual bar heights as well as labels: text-only correctness is insufficient.
                ns = "{http://www.w3.org/2000/svg}"
                bars = [g for g in root.iter(ns+"g") if re.fullmatch(r"patch_\d+", g.get("id", ""))]
                bar_paths = [g.find(ns+"path") for g in bars[2:2+len(entry["values"])]]
                heights = []
                for p in bar_paths:
                    coords = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", p.get("d"))]
                    heights.append(abs(coords[1]-coords[5]))
                numeric = list(entry["values"].values())
                scales = [h/v for h,v in zip(heights,numeric) if v]
                passed &= len(heights) == len(numeric) and all(abs(s-scales[0]) < 1e-4 for s in scales)
                passed &= all(abs(h) < 1e-6 for h,v in zip(heights,numeric) if v == 0)
            except (OSError, ET.ParseError, ValueError, TypeError, IndexError, AttributeError):
                passed = False
            checks.append({"name": "chart_readback:" + entry["node_id"], "passed": bool(passed)})
    # Answers and desired membership sets are intentionally absent from writer's input.
    spec = mapping["narrative_spec"]
    checks.append({"name": "writer_has_no_values_or_answers", "passed": not ({"facts", "constraints", "answers", "entities"} & set(spec))})
    checks.append({"name": "narrative_does_not_name_any_entity", "passed": not any(e in text for e in graph["entities"])})
    checks.append({"name": "public_only_allowlisted_files", "passed": all(p.name in {"rules.txt", "report.html"} or re.fullmatch(r"chart_\d+\.svg", p.name) for p in directory.iterdir())})
    return {"passed": all(c["passed"] for c in checks), "checks": checks,
            "scope": "Controlled template rendering and SVG label/bar readback; not manual semantic/visual review or proof against all numerical coincidences."}
