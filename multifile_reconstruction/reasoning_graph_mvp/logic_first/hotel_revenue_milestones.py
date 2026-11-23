"""Build native hotel milestones that extend, exercise, and preserve earlier work."""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.utils import quote_sheetname

from .hotel_deep_case import exact_reference, task_text
from .hotel_graph import ARTIFACTS, RULES, amended, read_json, save_json
from .hotel_office import FILES, build_layout, digest, grade, patch_reference, read_observations, snapshot, structural_key
from .hotel_revenue_evidence import GATE, NET, RECEIPTS, REFUNDS, shared_case
from .hotel_revenue_sequence import OFFICE_FILES, SOLD, prepare_stage_two, sequence_slots
from .hotel_visibility import solve_public


DEFAULT = ARTIFACTS / 'hotel_revenue_milestones'
ANALYSIS = '酒店指标'
WILLOW = 'Willowbank Hotel'
HARBOR = 'Harborstone Grand Hotel'
SILVER = 'Silverbrook Garden Hotel'
MAPLE = 'Maplecrest City Hotel'
CONFIRMED = frozenset((WILLOW, HARBOR, MAPLE))
PENDING = '待签发 / Pending approval'
RENEWAL_POLICY = (
    '只有已列入正式签约名单且当前仍符合资格的酒店可以续约。已签发的名单及家数记录当时决定，后续复核不可追溯修改。 / '
    'Renew only hotels in the issued signing roster that still qualify today. The issued roster and its count record the earlier decision and stay fixed in later reviews.'
)
CAPABILITIES = ('live_amounts_and_rates', 'live_qualification_and_issued_approval', 'renewal_reporting')
BUSINESS_SLOTS = (
    ('word', 'approved_roster', '历史正式签约名单 / Issued signing roster'),
    ('excel', 'approved_count', '历史签约家数 / Issued signing count'),
    ('word', 'renewal_roster', '本次续约名单 / Renewal roster'),
    ('excel', 'renewal_count', '本次续约家数 / Renewal count'),
    ('ppt', 'renewal_count', '本次续约家数 / Renewal count'),
)


def roster(members):
    return ', '.join(sorted(members)) or '无 / None'


def cases():
    graph, first, _ = shared_case()
    event = {**first['event'], 'id': 'Q4-RENEWAL-001', 'entity': WILLOW, 'old_value': 8000, 'new_value': 9000}
    revised_basis = amended(first['after_basis'], event)
    net = {hotel: revised_basis[RECEIPTS][hotel] - revised_basis[REFUNDS][hotel] for hotel in graph['entities']}
    revised = {**exact_reference(graph, {**revised_basis, NET: net}), 'policy_gate': GATE}
    last = {'before': first['after'], 'after': revised, 'event': event, 'after_basis': revised_basis}
    proof = solve_public(graph, {**revised_basis, 'policy_gate': GATE}, RULES, targets=revised)
    if proof['missing_targets'] or proof['conflicts'] or {k: r['value'] for k, r in proof['proofs'].items()} != revised:
        raise ValueError('Independent exact hotel arithmetic disagrees with the execution graph')
    approved = first['after']['qualified'] & CONFIRMED
    renewed = approved & revised['qualified']
    if (approved, revised['qualified'], revised['average_daily_rate'][WILLOW], revised['review_adr_floor'], renewed) != (
        {HARBOR, WILLOW}, {HARBOR, SILVER}, 120, 125, {HARBOR},
    ):
        raise ValueError('Milestone truth disagrees with the hand-calculated control')
    return graph, first, last, approved, renewed


