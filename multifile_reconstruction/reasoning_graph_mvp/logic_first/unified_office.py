"""Run staged authorship, choose evidence, and reuse the native Office builder."""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from worldloom.render import RenderError

from .business_view import unit_text
from .hotel_graph import save_json
from .hotel_office import BUSINESS_FILES
from .hotel_visibility import audit_layout
from .program_office import build_case
from .program_operators import OPERATORS, require
from .scenario_author import propose_unified
from .scenario_graph import fields
from .unified_program import (compile_objects, finalize, partial, revisions, schema_description,
                              verify_backward)


def choose_layout(graph, state, event):
    objects = tuple(graph['objects'])
    candidates = [key for key in objects if key != event['source']]
    # Keep the changed record in the workbook. Search table placements for the
    # other evidence, preferring smaller and more readable slide tables.
    choices = [frozenset(ppt) for count in range(len(candidates) + 1) for ppt in combinations(candidates, count)]

    def size(slides):
        return sum(len(state[key]) * len(graph['objects'][key]['fields']) for key in slides)

    targets = {key: state[key] for key in graph['targets']}
    wanted = 3 if graph['level'] >= 3 else 2
    for slides in sorted(choices, key=size):
        if size(slides) > 14:
            continue
        placement = {key: 'ppt' if key in slides else 'excel' for key in objects}
        bundles = {file: {'observations': {key: state[key] for key in objects if placement[key] == file}, 'rules': []}
                   for file in BUSINESS_FILES}
        bundles['word']['rules'].append('policy_gate')
        proof = audit_layout(graph, bundles, targets, enumerate_paths=False)
        if proof['passed'] and proof['minimum_files'] == wanted:
            return placement, wanted
    raise ValueError('No readable Office placement has the requested registered proof footprint')


def surfaces(graph, old, new, event, placement):
    objects = graph['objects']
    roster, *reports = graph['targets']
    titles = {key: node['label'] for key, node in graph['nodes'].items()}
    slots, before, after, cell_map, policies = [], {}, {}, {}, {}

    def add(node, file, role, title, was, now, **chart):
        slots.append({'node_id': node, 'file': file, 'label': title, 'role': role, **chart})
        before[node], after[node] = was, now

    def field_label(spec):
        return f"{spec['label']}（{unit_text(spec['unit'])}）" if spec['kind'] == 'number' else spec['label']

    event_key, event_label = None, None
    for ix, (source, obj) in enumerate(objects.items()):
        file, pk = placement[source], obj['key']['id']
        descriptions = [f"主键 {pk} 表示{obj['key']['label']}。"]
        for spec in obj['fields']:
            text = f"{spec['id']} 表示{field_label(spec)}"
            if spec['kind'] == 'reference':
                parent = objects[spec['target']]
                text += f"，引用{parent['label']}的主键 {parent['key']['id']}"
            descriptions.append(text + '。')
        policies[f'policy.source.{ix}'] = f"{obj['label']}的数据位于 {BUSINESS_FILES[file]}。" + ''.join(descriptions)
        for row_ix, (prior, revised) in enumerate(zip(old[source], new[source], strict=True)):
            for col, spec in enumerate(obj['fields']):
                key, identity = spec['id'], prior[pk]
                node = f'source.{ix}.{row_ix}.{col}'
                cell_map[node] = (file, source, row_ix, pk, identity, key)
                changed = prior[key] != revised[key]
                if changed:
                    require(event_key is None and file == 'excel', 'Expected one changed workbook cell')
                    event_key, event_label = node, field_label(spec)
                label = f"{obj['label']} · {identity} · {field_label(spec)}"
                add(node, file, 'output' if changed else 'evidence', label, prior[key], revised[key])
    require(event_key, 'Missing amendment in native evidence')
    policies['policy.scope'] = '本轮为合成内部政策。指标按精确值计算，发布汇总显示最多四位小数，四舍六入五成双。'
    for i, rule in enumerate(graph['productions'], 1):
        step = OPERATORS[rule['op']].describe([titles[key] for key in rule['inputs']], rule['program_parameters'])
        policies[f'policy.step.{i}'] = f"{step}结果记为{titles[rule['output']]}。"
    for index, (key, text) in enumerate(policies.items(), 1):
        add(key, 'word', 'policy', f'复核口径 {index}', text, text)
    for i, entity in enumerate(graph['entities']):
        for file in ('excel', 'ppt'):
            chart = {'chart': '资格状态（通过为 1）', 'category': entity, 'series': '资格状态（0或1）'} if file == 'ppt' else {}
            add(f'issued.{file}.{i}', file, 'output', f'{entity} · 资格状态', entity in old[roster], entity in new[roster], **chart)
    add('issued.roster', 'word', 'output', titles[roster], ', '.join(sorted(old[roster])), ', '.join(sorted(new[roster])))
    for i, key in enumerate(reports):
        unit = graph['nodes'][key]['unit']
        suffix = f'（{unit_text(unit)}）' if unit else ''
        add(f'issued.report.{i}', 'word', 'output', titles[key] + suffix, round(old[key], 4), round(new[key], 4))
    task = (f"# {graph['title']}｜{graph['period']}\n\n"
            f"已批准更正{titles[event['source']]}中的 {event['record']}（对应 {event['entity']}）。"
            f"将{event_label}由 {event['before']} 改为 {event['after']}，根据工作空间资料重做复核。\n\n"
            f"- 在 {BUSINESS_FILES['excel']} 更正台账并更新受影响的资格。\n"
            f"- 在 {BUSINESS_FILES['word']} 更新{titles[roster]}和{'、'.join(titles[key] for key in reports)}。"
            "名单按 Unicode 升序排列，用英文逗号加空格分隔。\n"
            f"- 在 {BUSINESS_FILES['ppt']} 更新资格表和原生图表（通过记为1）。\n\n"
            '提交三份原生文件，保留其他正确记录、政策、公式和版式。\n')
    return slots, before, after, task, cell_map, policies, event_key


