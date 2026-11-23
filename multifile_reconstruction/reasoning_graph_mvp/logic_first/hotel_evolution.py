"""Build a hotel workbook whose existing approval interface must evolve in place."""
from __future__ import annotations

import argparse
from pathlib import Path
from shutil import copyfile, copytree
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from .hotel_evolution_cases import formula_spec
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_office import FILES, build_layout, digest, grade, snapshot, structural_key
from .hotel_publication import workbook_signature
from .hotel_revenue_milestones import ANALYSIS, CONFIRMED, HARBOR, WILLOW, add_analysis, cases, phase_slots, roster, source_cells
from .hotel_revenue_milestones import instruction as analysis_instruction
from .hotel_revenue_sequence import OFFICE_FILES, prepare_stage_two


DEFAULT = ARTIFACTS / 'hotel_evolution'
APPROVAL = '审批办理'
CAPABILITIES = ('operating_metrics', 'new_contract_approval', 'renewal_at_the_same_approval_interface')
ARCHIVE_TERMS = (
    '历史正式签约名单与家数按签发时的决定存档。下一轮审查使用当轮批准的政策。 / '
    'The issued signing roster and count are historical records. Each later review uses the policy authorized for that phase.'
)
NEW_TEST = 'AND(B4>=$H$2,B5>=$H$3)'
RENEW_TEST = 'AND(B4>=$H$2,B5>=$H$4,INDEX($E$3:$E$8,MATCH(B3,$D$3:$D$8,0))=1)'