def instruction(stage, event):
    if stage == 1:
        return '''# 第一轮 · 建立酒店指标 / Milestone 1 · Build the hotel analysis

Willowbank Hotel 的已售间夜数已从 6,000 更正为 8,000。只修订 Excel 中已登记的间夜数，并完成「酒店指标」工作表。B 列从 PPT 客房入账图复制各酒店毛额，C 列以原生公式用更正后的已售间夜除以可售间夜，D 列以原生公式用 B 列毛额扣除 Excel 退款后除以更正后的已售间夜。公式应在来源数字变化后自动重新计算。保存原文件，保留已有公式、格式、其他发布结果及 Word 和 PPT。

Willowbank Hotel's sold room nights were corrected from 6,000 to 8,000. Update its posted amount in Excel and complete the prebuilt 酒店指标 sheet. Copy each hotel's gross room receipts from PowerPoint into column B. Use native formulas in C for corrected sold nights divided by available nights, and in D for gross receipts in B less the Excel refund, divided by corrected sold nights. Reference the source cells so the formulas update when inputs change. Save in place. Preserve existing formulas, formatting, other issued results, Word, and PowerPoint.
'''
    if stage == 2:
        names = roster(CONFIRMED)
        return f'''# 第二轮 · 签约审批 / Milestone 2 · Issue the signing decision

使用上一轮「酒店指标」的 C、D 列和 Word 的复核政策。先用低于入住率门槛的酒店算价格对照组和房价线。

- Excel：将入住率门槛（小数）和房价线填入「酒店指标」H2、H3，在预留的 E 列填入 1／0 原生资格公式，须直接引用同一行 C、D 和 H2、H3。更新复核报表已有的资格标记和历史签约家数。
- Word：在已有复核结论中更新价格对照组、对照组均价、房价线、入选名单、家数和入选均价，并填入历史正式签约名单。
- PPT：只更新已有的资格表及对应的可编辑资格图表。保留审核摘要中的续约家数。

委员会收到正式签约回执的酒店是 {names}。历史签约名单只包含其中当前符合资格的酒店。名单按英文名称排序，用英文逗号与空格分隔。保留第一轮公式和输入、续约留空区、现有布局和格式。使用现有位置填写，无需插入新段落、列或图表。

Use the C and D results created in 酒店指标 in milestone 1 and the review terms in Word. Find the below-threshold occupancy peer group and the rate hurdle.

- Excel: Put the minimum occupancy as a decimal and the rate hurdle in H2 and H3 of 酒店指标. Fill the existing E column with native 1/0 qualification formulas that directly reference C and D in that row and the two threshold cells. Update the existing issued-review qualification flags and the issued signing count.
- Word: Update the existing peer group, peer mean, rate hurdle, qualifying names, count, and unweighted mean ADR fields. Fill the historical issued signing roster.
- PowerPoint: Update only the existing qualification table and its editable qualification chart. Keep the review summary's renewal count unchanged.

The signed-reply list for this approval is {names}. Include only currently qualified hotels from this list in the issued signing roster. Use alphabetical names separated by comma and space. Preserve the prior formulas and inputs, the unused renewal fields, layout, formatting, and unrelated content. Fill the existing fields without adding paragraphs, columns, or charts. Save the three files in place.
'''
    return task_text(event).replace('季度酒店定价试点复核 / Quarterly pricing pilot review',
                                    '第三轮 · 续约审核 / Milestone 3 · Add renewal reporting') + '''
请继续使用「酒店指标」中已存在的计算和资格公式。按 Word 续约条款填入本次续约名单，并同步 Excel 与 PPT 的续约家数。名单按英文名称排序，用英文逗号与空格分隔。历史签约名单及家数按当时正式签发结果保留。

Continue to use the existing analysis and qualification formulas on 酒店指标. Apply the renewal terms in Word. Update the renewal roster in Word and renewal count in Excel and PowerPoint. Use alphabetical names separated by comma and space. Preserve the signed historical roster and count as issued. Retain both earlier Excel capabilities.
'''


def phase_slots(graph, first, last, approved, renewed, stage):
    slots, before, after = sequence_slots(graph, last if stage == 3 else first)
    for slot in slots:
        key = slot['node_id']
        if slot['role'] == 'output' and slot['semantic_node_id'] == SOLD and stage == 2:
            before[key] = after[key]
        if slot['role'] == 'output' and slot['semantic_node_id'] != SOLD and stage == 1:
            after[key] = before[key]

    def add(file, node, label, old, new, role='output'):
        key = f'{role}.{node}.{file}'
        slots.append({'file': file, 'node_id': key, 'semantic_node_id': node, 'role': role, 'label': label})
        before[key], after[key] = old, new

    add('word', 'renewal_policy', '续约条款 / Renewal terms', RENEWAL_POLICY, RENEWAL_POLICY, 'context')
    for file, node, label in BUSINESS_SLOTS:
        if node.startswith('approved'):
            initial, signed = (PENDING, roster(approved)) if node == 'approved_roster' else (0, len(approved))
            add(file, node, label, signed if stage == 3 else initial, signed if stage >= 2 else initial)
        else:
            initial, decided = (PENDING, roster(renewed)) if node == 'renewal_roster' else (0, len(renewed))
            add(file, node, label, initial, decided if stage == 3 else initial)
    return slots, before, after


