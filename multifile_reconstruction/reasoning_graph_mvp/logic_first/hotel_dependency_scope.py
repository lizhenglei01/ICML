"""Build matched hotel milestones with centralized or workflow-local policy routing."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from shutil import copy2, copytree

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import quote_sheetname

from .hotel_agent import file_hashes
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_office import FILES, build_layout, grade, snapshot
from .hotel_publication import workbook_signature

DEFAULT = ARTIFACTS / 'hotel_dependency_scope'
OFFICE = tuple(FILES.values())
JUN, JUL = 202606, 202607
HOTELS = ('Willowbank', 'Harborview', 'Maple Court')
LEDGER, POLICY, ARCHIVE, HUB = 'Ledger', 'Policy', 'June archive', 'Shared assessment'
SINGLE, BATCH, REPORT = 'Single review', 'Batch review', 'Monthly report'
WORKFLOWS = {
    SINGLE: [(HOTELS[0], JUL)],
    BATCH: [(name, month) for month in (JUN, JUL) for name in HOTELS],
    REPORT: [(name, JUL) for name in HOTELS],
}
RECORDS = {
    (HOTELS[0], JUN): (80, 100, 10400), (HOTELS[1], JUN): (80, 100, 10400),
    (HOTELS[2], JUN): (80, 100, 8800), (HOTELS[0], JUL): (80, 100, 9600),
    (HOTELS[1], JUL): (70, 100, 8680), (HOTELS[2], JUL): (80, 100, 9840),
}
SIGNED = (1, 1, 0)
PACKAGES = {'m1': 1, 'm2': 2, 'm3_a': 3, 'm3_b': 3}
CAPABILITIES = ('经营数据可重算', '单家、批量与月报执行新签审批', '三个入口按月份及历史签约状态执行续约政策')
RENEWAL_NOTICE = ('自2026年7月起，6月已签约酒店适用续约门槛：入住率至少75%，平均房价至少120元。'
                  ' / From July 2026, hotels in the June signed archive renew at a minimum of 75% occupancy and 120 yuan ADR.')


def task_text(stage):
    shared = """请在原文件上完成本轮任务。三种 Excel 业务入口分别是「Single review」单家审核、「Batch review」批量审核和「Monthly report」月报。月份采用 YYYYMM，房价以元计。业务入口的酒店和月份可切换，账本会收到更正，因此 Excel 的计算和判断结果需要用可重算公式。维持原有表格、标题、展示结构和无关内容。可在业务计算区域添加同一工作表上的辅助公式。Word 和 PPT 中的发布结果是本次月报的已发布快照。

Complete this milestone in the supplied files. Excel has three workflows: Single review, Batch review and Monthly report. Review months use YYYYMM and prices are in yuan. The selected hotel and month can change and ledger entries can be corrected, so use recalculating Excel formulas. Keep existing tables, labels, layout and unrelated content. You may add formulas to the calculation areas without adding sheets. The published Word and PPT values are snapshots of the issued monthly report.
"""
    tasks = {
        1: """本轮仅编辑 Excel。以账本为来源，按酒店和月份计算入住率（已售间夜 ÷ 可售间夜）及平均房价（净客房收入 ÷ 已售间夜）。把计算放在共用经营底稿，三个入口均从它读取对应酒店和月份的值。月报按选择器选定的月份展示全部酒店，批量表按每行月份，单家表按所选酒店和月份。审批和发布留待下一轮。Word 和 PPT 只读。

Edit Excel only. Build reusable occupancy (sold / available room nights) and ADR (net room revenue / sold room nights) formulas in Shared assessment. All three workflows should consume the shared metrics for their selected hotel and month. The report uses its month selector for all hotels; batch uses each row's month; single uses its selected hotel and month. Leave decision and publication work for the next milestone. Word and PPT are read-only.
""",
        2: """本轮完成新签审批。入住率至少 75% 且平均房价至少 125 元为通过。用同一共用底稿计算新签结果。让三个入口都按各自现有的审批路由取值：0 取共用的新签结果，1 取共用的预留续约结果。当前路由一律为 0，续约先不启用。通过为 1，未通过为 0。月报统计通过数。6 月签约归档记录 6 月新签审批的实际通过结果，并写成固定历史值。

把 7 月月报发布到 Word 和 PPT 的现有发布区。Word 更新合格酒店名单和 6 月签约归档名单，按酒店名称排序并以逗号和空格分隔，无人时写 None。PPT 更新本月通过数、三家酒店的 0/1 图表数据及图表旁的同值表格。保留标题和图表格式。