def instruction(stage, event):
    if stage == 1:
        return analysis_instruction(1, None)
    if stage == 2:
        names = roster(CONFIRMED)
        return f'''# 第二轮 · 建立新签审批入口 / Milestone 2 · Build the signing approval interface

在 Excel 已预留的「审批办理」表建立可重算的新签功能。用户只需在 B3 换一个酒店名称。让 B4、B5 返回第一轮「酒店指标」中同一家酒店的入住率和平均房价。B6 保持「新签」。H2 写入本轮批准的入住率线 75%（小数），H3 写入房价线 125 元。B8 是此入口的唯一审批结果，用原生公式返回 1 或 0，两项均达到门槛才通过，包含等号。审批应随 B3、第一轮计算结果或门槛输入变化。保留位置和用途，允许重组内部公式。H4 和 E3:E8 暂留原样。

发布本轮结果。Excel 只更新「复核报表」已有资格标记和历史签约家数。当前资格可填结果或用公式，历史签约家数填固定值。Word 更新已有的价格对照组、对照组均价、房价线、入选名单、家数、入选均价与历史签约名单。PPT 只更新已有资格表和对应可编辑图表。对照组是入住率低于 75% 的酒店，Word 的既有口径继续适用。委员会已收到正式签约回执的酒店是 {names}，只有其中符合新签条件的才列入历史签约名单。英文名称按字母排序，以逗号和空格分隔。保留第一轮的计算功能和其他现有材料。

Build the live new-contract approval interface on the provided 审批办理 sheet. A caller selects any hotel by setting B3. Use formulas in B4 and B5 to retrieve that hotel's occupancy and ADR from the existing 酒店指标 analysis sheet. Leave B6 as 新签. Set H2 to the authorized occupancy floor (75% as a decimal) and H3 to the authorized signing ADR floor (125 yuan). B8 is the single verdict. Return a native 1 or 0 using both thresholds, inclusive of equality. The verdict must recalculate when the selector, earlier metrics, or thresholds change. Retain the interface positions and roles. You may reorganize the calculation internals. Leave H4 and E3:E8 as provided for now.

Publish the results in existing fields. In Excel update the issued-review qualification flags and historical signing count. The current Excel qualification fields may contain values or formulas. Save the historical signing count as a fixed value. In Word update the peer roster, peer mean, rate hurdle, qualifying names, count, mean qualifying ADR, and historical signing roster. In PowerPoint update just the existing qualification table and its editable chart. The price peers are hotels below 75% occupancy. Use Word's existing calculation terms. Signed replies came from {names}. Sign only the currently eligible hotels from that list. Use alphabetical names separated by comma and space. Preserve the first milestone's calculation functionality and unrelated material.
'''
    return f'''# 第三轮 · 升级同一个审批入口以支持续约 / Milestone 3 · Extend the same approval interface

已将 {event['entity']} 已售间夜从 {event['old_value']:,} 更正为 {event['new_value']:,}。Excel 来源记录已更新。更正已登记间夜，并升级现有「审批办理」入口。用户继续通过 B3 选酒店、B8 取同一个 1／0 结果。B6 增加支持「续约」，交付时保持默认「新签」。B4、B5 继续连接第一轮指标。两种模式都用 H2 的入住率门槛；新签沿用 H3；续约要求当前房价达到 H4 的新门槛 120 元并已有正式签约记录。按 Excel D3:D8 的酒店顺序，把 Word 已签约名单同步为 E3:E8 的 1／0，作为可更新的历史输入。保留 Word 里的历史签约名单和 Excel 的历史签约家数。

同步本次发布结果。Excel 的原资格标记、Word 的原复核结论和 PPT 原资格表与可编辑图表仍展示新签资格。Word 的本次续约名单及 Excel 和 PPT 的本次续约家数，把同一个入口切到续约规则得到各酒店结果后再汇总。Excel 本轮资格标记和续约家数可填结果，也可用公式。历史签约名单及家数是固定档案，后续改变业务输入时仍须保持原值。H4 的 G4 说明可改为「续约房价线 / Renewal ADR floor」。名单按字母排序、以英文逗号与空格分隔。两种模式必须共享 B8 入口，并随输入重算。可以改原公式和内部组织，保留原有输入输出位置、其他材料及原计算与新签的行为。

The approved sold-room-night correction for {event['entity']} is {event['old_value']:,} to {event['new_value']:,}; the Excel source already reflects it. Correct its posted amount. Upgrade the existing 审批办理 workflow. Callers continue to select the hotel in B3 and get the one numeric verdict in B8. Add B6 mode 续约 while keeping the delivered default 新签. Keep B4 and B5 tied to the first milestone’s metrics. Both modes use the occupancy floor in H2. New signings retain the H3 ADR floor. Renewals require the new H4 floor of 120 yuan and a prior signing. Put 1/0 history flags in E3:E8 using the historical Word roster and the hotel names in D3:D8. Treat those flags as live data. Preserve the official Word signing roster and Excel signing count.

Publish to existing fields. Original qualification flags, Word review conclusions, and the PPT qualification table and editable chart continue to represent new-signing eligibility. Update the Word renewal roster and renewal counts in Excel and PPT from the renewal-mode results for the hotels. The current Excel qualifications and renewal count may be values or formulas. The historical signing roster and count are fixed records and must retain their issued values after business inputs change. The G4 label for H4 may read '续约房价线 / Renewal ADR floor'. Use alphabetical names separated by comma and space. Support both modes through the same B8 interface and recalculate when inputs change. You may refactor the earlier formulas and internal organization. Preserve the existing inputs, outputs, unrelated content, and earlier calculation and signing behavior.
'''