def source_cells(oracle):
    return {(row['semantic_node_id'], row['entity']): row['location'] for row in oracle['bindings']
            if row['role'] == 'evidence' and row['file'] == FILES['excel']}


def add_analysis(path, graph, state, sources, level, *, with_qualification=True):
    book = load_workbook(path)
    ws = book.create_sheet(ANALYSIS)
    ws.append(['酒店 / Hotel', '客房入账毛额 / Gross receipts', '入住率 / Occupancy', '平均房价 / ADR'])
    for col, width in zip('ABCD', (31, 30, 20, 20), strict=True):
        ws.column_dimensions[col].width = width
    if with_qualification:
        ws['E1'] = '当前资格 1/0 / Qualification'
        for col, width in zip('EGH', (26, 27, 17), strict=True):
            ws.column_dimensions[col].width = width
        ws['G1'], ws['H1'] = '审批参数 / Review terms', '当前值 / Value'
        ws['G2'], ws['G3'] = '最低入住率 / Occupancy floor', '房价线 / Rate hurdle'
        ws['H2'], ws['H3'] = (float(state['occupancy_floor']), float(state['review_adr_floor'])) if level >= 2 else (0, 0)
        ws['H2'].number_format = '0.00%'
        ws['H3'].number_format = '0.00'

    def ref(node, hotel):
        location = sources[node, hotel]
        return f"{quote_sheetname(location['sheet'])}!{location['cell']}"

    for i, hotel in enumerate(graph['entities'], 2):
        ws[f'A{i}'] = hotel
        ws[f'B{i}'] = state[RECEIPTS][hotel] if level >= 1 else 0
        ws[f'C{i}'].number_format = '0.00%'
        ws[f'D{i}'].number_format = '0.00'
        if with_qualification:
            ws[f'E{i}'].number_format = '0'
        if level >= 1:
            sold = ref(SOLD, hotel)
            ws[f'C{i}'] = f'={sold}/{ref("available_room_nights", hotel)}'
            ws[f'D{i}'] = f'=(B{i}-{ref(REFUNDS, hotel)})/{sold}'
        if with_qualification and level >= 2:
            ws[f'E{i}'] = f'=IF(AND(C{i}>=$H$2,D{i}>=$H$3),1,0)'
    book.save(path)
    book.close()


