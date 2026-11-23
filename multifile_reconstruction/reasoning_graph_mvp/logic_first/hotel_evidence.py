"""Build and audit the first native hotel evidence/propagation experiment."""
import argparse
from fractions import Fraction
import html
from pathlib import Path

from .business_view import export_diagram
from .hotel_graph import (ARTIFACTS, FORMULA_TEXT, GATE_TEXT, METRIC_LABELS, RULES, TARGETS,
                          build_graph, case_record, save_json)
from .hotel_office import FILES, FORMAT_NAMES, active_oracle_path, build_layout, native_controls
from .hotel_visibility import audit_layout, choose_layouts, closed_paths, solve_public


DEFAULT = ARTIFACTS / 'hotel_evidence'
INVENTORY = {
    'occupancy_rate': {'kind': 'derived', 'cross_files': ['excel']},
    'average_daily_rate': {'kind': 'derived', 'cross_files': ['ppt', 'excel']},
    'revpar': {'kind': 'derived', 'cross_files': ['ppt', 'excel']},
    'sold_room_nights': {'kind': 'base', 'cross_files': ['excel', 'ppt']},
    'available_room_nights': {'kind': 'base', 'cross_files': ['excel']},
    'room_revenue': {'kind': 'base', 'cross_files': ['ppt', 'excel']},
    'occupancy_floor': {'kind': 'policy', 'cross_files': ['word']},
    'adr_floor': {'kind': 'policy', 'cross_files': ['word']},
    'policy_gate': {'kind': 'rule', 'cross_files': ['word']},
}
for channels in INVENTORY.values():
    channels['central_files'] = ['excel']


def instruction(event):
    hotel = event['entity']
    return f'''# 2025 年第四季度客房经营复核 / Q4 2025 Hotel Operations Review

已批准一项更正。{hotel} 的已售间夜数由 {event['old_value']:,} 更正为 {event['new_value']:,}，客房收入、可售间夜数及其他酒店的记录保持原口径。文件中的经营事实是可用于本次复核的最新资料。三份文件中原有的复核结果、业务结论和资格图仍为此前发布版本。

请根据文件中的复核口径重新核对每家酒店。将 Excel 的已登记间夜数更正，并更新需要变化的资格状态。更新 Word 的入选名单、家数以及入选酒店平均房价的算术平均值（各酒店等权）。同步修正 PPT 资格图的数据表和原生图表数据，保留图表的可编辑性。名单中多个名称用英文逗号和空格隔开。现有格式、公式、经营事实、复核口径和不受影响的记录应予保留。

The approved adjustment changes {hotel}'s sold room nights from {event['old_value']:,} to {event['new_value']:,}. Room revenue, available room nights, and other hotels' records are unchanged. Operating records in the files are current source evidence. The editable review results, narrative conclusions and qualification chart still reflect the previous release.

Apply the review terms found in the files to each hotel. Correct the posted sold room nights and any affected hotel status in Excel. Update the names, count and unweighted mean of qualifying hotels' ADR in Word. Synchronize the qualification chart's displayed table and editable native chart data in PowerPoint. Separate multiple names with a comma and space. Preserve existing formatting, formulas, source facts, review terms, and unaffected records. Save the changes in the original three Office files.
'''