def build(topic, goal, level, root, *, seed=8128, attempts=3, reuse=False, effort='low'):
    root = Path(root)
    require(not (root / 'case').exists(), 'Choose a new output directory for an accepted case')
    require(level in {1, 2, 3, 4} and attempts > 0, 'Invalid level or attempt count')
    settings = {'topic': topic, 'goal': goal, 'level': level, 'seed': seed, 'builder': 'unified'}
    if (root / 'input.json').is_file():
        require(json.loads((root / 'input.json').read_text()) == settings, 'Output directory already belongs to another run')
    save_json(root / 'input.json', settings)
    failures = []
    for i in range(1, attempts + 1):
        proposal_root = root / 'proposals'
        if i > 1:
            proposal_root = proposal_root / f'attempt_{i:02}'

        def author(stage, context):
            directory = proposal_root / stage
            return propose_unified(stage, topic, goal, level, context, directory,
                                   reuse=reuse or (directory / 'proposal.json').is_file(), effort=effort)

        stage = 'observations'
        try:
            observations = author(stage, {'previous_validation': failures[-1]} if failures else {})
            base, schemas = compile_objects(observations)
            stage = 'forward'
            forward = author(stage, observations)
            fields(forward, 'nodes', 'target', 'definition')
            output, _ = partial(base, schemas, forward)
            stage = 'backward'
            backward = author(stage, {'observations': observations,
                                      'target': {'definition': forward['definition'], 'schema': schema_description(output)}})
            fields(backward, 'nodes', 'target')
            stage = 'backward_verification'
            verification = verify_backward(base, schemas, forward, backward, seed)
            save_json(proposal_root / 'backward_verification.json', verification)
            stage = 'policy'
            policy = author(stage, {'observations': observations, 'forward': forward, 'forward_target': schema_description(output)})
            graph = finalize(base, schemas, forward, policy, level)
            save_json(proposal_root / 'compiled_graph.json', graph)
            stage = 'revision_and_layout'
            selection = next(revisions(graph, seed + i - 1))
            placement, minimum = choose_layout(graph, selection[1], selection[2])
            stage = 'native_package'
            with TemporaryDirectory(dir=root, prefix='.program-') as draft:
                pending = Path(draft) / 'case'
                result = build_case(pending, graph, seed, selection=selection, minimum_files=minimum,
                                    render_surfaces=lambda *args: surfaces(*args, placement))
                pending.rename(root / 'case')
            result.update(builder='unified', attempt=i, table_count=len(base['objects']), layout=placement,
                          backward_verification=verification, rejected_proposals=failures)
            save_json(root / 'summary.json', result)
            return result
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, ArithmeticError, RenderError, subprocess.SubprocessError) as exc:
            failures.append({'attempt': i, 'stage': stage, 'reason': str(exc)})
            save_json(proposal_root / 'rejection.json', failures[-1])
            print(f'Unified attempt {i} rejected at {stage}: {exc}', flush=True)
    report = {'passed': False, 'level': level, 'builder': 'unified', 'rejected_proposals': failures}
    save_json(root / 'summary.json', report)
    return report