def formula_spec(graph, state, sources, stage):
    def point(cell, value, sheet=ANALYSIS):
        return {'sheet': sheet, 'cell': cell, 'value': value}

    names = ['published_inputs', 'changed_sold_nights', 'changed_capacity', 'changed_receipts', 'changed_refund']
    prior_metric_probes = [(0, 1000, 0), (1, 0, 0), (1, 1000, 1)]
    metric_names = ('prior_occupancy_below_threshold', 'prior_adr_below_threshold', 'both_prior_metrics_pass')
    if stage >= 2:
        names.extend([*metric_names, 'changed_occupancy_floor', 'changed_rate_hurdle'])
    by_name = {name: {'name': name, 'inputs': [], 'expected': []} for name in names}
    baseline = by_name['published_inputs']
    sold_probe = by_name['changed_sold_nights']
    if stage >= 2:
        by_name['changed_occupancy_floor']['inputs'].append(point('H2', 1))
        by_name['changed_rate_hurdle']['inputs'].append(point('H3', 1000))
    origins = {}
    for i, hotel in enumerate(graph['entities'], 2):
        sold, capacity, net = (state[node][hotel] for node in (SOLD, 'available_room_nights', NET))
        trial_sold = sold - 500
        location = sources[SOLD, hotel]
        sold_probe['inputs'].append(point(location['cell'], trial_sold, location['sheet']))
        live_c, live_d = Fraction(sold, capacity), Fraction(net, sold)
        test_c, test_d = Fraction(trial_sold, capacity), Fraction(net, trial_sold)
        for col, live, trial in [('C', live_c, test_c), ('D', live_d, test_d)]:
            cell = f'{col}{i}'
            origins[cell] = 1
            baseline['expected'].append(point(cell, live))
            sold_probe['expected'].append(point(cell, trial))
        capacity_location = sources['available_room_nights', hotel]
        refund_location = sources[REFUNDS, hotel]
        for name, updated, expected in (
            ('changed_capacity', point(capacity_location['cell'], capacity + 1000, capacity_location['sheet']),
             point(f'C{i}', Fraction(sold, capacity + 1000))),
            ('changed_receipts', point(f'B{i}', state[RECEIPTS][hotel] + 90000), point(f'D{i}', Fraction(net + 90000, sold))),
            ('changed_refund', point(refund_location['cell'], state[REFUNDS][hotel] + 60000, refund_location['sheet']),
             point(f'D{i}', Fraction(net - 60000, sold))),
        ):
            by_name[name]['inputs'].append(updated)
            by_name[name]['expected'].append(expected)
        if stage >= 2:
            e = f'E{i}'
            origins[e] = 2
            qualifies = int(hotel in state['qualified'])
            baseline['expected'].append(point(e, qualifies))
            sold_probe['expected'].append(point(e, int(test_c >= state['occupancy_floor'] and test_d >= state['review_adr_floor'])))
            # Keep one prior metric passing while isolating the other condition.
            for name, (occupancy, adr, flag) in zip(metric_names, prior_metric_probes, strict=True):
                probe = by_name[name]
                probe['inputs'].extend((point(f'C{i}', occupancy), point(f'D{i}', adr)))
                probe['expected'].append(point(e, flag))
            # Put every hotel in the passing range under the original threshold,
            # so ignoring a threshold reference fails even on currently ineligible rows.
            for name, occupancy, adr in [('changed_occupancy_floor', Fraction(4, 5), 1000), ('changed_rate_hurdle', 1, 200)]:
                probe = by_name[name]
                probe['inputs'].extend((point(f'C{i}', occupancy), point(f'D{i}', adr)))
                probe['expected'].append(point(e, 0))
    return list(by_name.values()), origins


def prepare_stage(submission, destination, package):
    submission, destination, package = map(Path, (submission, destination, package))
    meta = read_json(package / 'private/milestone.json')
    if meta['stage'] == 3:
        return prepare_stage_two(submission, destination, package)
    if meta['stage'] != 2 or any(not (submission / f).is_file() or (submission / f).is_symlink() for f in OFFICE_FILES):
        raise ValueError('Milestone-two history needs the complete, regular preceding Office files')
    destination.mkdir(parents=True)
    for name in OFFICE_FILES:
        copyfile(submission / name, destination / name)
    copyfile(package / 'public/instruction.md', destination / 'instruction.md')
    return {'event': None, 'changed_native_objects': [], 'unchanged_file_bytes': list(OFFICE_FILES)}