def add_approval(path, entities, *, level, approved=()):
    book = load_workbook(path)
    ws = book.create_sheet(APPROVAL)
    ws['A1'] = '酒店审批办理 / Hotel approval'
    for row, label in {3: '酒店 / Hotel', 4: '入住率 / Occupancy', 5: '平均房价 / ADR', 6: '审核类型 / Mode', 8: '办理结果 1/0 / Verdict'}.items():
        ws[f'A{row}'] = label
    ws['B3'], ws['B6'] = WILLOW, '新签'
    ws['B4'].number_format = '0.00%'
    ws['B5'].number_format = '0.00'
    ws['B8'].number_format = '0'
    ws['G2'], ws['G3'], ws['G4'] = '入住率线 / Occupancy floor', '新签房价线 / Signing ADR floor', '补充房价线 / Supplemental ADR floor'
    ws['H2'], ws['H3'], ws['H4'] = 0, 0, 0
    ws['H2'].number_format = '0.00%'
    ws['D2'], ws['E2'] = '酒店 / Hotel', '签约历史 1/0 / Signing history'
    for row, name in enumerate(entities, 3):
        ws[f'D{row}'], ws[f'E{row}'] = name, int(level == 3 and name in approved)
    if level >= 2:
        selected = f"MATCH(B3,'{ANALYSIS}'!$A$2:$A$7,0)"
        ws['B4'] = f"=INDEX('{ANALYSIS}'!$C$2:$C$7,{selected})"
        ws['B5'] = f"=INDEX('{ANALYSIS}'!$D$2:$D$7,{selected})"
        ws['H2'], ws['H3'] = 0.75, 125
        ws['B8'] = f'=IF({NEW_TEST},1,0)'
    if level == 3:
        ws['H4'] = 120
        ws['B8'] = f'=IF(IF(B6="续约",{RENEW_TEST},{NEW_TEST}),1,0)'
    for col, width in zip('ABDEGH', (30, 30, 32, 27, 31, 18), strict=True):
        ws.column_dimensions[col].width = width
    book.save(path)
    book.close()


def prepare_stage(submission, destination, package):
    submission, destination, package = map(Path, (submission, destination, package))
    meta = read_json(package / 'private/milestone.json')
    if meta['stage'] == 3:
        return prepare_stage_two(submission, destination, package)
    if meta['stage'] != 2 or any(not (submission / f).is_file() or (submission / f).is_symlink() for f in OFFICE_FILES):
        raise ValueError('Milestone two requires the preceding Office files')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='evolution_handoff_', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'work'
        staged.mkdir()
        for name in OFFICE_FILES:
            copyfile(submission / name, staged / name)
        copyfile(package / 'public/instruction.md', staged / 'instruction.md')
        add_approval(staged / FILES['excel'], meta['entities'], level=1)
        before, after = snapshot(submission, OFFICE_FILES), snapshot(staged, OFFICE_FILES)
        prefix = FILES['excel'] + ':sheet:' + APPROVAL + ':'
        workbook = FILES['excel'] + ':'
        allowed = {workbook + key for key in ('package_parts', 'sheets', 'protected_part:[Content_Types].xml',
            'protected_part:xl/workbook.xml', 'protected_part:xl/_rels/workbook.xml.rels', 'protected_part:xl/styles.xml')}
        delta = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        if any(key not in allowed and not key.startswith(prefix) for key in delta):
            raise ValueError('Adding the authorized blank approval interface changed prior work')
        staged.rename(destination)
    return {'event': None, 'scaffold_added': APPROVAL, 'changed_native_objects': delta,
            'unchanged_file_bytes': [FILES['word'], FILES['ppt']]}


def publication_contract(reference, oracle, stage):
    expected = {check['object']: check['expected'] for check in oracle['checks']}
    workbook = FILES['excel']
    fields = {'qualified', 'renewal_count'} if stage == 3 else {'qualified'}
    cells = []
    for binding in oracle['bindings']:
        if binding['file'] == workbook and binding['role'] == 'output' and binding['semantic_node_id'] in fields:
            key, = structural_key(reference, binding)
            cells.append({**binding['location'], 'object': key, 'evidence_id': binding['evidence_id']})
    label_key, = structural_key(reference, {'file': workbook, 'location': {'kind': 'cell', 'sheet': APPROVAL, 'cell': 'G4'}})
    return {
        'file': workbook,
        'cells': cells,
        'reference_workbook_part_hash': expected[workbook + ':protected_part:xl/workbook.xml'],
        'default_calc_workbook_signature': workbook_signature(reference / workbook),
        'alternate_labels': {label_key: {'expected': expected[label_key]['value'],
            'alternatives': ['续约房价线 / Renewal ADR floor']}} if stage == 3 else {},
    }