def make_slots(graph, case, layout, *, metric_labels=METRIC_LABELS, gate_text=GATE_TEXT,
               formula_text=FORMULA_TEXT, extra_word_results=()):
    slots, old_surfaces, new_surfaces = [], {}, {}
    before, after = case['before'], case['after']

    def add(file, node, label, role, old, new, *, entity=None, chart=None, history=None):
        key = f'{role}.{node}' + (f'.{graph["entities"].index(entity):02}' if entity is not None else '')
        slot = {'node_id': key, 'semantic_node_id': node, 'file': file, 'label': label, 'role': role}
        if entity is not None:
            slot['entity'] = entity
        if chart is not None:
            slot.update(chart=chart, category=entity, series=metric_labels.get(node, '入选 (1) / Qualified (1)'))
        if history:
            slot['history_id'] = history
        slots.append(slot)
        old_surfaces[key], new_surfaces[key] = old, new

    policy_file = None
    for file, bundle in layout['files'].items():
        for node, value in bundle['observations'].items():
            if isinstance(value, dict):
                for entity in graph['entities']:
                    add(file, node, f'{entity} · {metric_labels[node]}', 'evidence', value[entity], value[entity],
                        entity=entity, chart=metric_labels[node] if file == 'ppt' else None)
            else:
                add(file, node, metric_labels[node], 'policy', value, value)
        if 'policy_gate' in bundle['rules']:
            policy_file = file
            add(file, 'policy_gate', '入选条件 / Qualification rule', 'policy', gate_text, gate_text)
    if policy_file is None:
        raise ValueError('Review terms need a public source')
    add(policy_file, 'metric_definitions', '指标口径 / Metric definitions', 'context', formula_text, formula_text)

    entity = case['event']['entity']
    add('excel', 'sold_room_nights', f'{entity} · 已登记已售间夜数 / Posted sold room nights', 'output',
        before['sold_room_nights'][entity], after['sold_room_nights'][entity], history='prior_posted_sold')
    for e in graph['entities']:
        for file in ('excel', 'ppt'):
            add(file, 'qualified', f'{e} · 复核资格 / Qualification', 'output', e in before['qualified'], e in after['qualified'],
                entity=e, chart='酒店复核资格 / Hotel qualification' if file == 'ppt' else None,
                history='prior_qualification_flags')
    add('word', 'qualified', '入选酒店 / Qualifying hotels', 'output', ', '.join(sorted(before['qualified'])),
        ', '.join(sorted(after['qualified'])), history='prior_qualified_names')
    add('word', 'qualified_count', '入选家数 / Qualified hotel count', 'output', before['qualified_count'], after['qualified_count'],
        history='prior_qualified_count')
    add('word', 'qualified_adr_mean', '入选酒店平均房价的算术平均（元／间夜） / Unweighted mean ADR (CNY per room night)',
        'output', before['qualified_adr_mean'], after['qualified_adr_mean'], history='prior_qualified_adr_mean')
    for node, label in extra_word_results:
        def surface(value):
            return ', '.join(sorted(value)) if isinstance(value, frozenset) else value
        add('word', node, label, 'output', surface(before[node]), surface(after[node]), history='prior_' + node)
    return slots, old_surfaces, new_surfaces


def native_bundles(graph, records, *, include_prior, gate_text=GATE_TEXT, formula_text=FORMULA_TEXT):
    bundles = {key: {'observations': {}, 'rules': []} for key in FILES}
    for record in records:
        bundle = bundles[FORMAT_NAMES[record['file']]]
        node = record['semantic_node_id']
        value = record['value']
        if record['role'] == 'context':
            if node == 'metric_definitions' and value != formula_text:
                raise ValueError('Arithmetic definitions differ from the supported contract')
            continue
        if node == 'policy_gate':
            if value != gate_text:
                raise ValueError('Qualification rule differs from the graph')
            bundle['rules'].append(node)
            continue
        if record['role'] == 'output':
            if not include_prior:
                continue
            node = record['history_id']
        if type(value) is float:
            value = Fraction(str(value))
        observed = bundle['observations']
        if 'entity' in record:
            vector = observed.setdefault(node, {})
            entity = record['entity']
            if entity in vector and vector[entity] != value:
                raise ValueError('Native replicas disagree')
            vector[entity] = value
        else:
            if node in observed and observed[node] != value:
                raise ValueError('Native replicas disagree')
            observed[node] = value
    for bundle in bundles.values():
        for value in bundle['observations'].values():
            if isinstance(value, dict) and set(value) != set(graph['entities']):
                raise ValueError('Incomplete observed metric vector')
    return bundles