def build_phase(package, graph, first, last, approved, renewed, stage):
    slots, before, after = phase_slots(graph, first, last, approved, renewed, stage)
    raw = last if stage == 3 else first
    result = build_layout(package, slots, before, after, instruction(stage, raw['event']), ppt_size='medium')
    oracle = result['oracle']
    src = source_cells(oracle)
    current = raw['after']
    public, private = package / 'public', package / 'private'
    ref = private / 'reference'
    add_analysis(public / FILES['excel'], graph, first['after'], src, min(stage - 1, 2))
    add_analysis(ref / FILES['excel'], graph, current, src, min(stage, 2))
    initial, final = snapshot(public, OFFICE_FILES), snapshot(ref, OFFICE_FILES)
    if initial.keys() != final.keys():
        raise ValueError('Milestone reference changed the prepared object inventory')
    scenarios, origins = formula_spec(graph, current, src, stage)
    formula_objects = {structural_key(ref, {'file': FILES['excel'], 'location': {
        'kind': 'cell', 'sheet': ANALYSIS, 'cell': cell}})[0]: f'{ANALYSIS}!{cell}' for cell in origins}
    oracle['functional_excel'] = {'file': FILES['excel'], 'objects': formula_objects, 'scenarios': scenarios}
    oracle['checks'] = [{'object': key, 'expected': final[key], 'group': 'required' if value != final[key] else 'protected'}
                        for key, value in sorted(initial.items())]
    save_json(private / 'oracle.json', oracle)
    frozen_oracle = read_json(private / 'oracle.json')
    assessed = grade(ref, frozen_oracle)
    if not assessed['pass']:
        raise ValueError(f'{package.name} reference failed: {[c for c in assessed["checks"] if not c["pass"]]}')
    save_json(private / 'native_reference_evaluation.json', assessed)
    manifest = read_json(private / 'native_manifest.json')
    manifest.update(public_sha256={f: digest(public / f) for f in OFFICE_FILES}, reference_sha256={f: digest(ref / f) for f in OFFICE_FILES})
    save_json(private / 'native_manifest.json', manifest)
    save_json(private / 'case.json', raw)
    save_json(private / 'milestone.json', {
        'stage': stage, 'event': raw['event'] if stage != 2 else None,
        'new_capabilities': [CAPABILITIES[stage - 1]], 'retained_capabilities': list(CAPABILITIES[:stage-1]),
        'formula_stage': {f'{ANALYSIS}!{cell}': prev for cell, prev in origins.items()},
    })
    return assessed['groups']


def alter_roster(source, output, oracle, members):
    values = {check['node_id']: check['after'] for check in oracle['evidence_checks']}
    binding, = [b for b in oracle['bindings'] if b['semantic_node_id'] == 'approved_roster']
    patch_reference(source, output, oracle['bindings'], values, {**values, binding['node_id']: roster(members)})
    return binding