Implement new signing approval in the shared assessment. Pass if occupancy is at least 75% and ADR at least 125 yuan. Each workflow's existing decision route selects the shared new-sign result at 0 and the reserved shared renewal result at 1. All routes currently resolve to 0. Leave renewal inactive. Use 1 for pass and 0 for fail. Count passes in the monthly report. Freeze the actual June new-sign decisions as literal flags in the June signed archive.

Publish the July report using the existing areas only. In Word update the current eligible hotels and June signed hotel archive lists, alphabetically, comma-space separated, using None if empty. In PPT update the July total and each hotel's 0/1 chart data and the accompanying table. Preserve labels and chart formatting.
""",
        3: """从 2026 年 7 月起，6 月已签约酒店按续约审批。入住率门槛仍为 75%，续约房价门槛为 120 元。其余酒店和 7 月前的业务月份继续用原新签规则。三个经营入口使用各自所选酒店及业务月份。月报总数及发布应与同月审批一致。更新 Excel 的审批、Word 的现行政策说明和当月名单、PPT 的当月计数及三家酒店的 0/1 原生图表和旁表。Word 名单按酒店名称排序，用逗号和空格分开，无人时写 None。现行政策段需注明生效月份、续约对象及门槛，用中英文表述。6 月历史签约记录（Excel 和 Word）永久保留。不要追加新报表、图表或段落。