def draw_overview(directory, graph, case, title='酒店复核｜统一可执行依赖图'):
    kinds = {'base': 'evidence', 'derived': 'set', 'arithmetic': 'set', 'policy': 'evidence',
             'rule': 'evidence', 'selection': 'set', 'result': 'answer'}
    nodes = []
    for node in graph['nodes']:
        label = node['meaning'].split(' / ')[0]
        if node['id'] == 'policy_gate':
            label = node.get('diagram_label', '政策：两个门槛同时满足')
        if node['id'] == case['event']['node_id']:
            label += f'\n{case["event"]["old_value"]:,} → {case["event"]["new_value"]:,}（更正酒店）'
        if node['id'] in graph['targets']:
            old, new = (case[when][node['id']] for when in ('before', 'after'))
            label += '\n'+ (f'{len(old)} → {len(new)} 家' if isinstance(old, frozenset) else f'{old} → {new}')
        nodes.append({'id': node['id'], 'kind': kinds[node['kind']], 'label': label})
    edges = []
    for rule in graph['productions']:
        identifier = 'operator.' + rule['id']
        op = rule['op']
        nodes.append({'id': identifier, 'kind': 'logic' if op in ('AND', 'OR') else 'rule', 'label': op})
        for index, key in enumerate(rule['inputs']):
            label = ('分子', '分母')[index] if op == 'DIV' else ''
            edges.append({'source': key, 'target': identifier, 'label': label})
        for key in rule.get('requires', []):
            edges.append({'source': key, 'target': identifier, 'label': '政策依据'})
        edges.append({'source': identifier, 'target': rule['output'], 'label': ''})
    export_diagram(directory, title, nodes, edges,
                   '同一指标可有多条路径。图中保留了替代公式；单次推导使用有来源且无循环的路径。')


def build(root=DEFAULT):
    root = Path(root)
    graph = build_graph()
    case = case_record(graph)
    expected = {node: case['after'][node] for node in TARGETS}
    event_obs = {'observations': {'event_new_sold': case['event']['new_value']}, 'rules': []}
    layouts = choose_layouts(graph, case['after'], INVENTORY, expected, file_names=tuple(FILES), common=event_obs)
    root.mkdir(parents=True, exist_ok=True)
    save_json(root / 'private/graph.json', graph)
    save_json(root / 'private/case.json', case)
    save_json(root / 'private/evidence_inventory.json', INVENTORY)
    draw_overview(root / 'private', graph, case)
    full_base = case['after_basis']
    revpar = solve_public(graph, full_base, RULES, ['revpar'])
    path_report = closed_paths(revpar, full_base, ['revpar'])
    save_json(root / 'private/revpar_paths.json', path_report)
    summary = {'passed': False, 'layouts': {}, 'shared_assets': {'graph': 'private/graph.json', 'case': 'private/case.json'},
               'revpar_minimum_depth_from_base': path_report['revpar']['minimum_depth'],
               'origins': {
                   'manual': 'Business definition registry, hotel correction, review thresholds, admissible evidence placements and native edit contract.',
                   'model': 'Forward exploration, earlier one-shot formula generation and earlier goal-driven alternatives. The input artifacts name actual runs.',
                   'program': 'Formula verification, canonical graph assembly, layout search, exact counterworlds, native rendering/readback, grading and controls.',
               }, 'input_artifacts': graph['input_artifacts']}
    for name, layout in layouts.items():
        package = root / 'layouts' / name
        slots, before, after = make_slots(graph, case, layout)
        native = build_layout(package, slots, before, after, instruction(case['event']))
        controls = native_controls(package, native['oracle'])
        actual = native_bundles(graph, native['before_observations'], include_prior=True)
        if native['oracle'].get('count_formula'):
            formula = native['oracle']['count_formula']
            from openpyxl import load_workbook
            workbook = load_workbook(package / 'public' / formula['file'], data_only=True, read_only=True)
            try:
                actual['excel']['observations']['prior_qualified_count'] = workbook[formula['sheet']][formula['cell']].value
            finally:
                workbook.close()
        audit = audit_layout(graph, actual, expected, common=event_obs, worlds=case['counterworlds'])
        is_cross = name.endswith('cross_file')
        expected_files = 3 if is_cross else 1
        target_depths = {k: v['depth'] for k, v in audit['proof']['proofs'].items()}
        if not audit['passed'] or audit['minimum_files'] != expected_files:
            raise ValueError(f'Rendered evidence changed solvability or file necessity for {name}')
        if audit['minimum_depth'] != layout['audit']['minimum_depth']:
            raise ValueError(f'Rendered evidence changed proof depth for {name}')
        for subset in audit['subsets']:
            if not subset['sufficient'] and (subset['counterworld'] is None or
                                             set(subset['counterworld']['changed_answers']) != set(TARGETS)):
                raise ValueError(f'Missing indistinguishable counterworld in {name}: {subset["files"]}')
        reference = native_bundles(graph, native['after_observations'], include_prior=False)
        reference_audit = audit_layout(graph, reference, expected, common=event_obs, enumerate_paths=False)
        if not reference_audit['passed'] or reference_audit['minimum_depth'] != audit['minimum_depth']:
            raise ValueError('Reference source evidence differs from the original')
        save_json(package / 'private/visibility.json', audit)
        save_json(package / 'private/placement.json', layout['files'])
        save_json(package / 'private/native_semantic_delta.json', [
            {**slot, 'before': before[slot['node_id']], 'after': after[slot['node_id']]}
            for slot in slots if before[slot['node_id']] != after[slot['node_id']]
        ])
        summary['layouts'][name] = {'passed': native['reference_grade']['pass'], 'necessary_files': audit['necessary_files'],
                                    'minimum_files': audit['minimum_files'], 'minimum_depth': audit['minimum_depth'],
                                    'target_depths': target_depths, 'selected_evidence': layout['evidence_ids'],
                                    'required_native_objects': native['required_native_objects'],
                                    'reference_grade': native['reference_grade']['groups'],
                                    'controls': {k: v['pass'] for k, v in controls.items()},
                                    'subsets_with_witness': sum(s.get('counterworld') is not None for s in audit['subsets'])}
        print(f'{name} depth={audit["minimum_depth"]} files={audit["minimum_files"]}', flush=True)
    by_name = summary['layouts']
    for suffix in ('concentrated', 'cross_file'):
        if by_name['deep_' + suffix]['minimum_depth'] <= by_name['shallow_' + suffix]['minimum_depth']:
            raise ValueError('Deep variant did not require a longer proof')
    for prefix in ('shallow', 'deep'):
        if by_name[prefix + '_concentrated']['target_depths'] != by_name[prefix + '_cross_file']['target_depths']:
            raise ValueError('Moving evidence changed proof depth')
    summary['passed'] = all(item['passed'] for item in by_name.values())
    write_index(root, summary)
    save_json(root / 'summary.json', summary)
    return summary