def validate_transitions(stages, graph, first, last, approved, renewed):
    stage = stages[-1]
    oracle = read_json(stage / 'private/oracle.json')
    public = stage / 'public'
    ref = stage / 'private/reference'
    alternative = frozenset((SILVER, WILLOW))
    if not alternative <= first['after']['qualified']:
        raise ValueError('Alternate committee replies would not produce a legitimate approval')
    with TemporaryDirectory(prefix='hotel_milestones_') as temporary:
        area = Path(temporary)
        transitions = []
        for idx, (prev, nxt) in enumerate(zip(stages, stages[1:]), 2):
            carry = area / f'carry_{idx}'
            record = prepare_stage(prev / 'private/reference', carry, nxt)
            if snapshot(carry, OFFICE_FILES) != snapshot(nxt / 'public', OFFICE_FILES):
                raise ValueError(f'Teacher control and inherited ideal state differ at milestone {idx}')
            transitions.append(record)
        alternate_public = area / 'alternate'
        binding = alter_roster(public, alternate_public, oracle, alternative)
        policy_binding, = [b for b in oracle['bindings'] if b['semantic_node_id'] == 'renewal_policy']
        permitted = structural_key(public, binding)
        baseline, changed = (snapshot(p, OFFICE_FILES) for p in (public, alternate_public))
        diff = sorted(k for k in baseline.keys() | changed.keys() if baseline.get(k) != changed.get(k))
        if diff != permitted:
            raise ValueError('Changing the prior decision also changed other stage-three evidence')
        renewal_graph = {**graph, 'productions': [*graph['productions'],
            {'id': 'renew_only_current_qualified_and_preapproved', 'op': 'AND', 'output': 'renewal_roster',
             'inputs': ['approved_roster', 'qualified'], 'requires': ['renewal_policy']},
            {'id': 'count_renewals', 'op': 'COUNT', 'output': 'renewal_count', 'inputs': ['renewal_roster']}]}
        rule_ids = (*RULES, 'renewal_policy')
        observations = {**last['after_basis'], 'policy_gate': GATE}
        targets = ('renewal_roster', 'renewal_count')
        without = solve_public(renewal_graph, observations, rule_ids, targets=targets)
        results = []
        for folder in (public, alternate_public):
            read_roster, rule = read_observations(folder, [binding, policy_binding])
            if rule['value'] != RENEWAL_POLICY:
                raise ValueError('Renewal terms are missing from the public Word source')
            observed = frozenset(read_roster['value'].split(', '))
            solved = solve_public(renewal_graph, {**observations, 'approved_roster': observed}, rule_ids, targets=targets)
            if solved['missing_targets'] or solved['conflicts']:
                raise ValueError('The renewal graph must recover a unique answer in both legal worlds')
            results.append(solved['proofs']['renewal_roster']['value'])
        if (set(without['missing_targets']) != set(targets) or results != [renewed, {SILVER}] or results[0] == results[1]):
            raise ValueError('The new capability did not demonstrably need the earlier approved roster')

        # Begin from the fully correct stage-three reference. Change only the older approval to the current list.
        regression = area / 'new_correct_old_regressed'
        alter_roster(ref, regression, oracle, last['after']['qualified'])
        regressed = grade(regression, oracle)
        failures = [r for r in regressed['checks'] if not r['pass']]
        if ({r['object'] for r in failures} != {*permitted, binding['evidence_id']}
                or regressed['groups']['required']['passed'] != regressed['groups']['required']['total']):
            raise ValueError('The new-correct/old-regressed control has incorrect attribution')

        wrong_formula = area / 'overwritten_formula'
        workbook = load_workbook(ref / FILES['excel'])
        workbook[ANALYSIS]['C2'] = float(last['after']['occupancy_rate'][WILLOW])
        wrong_formula.mkdir()
        workbook.save(wrong_formula / FILES['excel'])
        workbook.close()
        for name in (FILES['word'], FILES['ppt']):
            copyfile(ref / name, wrong_formula / name)
        formula_grade = grade(wrong_formula, oracle)
        if formula_grade['pass'] or formula_grade['groups']['protected']['passed'] == formula_grade['groups']['protected']['total']:
            raise ValueError('Oracle accepted a formula hardcoded to the current correct value')
        return {
            'transitions': transitions,
            'dependency_witness': {'identical_except_approved_roster': True, 'changed_native_objects': diff,
                'canonical_approved_roster': approved, 'alternate_approved_roster': alternative,
                'canonical_renewal_roster': results[0], 'alternate_renewal_roster': results[1],
                'renewals_underdetermined_without_approved_roster': True, 'current_qualified': last['after']['qualified']},
            'regression_control': {'pass': regressed['pass'], 'score_groups': regressed['groups'], 'failed_checks': failures},
            'hardcoded_formula_control': {'pass': formula_grade['pass'], 'score_groups': formula_grade['groups']},
            'productions': renewal_graph['productions'],
        }


def build(root=DEFAULT):
    root = Path(root)
    if any((root / 'agent_runs').glob('*/plan.json')):
        raise FileExistsError(f'{root} is linked to a frozen model trial. Select a new --output directory.')
    graph, first, last, approved, renewed = cases()
    stages = [root / f'stage_{i:02d}' for i in range(1, 4)]
    scores = {p.name: build_phase(p, graph, first, last, approved, renewed, i) for i, p in enumerate(stages, 1)}
    controls = validate_transitions(stages, graph, first, last, approved, renewed)
    productions = controls.pop('productions')
    summary = {'passed': True, 'stages': scores, **controls, 'agent_calls': 0}
    save_json(root / 'summary.json', summary)
    save_json(root / 'task_dependencies.json', {'stages': list(CAPABILITIES), 'edges': [
        {'from': 1, 'to': 2, 'via': 'Live occupancy and net ADR formulas from Excel'},
        {'from': 1, 'to': 3, 'via': 'Live occupancy and net ADR formulas from Excel'},
        {'from': 2, 'to': 3, 'via': 'Live qualification function and the official Word signing roster'},
    ], 'rules': productions})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    output = parser.parse_args().output
    build(output)
    print(f'Built and validated three hotel milestones in {output}')


if __name__ == '__main__':
    main()
