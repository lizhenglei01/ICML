"""Build and verify native Office revisions for a model-authored relational program."""
from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from worldloom.render import RenderError

from .hotel_graph import save_json
from .hotel_office import BUSINESS_FILES, build_layout, native_controls
from .hotel_visibility import _equal, audit_layout
from .program_graph import compile_program, search_revision
from .program_operators import OPERATORS, require
from .program_reference import reference
from .scenario_author import propose_program
from .scenario_bench import draw


def check_reference(graph, old, new):
    sources = tuple(graph['objects']) if 'objects' in graph else (graph['directory']['id'], graph['records']['id'])
    for state in (old, new):
        confirmed = reference(graph, {key: state[key] for key in sources})
        require(state == confirmed, 'SQLite cross-check disagrees with registered graph')


def encode_groups(value):
    if isinstance(value, dict):
        if value and all(isinstance(key, tuple) for key in value):
            return [{'key': list(key), 'rows': encode_groups(rows)} for key, rows in sorted(value.items())]
        return {key: encode_groups(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode_groups(item) for item in value]
    return value


def surfaces(graph, old, new, event):
    directory, records = graph['directory'], graph['records']
    directory_id, records_id = directory['id'], records['id']
    entity, group = directory['entity_key'], directory['group_key']
    roster, report = graph['targets']
    titles = {key: node['label'] for key, node in graph['nodes'].items()}
    fields = {entity: directory['entity_label'], group: directory['group_label'], records['row_key']: '记录编号',
              **{m['id']: f"{m['label']}（{m['unit']}）" for m in records['measures']}}
    slots, previous, current, source_cells, policies = [], {}, {}, {}, {}

    def add(key, file, role, title, was, now, **chart):
        slots.append({'node_id': key, 'file': file, 'label': title, 'role': role, **chart})
        previous[key], current[key] = was, now

    event_key = None
    # Directory records are evidence in the PPT. Entity names are also included
    # in each label so the native table remains readable on its own.
    for file, source, id_field in (('excel', records_id, records['row_key']), ('ppt', directory_id, entity)):
        for index, (prior, revised) in enumerate(zip(old[source], new[source], strict=True)):
            for col, key in enumerate(prior):
                if key == id_field:
                    continue
                node = f'source.{file}.{index}.{col}'
                source_cells[node] = (file, source, index, id_field, prior[id_field], key)
                role = 'output' if prior[key] != revised[key] else 'evidence'
                if role == 'output':
                    require(event_key is None, 'More than one native source cell would change')
                    event_key = node
                add(node, file, role, f'{prior[id_field]} · {fields[key]}', prior[key], revised[key])
    require(event_key is not None, 'No amendable source changed')

    intro = f"本文件为合成内部政策。底表为{titles[records_id]}，主数据为{titles[directory_id]}。"
    field_names = '。'.join(f'{key} 表示 {text}' for key, text in fields.items()) + '。'
    policies['policy.fields'] = intro + field_names
    for i, rule in enumerate(graph['productions'], 1):
        names = [titles[key] for key in rule['inputs']]
        step = OPERATORS[rule['op']].describe(names, rule['program_parameters'])
        policies[f'policy.{i}'] = f"{step}结果记为{titles[rule['output']]}。"
    for index, (key, text) in enumerate(policies.items()):
        add(key, 'word', 'policy', '表与字段说明' if index == 0 else f'复核规则 {index}', text, text)

    for index, name in enumerate(graph['entities']):
        for file in ('excel', 'ppt'):
            chart = {'chart': '资格结果（通过为 1）', 'category': name, 'series': '资格状态（0或1）'} if file == 'ppt' else {}
            add(f'issued.{file}.{index}', file, 'output', f'{name} · 复核资格', name in old[roster], name in new[roster], **chart)
    add('issued.roster', 'word', 'output', '入选名单', ', '.join(sorted(old[roster])), ', '.join(sorted(new[roster])))
    add('issued.count', 'word', 'output', '入选对象数（个）', old[report], new[report])
    task = (f"# {graph['title']}｜{graph['period']}\n\n"
            f"已批准将记录 {event['record']}（{event['entity']}）的{fields[event['field']]}由 {event['before']} 改为 {event['after']}。"
            "依据工作空间中的明细、主数据和复核规则，检查这项更正是否影响其他对象。\n\n"
            f"- 在 {BUSINESS_FILES['excel']} 更正该记录并更新所有受影响的复核资格。\n"
            f"- 在 {BUSINESS_FILES['word']} 更新入选名单和数量。名单按名称 Unicode 顺序排列，用英文逗号和空格分隔。\n"
            f"- 在 {BUSINESS_FILES['ppt']} 更新资格表及原生图表，通过记为 1。\n\n"
            "提交三份原生文件。保留无关记录、原有公式、规则、版式及其他仍然正确的内容。\n")
    return slots, previous, current, task, source_cells, policies, event_key


def read_sources(graph, actual, cell_map, policies, event, event_key, *, revised_source):
    values = {binding['node_id']: binding['value'] for binding in actual}
    bundles = {file: {'observations': {}, 'rules': []} for file in BUSINESS_FILES}
    require(all(values.get(key) == value for key, value in policies.items()), 'Authored policy did not survive Office rendering')
    bundles['word']['rules'].append('policy_gate')
    tables = {}
    for node, (file, source, index, id_field, identity, field) in cell_map.items():
        actual_value = values[node]
        if node == event_key:
            expected = event['after'] if revised_source else event['before']
            require(actual_value == expected, 'Native source does not match stated correction')
            actual_value = event['after']
        table = tables.setdefault((file, source), {})
        table.setdefault(index, {id_field: identity})[field] = actual_value
    for (file, source), rows in tables.items():
        bundles[file]['observations'][source] = tuple(rows[index] for index in sorted(rows))
    return bundles


def build_case(root, graph, seed, *, selection=None, render_surfaces=surfaces, minimum_files=3):
    old, new, event, search = search_revision(graph, seed) if selection is None else selection
    check_reference(graph, old, new)
    slots, before, after, task, cells, policies, event_key = render_surfaces(graph, old, new, event)
    native = build_layout(root, slots, before, after, task, ppt_size='long', title=graph['title'])
    expected = {key: new[key] for key in graph['targets']}
    visibility = None
    for revised_source in (False, True):
        observed = native['after_observations' if revised_source else 'before_observations']
        bundles = read_sources(graph, observed, cells, policies, event, event_key, revised_source=revised_source)
        sources = {key: value for bundle in bundles.values() for key, value in bundle['observations'].items()}
        require(all(sources[key] == new[key] for key in sources), 'Native readback differs from corrected source data')
        proof = audit_layout(graph, bundles, expected, enumerate_paths=True)
        require(proof['passed'] and proof['minimum_files'] == minimum_files,
                'Readback proof does not match the selected evidence layout')
        if visibility is None:
            visibility = proof
        else:
            require(visibility == proof, 'Reference revision changes the evidence used to derive the result')
    controls = native_controls(root, native['oracle'])
    require({'reference', 'unchanged_original', 'unchanged_word', 'wrong_corrected_value',
             'protected_formula_replaced', 'chart_cache_corrupted'} <= controls.keys(), 'Incomplete scoring controls')
    private = root / 'private'
    save_json(private / 'graph.json', graph)
    changes = {key: {'before': old[key], 'after': new[key], 'changed': not _equal(old[key], new[key])}
               for key in new}
    save_json(private / 'truth.json', encode_groups({'event': event, 'search': search, 'nodes': changes}))
    save_json(private / 'visibility.json', encode_groups(visibility))
    save_json(private / 'source_cell_map.json', cells)
    draw(private, graph, '已用独立 SQLite 路径复算所有节点。')
    return {'passed': True, 'level': graph['level'], 'event': event, 'search': search,
            'operator_sequence': [rule['op'] for rule in graph['productions']],
            'sqlite_nodes_checked': 2 * len(graph['productions']),
            'minimum_depth': visibility['minimum_depth'], 'minimum_files': visibility['minimum_files'],
            'required_native_objects': len(native['required_native_objects']),
            'reference_grade': native['reference_grade']['groups'],
            'controls': {key: result['pass'] for key, result in controls.items()}}


def build(topic, goal, level, root, *, seed=8128, attempts=3, reuse=False, effort='low'):
    legacy_replay = reuse and (root / 'proposals/attempt_01/request.json').is_file()
    if not legacy_replay:
        from .unified_office import build as build_unified

        return build_unified(topic, goal, level, root, seed=seed, attempts=attempts, reuse=reuse, effort=effort)
    return build_legacy(topic, goal, level, root, seed=seed, attempts=attempts, reuse=reuse, effort=effort)


def build_legacy(topic, goal, level, root, *, seed=8128, attempts=3, reuse=True, effort='low'):
    if (root / 'case').exists():
        raise ValueError('An accepted case already exists. Choose a new output directory.')
    save_json(root / 'input.json', {'topic': topic, 'goal': goal, 'level': level, 'seed': seed})
    failures = []
    for i in range(1, attempts + 1):
        proposal_dir = root / 'proposals' / f'attempt_{i:02}'
        try:
            proposal = propose_program(topic, goal, level, proposal_dir, reuse=reuse, effort=effort,
                                       feedback=failures[-1]['reason'] if failures else None)
            graph = compile_program(proposal, level)
            with TemporaryDirectory(dir=root, prefix='.program-') as draft:
                pending = Path(draft) / 'case'
                result = build_case(pending, graph, seed + i - 1)
                pending.rename(root / 'case')
            result['attempt'] = i
            result['rejected_proposals'] = failures
            save_json(root / 'summary.json', result)
            return result
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, ArithmeticError, RenderError) as exc:
            failures.append({'attempt': i, 'reason': str(exc)})
            save_json(proposal_dir / 'rejection.json', failures[-1])
            print(f'Level {level} attempt {i} rejected: {exc}', flush=True)
    report = {'passed': False, 'level': level, 'rejected_proposals': failures}
    save_json(root / 'summary.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topic', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--level', type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=8128)
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--effort', default='low')
    args = parser.parse_args()
    result = build(args.topic, args.goal, args.level, args.out, seed=args.seed, attempts=args.attempts,
                   reuse=args.reuse, effort=args.effort)
    if not result['passed']:
        raise SystemExit(1)
    print(f"Level {args.level} passed, depth={result['minimum_depth']}, files={result['minimum_files']}", flush=True)


if __name__ == '__main__':
    main()