def build_phase(package, graph, opening, corrected, approved, renewed, stage):
    slots, before, after = phase_slots(graph, opening, corrected, approved, renewed, stage)
    for slot in slots:
        if slot['semantic_node_id'] == 'renewal_policy':
            before[slot['node_id']] = after[slot['node_id']] = ARCHIVE_TERMS
    raw = corrected if stage == 3 else opening
    product = build_layout(package, slots, before, after, instruction(stage, raw['event']), ppt_size='medium')
    oracle = product['oracle']
    public, private = package / 'public', package / 'private'
    reference = private / 'reference'
    sources = source_cells(oracle)
    for directory, level in ((public, stage - 1), (reference, stage)):
        workbook = directory / FILES['excel']
        add_analysis(workbook, graph, raw['after'], sources, int(level >= 1), with_qualification=False)
        if stage >= 2:
            add_approval(workbook, graph['entities'], level=level, approved=approved)
    initial, final = snapshot(public, OFFICE_FILES), snapshot(reference, OFFICE_FILES)
    if initial.keys() != final.keys():
        raise ValueError(f'{package.name} changed prepared object inventory')
    scenarios, origins = formula_spec(graph, raw['after'], sources, stage, approved=approved)
    objects = {}
    probe_origins = {}
    for scenario in scenarios:
        for point in scenario['expected']:
            address = f"{point['sheet']}!{point['cell']}"
            if address not in objects:
                objects[address], = structural_key(reference, {'file': FILES['excel'], 'location': {
                    'kind': 'cell', 'sheet': point['sheet'], 'cell': point['cell']}})
            probe_origins[f"functional_excel:{scenario['name']}:{address}"] = point.get('milestone', scenario.get('milestone', 1))
    oracle['functional_excel'] = {'file': FILES['excel'], 'objects': {key: address for address, key in objects.items()},
        'scenarios': scenarios, 'allow_text_inputs': stage >= 2, 'flexible_formula_cells': True}
    areas = [f'{FILES["excel"]}:sheet:{sheet}:' for sheet in ([ANALYSIS, APPROVAL] if stage >= 2 else [ANALYSIS])]
    oracle['allow_helpers_in'] = areas
    oracle['checks'] = [{'object': key, 'expected': final[key], 'group': 'required' if value != final[key] else 'protected'}
                        for key, value in sorted(initial.items()) if key not in {area + 'structure' for area in areas}]
    if stage >= 2:
        oracle['excel_publication'] = publication_contract(reference, oracle, stage)
    save_json(private / 'oracle.json', oracle)
    scores = grade(reference, read_json(private / 'oracle.json'))
    if not scores['pass']:
        raise ValueError(f'{package.name} reference failed: {[check for check in scores["checks"] if not check["pass"]]}')
    save_json(private / 'native_reference_evaluation.json', scores)
    manifest = read_json(private / 'native_manifest.json')
    manifest.update(public_sha256={f: digest(public / f) for f in OFFICE_FILES},
                    reference_sha256={f: digest(reference / f) for f in OFFICE_FILES})
    save_json(private / 'native_manifest.json', manifest)
    save_json(private / 'case.json', raw)
    save_json(private / 'milestone.json', {'stage': stage, 'event': raw['event'] if stage != 2 else None,
        'entities': graph['entities'], 'new_capabilities': [CAPABILITIES[stage-1]], 'retained_capabilities': list(CAPABILITIES[:stage-1]),
        'formula_stage': origins, 'formula_check_stage': probe_origins})
    return scores['groups']