def write_index(root, summary):
    labels = {'shallow_concentrated': '浅层 · 集中', 'shallow_cross_file': '浅层 · 跨文件',
              'deep_concentrated': '深层 · 集中', 'deep_cross_file': '深层 · 跨文件'}
    rows = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>酒店经营复核实验</title>',
            '<style>body{font:17px system-ui;line-height:1.7;max-width:950px;margin:2rem auto;padding:0 1rem} '
            'table{border-collapse:collapse}th,td{border-bottom:1px solid #ddd;padding:.5em;text-align:left}</style>',
            '<h1>酒店经营复核实验</h1><p>四种布局使用同一业务场景、同一更正和同一组问题。</p>',
            '<p><a href="private/dependency_graph.svg">依赖图</a> · <a href="private/graph.json">完整图数据</a> · '
            '<a href="private/revpar_paths.json">RevPAR 路径</a> · <a href="summary.json">验证总表</a></p>',
            '<table><tr><th>布局</th><th>入选名单深度</th><th>最少文件</th><th>公开文件</th><th>参考文件与检查</th></tr>']
    for name, result in summary['layouts'].items():
        prefix = f'layouts/{name}'
        public = ' · '.join(f'<a href="{prefix}/public/{file}">{label}</a>'
                            for file, label in zip(FILES.values(), ('Excel', 'Word', 'PPT')))
        public += f' · <a href="{prefix}/public/instruction.md">任务</a>'
        references = ' · '.join(f'<a href="{prefix}/private/reference/{file}">{label}</a>'
                                for file, label in zip(FILES.values(), ('Excel', 'Word', 'PPT')))
        oracle_file = active_oracle_path(root / prefix / 'private').name
        references += (f'<br><a href="{prefix}/private/visibility.json">证据验证</a> · '
                       f'<a href="{prefix}/private/{oracle_file}">Oracle</a> · '
                       f'<a href="{prefix}/private/evidence_map.json">位置映射</a>')
        rows.append(f'<tr><td>{html.escape(labels[name])}</td><td>{result["target_depths"]["qualified"]}</td>'
                    f'<td>{result["minimum_files"]}</td><td>{public}</td><td>{references}</td></tr>')
    (root / 'index.html').write_text('\n'.join(rows + ['</table></html>']), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output)
    print(args.output / 'summary.json')


if __name__ == '__main__':
    main()