Effective July 2026, hotels in the June signed archive use renewal approval with the same 75% occupancy minimum and an ADR minimum of 120 yuan. All other hotels and all pre-July reviews retain the original new-signing rule. Apply this policy to the selected hotel and business month of each workflow. The monthly total and issued July results must agree. Update Excel approval, the existing current-policy paragraph and current eligibility list in Word, and the current count and native 0/1 chart plus companion table in PPT. Sort the Word list alphabetically and separate names with comma-space, using None if empty. State the effective month, qualifying archive and minima in the bilingual current-policy paragraph. Preserve the June signed archive in Excel and Word. Do not add report sections, charts or paragraphs.
""",
    }
    text = f'# 酒店合作审批 · M{stage} / Hotel partner approval · M{stage}\n\n' + shared + '\n' + tasks[stage]
    if stage == 3:
        text += ('\n在 Policy 中填入新参数，并在共用底稿建立可重算的续约资格。Word 现行政策栏使用批准的双语文案。'
                 '\nFill the new parameters in Policy and establish recalculating renewal eligibility in Shared assessment. '
                 'Use the approved wording below as the value of the existing current-policy item. Keep its label.\n\n'
                 + RENEWAL_NOTICE + '\n')
    return text


def qualified(month, archive, sold, available, revenue, stage):
    renewal = stage >= 3 and month >= JUL and archive
    return int(4 * sold >= 3 * available and revenue >= sold * (120 if renewal else 125))


def publication(level):
    current = {name: qualified(JUL, SIGNED[i], *RECORDS[name, JUL], level) if level >= 2 else 0
               for i, name in enumerate(HOTELS)}
    values = {
        'archive': ', '.join(sorted(name for name, flag in zip(HOTELS, SIGNED) if flag)) if level >= 2 else 'Pending',
        'eligible': ', '.join(sorted(name for name, flag in current.items() if flag)) or 'None',
        'total': sum(current.values()),
        'current_policy': RENEWAL_NOTICE if level >= 3 else '采用新签规则。 / New-signing policy applies.',
    }
    values.update({f'flag_{i}': current[name] for i, name in enumerate(HOTELS)})
    return values


def slots():
    chart = '7月审批结果 / July approval outcomes'
    definitions = [
        ('word', 'archive', '6月签约归档 / June signed archive'),
        ('word', 'eligible', '7月通过名单 / July eligible hotels'),
        ('word', 'current_policy', '现行审批政策 / Current approval policy'),
        ('ppt', 'total', '7月通过数 / July approval count'),
    ]
    bindings = [{'file': file, 'node_id': node, 'semantic_node_id': node, 'label': label, 'role': 'output'}
                for file, node, label in definitions]
    bindings.extend({'file': 'ppt', 'node_id': f'flag_{i}', 'semantic_node_id': f'flag_{i}',
                     'label': name, 'category': name, 'role': 'output', 'chart': chart,
                     'series': '审批结果（0未通过，1通过） / Decision (0 fail, 1 pass)'}
                    for i, name in enumerate(HOTELS))
    return bindings


def ref(sheet, address):
    return f'{quote_sheetname(sheet)}!{address}'


def route(name, month):
    return (f'=--AND({month}>={ref(POLICY, "$B$5")},'
            f'INDEX({ref(ARCHIVE, "$B$2:$B$4")},MATCH({name},{ref(ARCHIVE, "$A$2:$A$4")},0))=1)')


def lookup(column, row):
    return (f'INDEX({ref(HUB, f"${column}$2:${column}$7")},'
            f'MATCH(A{row}&"|"&B{row},{ref(HUB, "$A$2:$A$7")},0))')


def worksheet(book, name, header):
    sheet = book.create_sheet(name)
    sheet.append(header)
    for cell in sheet[1]:
        cell.font = Font(color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor='244865')
        sheet.column_dimensions[cell.column_letter].width = max(15, len(cell.value) + 3)
    sheet.freeze_panes = 'C2'
    return sheet


def write_book(path, level, variant):
    book = Workbook()
    book.remove(book.active)
    ledger = worksheet(book, LEDGER, ['Hotel', 'Business month', 'Sold nights', 'Available nights', 'Net room revenue'])
    for (name, month), (sold, available, revenue) in RECORDS.items():
        ledger.append([name, month, sold, available, revenue])
    policy = worksheet(book, POLICY, ['Threshold', 'Value'])
    for label, value in [('Occupancy minimum', .75), ('New-sign ADR minimum', 125),
                         ('Renewal ADR minimum', 120 if level >= 3 else 0),
                         ('Renewal effective month', JUL if level >= 3 else 0)]:
        policy.append([label, value])
    policy['B2'].number_format = '0%'
    archive = worksheet(book, ARCHIVE, ['Hotel', 'Signed in June (0/1)'])
    for name, flag in zip(HOTELS, SIGNED):
        archive.append([name, flag if level >= 2 else 0])
    hub = worksheet(book, HUB, ['Hotel | Month', 'Occupancy', 'ADR', 'New sign (0/1)', 'Renewal (0/1)', 'Shared route (0/1)'])
    for row in range(2, 8):
        name, month, sold, available, revenue = [ref(LEDGER, f'{col}{row}') for col in 'ABCDE']
        hub.append([f'={name}&"|"&{month}', f'={sold}/{available}' if level else 0, f'={revenue}/{sold}' if level else 0,
                    f'=--AND(B{row}>=Policy!$B$2,C{row}>=Policy!$B$3)' if level >= 2 else 0,
                    f'=--AND(B{row}>=Policy!$B$2,C{row}>=Policy!$B$4)' if level >= 3 else 0,
                    route(name, month) if level >= 3 and variant == 'a' else 0])
        hub[f'B{row}'].number_format = '0.0%'
    for workflow, entries in WORKFLOWS.items():
        sheet = worksheet(book, workflow, ['Hotel', 'Business month', 'Occupancy', 'ADR', 'Decision route (0/1)', 'Decision (0/1)'])
        for row, (name, month) in enumerate(entries, 2):
            if variant == 'a' or level < 2:
                router = '=' + lookup('F', row)
            else:
                router = route(f'A{row}', f'B{row}') if level >= 3 else 0
            sheet.append([name, '=$I$2' if workflow == REPORT else month,
                          '=' + lookup('B', row) if level else 0, '=' + lookup('C', row) if level else 0,
                          router, f'=IF(E{row}=1,{lookup("E", row)},{lookup("D", row)})' if level >= 2 else 0])
            sheet[f'C{row}'].number_format = '0.0%'
        if workflow == REPORT:
            sheet['H2'], sheet['I2'] = 'Report month', JUL
            sheet['H3'], sheet['I3'] = 'Total approved', '=SUM(F2:F4)' if level >= 2 else 0
            sheet.column_dimensions['H'].width = 20
    book.save(path)
    book.close()


def cell_key(sheet, address):
    from openpyxl.utils.cell import coordinate_to_tuple

    row, column = coordinate_to_tuple(address)
    return f'{FILES["excel"]}:sheet:{sheet}:logical_row:{row - 1}:column:{column}'


def points(mapping):
    return [{'sheet': sheet, 'cell': address, 'value': value,
             **({'value_type': 'text'} if isinstance(value, str) else {})}
            for (sheet, address), value in mapping.items()]


def expected(stage, inputs):
    """Compute expectations from raw inputs using independent business predicates."""
    ledger = {key: tuple(inputs.get((LEDGER, f'{col}{row}'), value)
                         for col, value in zip('CDE', amounts))
              for row, (key, amounts) in enumerate(RECORDS.items(), 2)}
    decisions, origins = {}, {}

    def add(sheet, address, value, milestone):
        decisions[sheet, address] = value
        origins[sheet, address] = milestone

    for row, (sold, available, revenue) in enumerate(ledger.values(), 2):
        add(HUB, f'B{row}', sold / available, 1)
        add(HUB, f'C{row}', revenue / sold, 1)
        if stage >= 2:
            add(HUB, f'D{row}', qualified(JUN, 0, sold, available, revenue, 2), 2)
        if stage >= 3:
            add(HUB, f'E{row}', int(4 * sold >= 3 * available and revenue >= sold * 120), 3)
    report_month = inputs.get((REPORT, 'I2'), JUL)
    report = []
    for workflow, entries in WORKFLOWS.items():
        for row, (name, month) in enumerate(entries, 2):
            name = inputs.get((workflow, f'A{row}'), name)
            month = report_month if workflow == REPORT else inputs.get((workflow, f'B{row}'), month)
            sold, available, revenue = ledger[name, month]
            add(workflow, f'C{row}', sold / available, 1)
            add(workflow, f'D{row}', revenue / sold, 1)
            if stage >= 2:
                archive = SIGNED[HOTELS.index(name)]
                target = qualified(month, archive, sold, available, revenue, stage)
                if stage == 2:
                    shared_row = list(RECORDS).index((name, month)) + 2
                    if inputs.get((HUB, f'F{shared_row}')) == 1:
                        target = inputs[HUB, f'E{shared_row}']
                add(workflow, f'F{row}', target, 3 if stage == 3 and archive and month >= JUL else 2)
                if workflow == REPORT:
                    report.append(target)
    if stage >= 2:
        add(REPORT, 'I3', sum(report),
            3 if stage == 3 and report_month >= JUL else 2)
    return decisions, origins


def scenario_spec(stage):
    data = [
        ('current', {}),
        ('corrected_all_high', {(LEDGER, f'{col}{row}'): val for row in range(2, 8)
                                for col, val in [('C', 85), ('D', 100), ('E', 11900)]}),
        ('corrected_all_low', {(LEDGER, f'{col}{row}'): val for row in range(2, 8)
                               for col, val in [('C', 60), ('D', 100), ('E', 6600)]}),
        ('june_archive_does_not_lower_new_sign_threshold', {
            (SINGLE, 'B2'): JUN, (REPORT, 'I2'): JUN, (LEDGER, 'E2'): 9760,
        }),
        ('switch_single_hotel_and_review_period', {
            (SINGLE, 'A2'): HOTELS[1], (SINGLE, 'B2'): JUN, (REPORT, 'I2'): JUN,
            (LEDGER, 'C3'): 75, (LEDGER, 'E3'): 9375,
        }),
        ('inclusive_renewal_border_and_below', {
            (LEDGER, 'C5'): 75, (LEDGER, 'E5'): 9000,
            (LEDGER, 'C6'): 75, (LEDGER, 'E6'): 8999,
        }),
    ]
    if stage == 2:
        data.append(('prewired_route_contract', {(HUB, coordinate): value for row in range(2, 8)
                                                 for coordinate, value in [(f'F{row}', 1), (f'E{row}', 1)]}))
    scenarios, origins = [], {}
    for name, inputs in data:
        values, stages = expected(stage, inputs)
        scenarios.append({'name': name, 'inputs': points(inputs), 'expected': points(values)})
        origins.update({f'functional_excel:{name}:{sheet}!{address}': milestone
                        for (sheet, address), milestone in stages.items()})
    baseline, _ = expected(stage, {})
    objects = {cell_key(sheet, address): f'{sheet}!{address}' for sheet, address in baseline}
    return {'file': FILES['excel'], 'objects': objects, 'scenarios': scenarios, 'allow_text_inputs': True,
            'flexible_formula_cells': True}, origins


def business_oracle(oracle):
    """Check renewal values on eligible dates and hotels, leaving inactive branch values open."""
    reviewed = deepcopy(oracle)
    inactive = {f'E{row}' for row, (name, month) in enumerate(RECORDS, 2)
                if month < JUL or not SIGNED[HOTELS.index(name)]}
    for scenario in reviewed['functional_excel']['scenarios']:
        scenario['expected'] = [item for item in scenario['expected']
                                if item['sheet'] != HUB or item['cell'] not in inactive]
    # Keep all formula objects, including the inactive rows, so the common
    # renewal column still has to implement reusable formulas.
    return reviewed


def routing_cells():
    return [(sheet, f'E{row}') for sheet, entries in WORKFLOWS.items() for row in range(2, 2 + len(entries))]


def activate_local_routing(path):
    book = load_workbook(path)
    try:
        for sheet, address in routing_cells():
            book[sheet][address] = 0
        book.save(path)
    finally:
        book.close()


def prepare_stage(submission, destination, package):
    destination.mkdir(parents=True)
    for filename in OFFICE:
        copy2(submission / filename, destination / filename)
    copy2(package / 'public' / 'instruction.md', destination / 'instruction.md')
    if package.name == 'm3_b':
        activate_local_routing(destination / FILES['excel'])
    return {'changed_routing_cells': [f'{sheet}!{cell}' for sheet, cell in routing_cells()] if package.name == 'm3_b' else []}


def build_package(package, predecessor):
    stage = PACKAGES[package.name]
    variant = 'b' if package.name == 'm3_b' else 'a'
    public, private = package / 'public', package / 'private'
    reference = private / 'reference'
    rendered = build_layout(package, slots(), publication(stage - 1), publication(stage), task_text(stage))
    if predecessor:
        for filename in OFFICE:
            copy2(predecessor / filename, public / filename)
        if variant == 'b':
            activate_local_routing(public / FILES['excel'])
    else:
        write_book(public / FILES['excel'], stage - 1, variant)
    write_book(reference / FILES['excel'], stage, variant)
    start, end = snapshot(public, OFFICE), snapshot(reference, OFFICE)
    if start.keys() != end.keys():
        raise ValueError(f'{package.name}: the object inventory changed')
    functional, origins = scenario_spec(stage)
    areas = [f'{FILES["excel"]}:sheet:{sheet}:' for sheet in (HUB, *WORKFLOWS)]
    route_internals = {cell_key(sheet, address) for sheet, address in routing_cells()}
    route_internals.update(cell_key(HUB, f'F{row}') for row in range(2, 8))
    ignored = {area + 'structure' for area in areas}
    if stage == 3:
        ignored |= route_internals
    oracle = rendered['oracle']
    oracle.update(
        allow_helpers_in=areas, functional_excel=functional, editable_files=list(OFFICE[:1] if stage == 1 else OFFICE),
        checks=[{'object': key, 'expected': value, 'group': 'required' if value != start[key] else 'protected'}
                for key, value in sorted(end.items()) if key not in ignored],
        excel_publication={'file': FILES['excel'], 'cells': [],
                           'reference_workbook_part_hash': end[FILES['excel'] + ':protected_part:xl/workbook.xml'],
                           'default_calc_workbook_signature': workbook_signature(reference / FILES['excel'])},
    )
    if stage == 3:
        save_json(private / 'oracle_all_intermediates.json', oracle)
        oracle = business_oracle(oracle)
        origins = {f"functional_excel:{scenario['name']}:{item['sheet']}!{item['cell']}":
                   origins[f"functional_excel:{scenario['name']}:{item['sheet']}!{item['cell']}"]
                   for scenario in oracle['functional_excel']['scenarios'] for item in scenario['expected']}
    save_json(private / 'oracle.json', oracle)
    save_json(private / 'milestone.json', {'stage': stage, 'new_capabilities': [CAPABILITIES[stage - 1]],
        'retained_capabilities': list(CAPABILITIES[:stage - 1]), 'formula_check_stage': origins, 'formula_stage': {}})
    manifest = read_json(private / 'native_manifest.json')
    manifest.update(public_sha256=file_hashes(public, OFFICE), reference_sha256=file_hashes(reference, OFFICE),
                    native_formula='Native Excel formulas recomputed by LibreOffice on unmodified and altered inputs.')
    save_json(private / 'native_manifest.json', manifest)
    reports = {}
    for name, path in [('reference', reference), ('unedited', public)]:
        score = grade(path, oracle)
        save_json(private / f'native_{name}_evaluation.json', score)
        failures = [item for item in score['checks'] if not item['pass']]
        if score['pass'] != (name == 'reference') or any(item['group'] == 'structure' for item in failures):
            raise ValueError(f'{package.name} / {name} invalid score: {failures[:12]}')
        reports[name] = score['groups']
    return reports


def validate_pair(a, b):
    before_a, before_b = (snapshot(path / 'public', OFFICE) for path in (a, b))
    changed = {key for key in before_a if before_a[key] != before_b[key]}
    expected_routes = {cell_key(sheet, address) for sheet, address in routing_cells()}
    if before_a.keys() != before_b.keys() or changed != expected_routes:
        raise ValueError(f'Unexpected A/B source differences: {sorted(changed ^ expected_routes)}')
    if (a / 'public/instruction.md').read_bytes() != (b / 'public/instruction.md').read_bytes():
        raise ValueError('The A/B prompt changed')
    if (a / 'private/oracle.json').read_bytes() != (b / 'private/oracle.json').read_bytes():
        raise ValueError('The A/B Oracle changed')
    return sorted(changed)


def controls(output):
    package = output / 'm3_b'
    oracle = read_json(package / 'private/oracle.json')
    cases = {
        'batch_missing_renewal': {(BATCH, 'E5'): 0},
        'new_sign_uses_renewal_floor': {(HUB, f'D{row}'): f'=--AND(B{row}>=Policy!$B$2,C{row}>=Policy!$B$4)'
                                       for row in range(2, 8)},
        'history_overwritten': {(ARCHIVE, 'B3'): 0},
        'monthly_hardcoded_count': {(REPORT, 'I3'): 1},
    }
    results = {}
    for case, mutations in cases.items():
        directory = output / 'controls' / case
        copytree(package / 'private/reference', directory)
        book = load_workbook(directory / FILES['excel'])
        for (sheet, address), value in mutations.items():
            book[sheet][address] = value
        book.save(directory / FILES['excel'])
        book.close()
        score = grade(directory, oracle)
        save_json(directory / 'score.json', score)
        failed = [item['object'] for item in score['checks'] if not item['pass']]
        criteria = {
            'batch_missing_renewal': f'functional_excel:current:{BATCH}!F5',
            'new_sign_uses_renewal_floor': f'functional_excel:june_archive_does_not_lower_new_sign_threshold:{SINGLE}!F2',
            'history_overwritten': cell_key(ARCHIVE, 'B3'),
            'monthly_hardcoded_count': f'functional_excel:current:{REPORT}!I3',
        }
        if score['pass'] or criteria[case] not in failed or any(item['group'] == 'structure' and not item['pass']
                                                                for item in score['checks']):
            raise ValueError(f'Control {case} had unexpected results: {failed}')
        results[case] = {'pass': score['pass'], 'failed': failed}
    return results


def validate_central_edit(output):
    """Apply A's complete edit in B while preserving B's three starting routes."""
    directory = output / 'controls' / 'central_edit_only'
    copytree(output / 'm3_a/private/reference', directory)
    activate_local_routing(directory / FILES['excel'])
    score = grade(directory, read_json(output / 'm3_b/private/oracle.json'))
    save_json(directory / 'score.json', score)
    failures = [item['object'] for item in score['checks'] if not item['pass']]
    required = {f'functional_excel:current:{sheet}!F{row}' for sheet, row in
                ((SINGLE, 2), (BATCH, 5), (REPORT, 2))}
    if score['pass'] or not required.issubset(failures):
        raise ValueError(f'Central-only edit did not expose the three local routing omissions: {failures}')
    if any(item['group'] != 'semantic' for item in score['checks'] if not item['pass']):
        raise ValueError('Expected only behavioral failures in the central-edit control')
    return {'a_reference_pass': True, 'same_edit_in_b_pass': score['pass'],
            'current_failures_by_workflow': sorted(required), 'failed': failures}


def build(output):
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for name, stage in PACKAGES.items():
        predecessor = {1: None, 2: output / 'm1/private/reference', 3: output / 'm2/private/reference'}[stage]
        results[name] = build_package(output / name, predecessor)
        print('HOTEL_SCOPE_VALIDATED', name, flush=True)
    differences = validate_pair(output / 'm3_a', output / 'm3_b')
    save_json(output / 'summary.json', {'passed': True, 'packages': results, 'controls': controls(output),
        'matched_central_edit': validate_central_edit(output),
        'input_snapshot_differences': differences, 'policy_edit_sites': {'a': [HUB], 'b': list(WORKFLOWS)},
        'contract': 'Same instruction, score, facts and publication. A uses a common route; B has a local route in each workflow. Both reuse shared new-sign and renewal eligibility calculations.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == '__main__':
    main()