def functional_groups(report, meta):
    groups = {name: {'passed': 0, 'total': 0, 'failed': []} for name in CAPABILITIES[:meta['stage']]}
    origins = meta['formula_check_stage']
    checks = {row['object']: row['pass'] for row in report['checks']}
    for key, stage in origins.items():
        bucket = groups[CAPABILITIES[stage - 1]]
        bucket['total'] += 1
        if checks.get(key):
            bucket['passed'] += 1
        else:
            bucket['failed'].append(key)
    return groups


def validate(stages):
    third = stages[-1]
    oracle = read_json(third / 'private/oracle.json')
    meta = read_json(third / 'private/milestone.json')
    with TemporaryDirectory(prefix='evolution_control_') as tmp:
        area = Path(tmp)
        transitions = []
        for i, previous in enumerate(stages[:-1], 1):
            candidate = area / f'transition_{i}'
            next_stage = stages[i]
            transitions.append(prepare_stage(previous / 'private/reference', candidate, next_stage))
            if snapshot(candidate, OFFICE_FILES) != snapshot(next_stage / 'public', OFFICE_FILES):
                raise ValueError('Canonical and actually carried gold inputs disagree')
        results = {}
        for label, formula in (
            ('new_pass_old_regression', f'=IF(IF(B6="续约",{RENEW_TEST},AND(B4>=$H$2,B5>=$H$4)),1,0)'),
            ('answers_hardcoded', '=0'),
        ):
            destination = area / label
            copytree(third / 'private/reference', destination)
            file = destination / FILES['excel']
            book = load_workbook(file)
            book[APPROVAL]['B8'] = formula
            book.save(file)
            book.close()
            scored = grade(destination, oracle)
            groups = functional_groups(scored, meta)
            if scored['pass']:
                raise ValueError(f'Unable to reject {label}')
            if label == 'new_pass_old_regression' and [group['passed'] == group['total'] for group in groups.values()] != [True, False, True]:
                raise ValueError('New success / old failure is not independently detected')
            results[label] = {'pass': scored['pass'], 'functional_groups': groups,
                              'native_groups': scored['groups']}
        return transitions, results


def build(output=DEFAULT):
    output = Path(output)
    if any((output / 'agent_runs').glob('*/plan.json')):
        raise FileExistsError('Use a fresh output directory for a task linked to a frozen model run')
    graph, opening, corrected, approved, _ = cases()
    latest = corrected['after']
    renewed = {name for name in approved if latest['occupancy_rate'][name] >= latest['occupancy_floor']
               and latest['average_daily_rate'][name] >= 120}
    if renewed != {WILLOW, HARBOR} or WILLOW in latest['qualified'] or latest['average_daily_rate'][WILLOW] != 120:
        raise ValueError('Case has no meaningful contrast between approval modes')
    stages = [output / f'stage_{i:02d}' for i in (1, 2, 3)]
    scores = {p.name: build_phase(p, graph, opening, corrected, approved, renewed, i) for i, p in enumerate(stages, 1)}
    transitions, controls = validate(stages)
    summary = {'passed': True, 'stages': scores, 'transitions': transitions, 'controls': controls, 'agent_calls': 0,
               'witness': {'hotel': WILLOW, 'adr': 120, 'prior_signed': True, 'new_signing': False, 'renewal': True,
                           'shared_entry': f'{APPROVAL}!B8', 'approved_roster': approved, 'renewal_roster': renewed}}
    save_json(output / 'summary.json', summary)
    save_json(output / 'task_dependencies.json', {'capabilities': list(CAPABILITIES), 'edges': [
        {'from': 1, 'to': 2, 'interface': f'{ANALYSIS}!C2:D7'},
        {'from': 2, 'to': 3, 'interface': f'{APPROVAL}!B8', 'historical_record': 'Word signed approval roster'},
    ]})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    destination = parser.parse_args().output
    build(destination)
    print(f'Built and verified three functional hotel milestones in {destination}')


if __name__ == '__main__':
    main()
