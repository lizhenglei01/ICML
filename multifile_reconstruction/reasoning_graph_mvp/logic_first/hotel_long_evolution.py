"""Continue the hotel workspace through shared policy and historical replay."""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from shutil import copyfile, copytree
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from .hotel_evolution import APPROVAL, CAPABILITIES as INITIAL_CAPABILITIES, publication_contract
from .hotel_evolution import DEFAULT as EARLIER
from .hotel_evolution_audit import corrected_specs
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_office import FILES, digest, grade, snapshot, structural_key
from .hotel_revenue_milestones import ANALYSIS, HARBOR, SILVER, WILLOW
from .hotel_revenue_sequence import OFFICE_FILES


DEFAULT = ARTIFACTS / 'hotel_eight_evolution'
POLICY, ARCHIVE = 'Policy', 'PolicyArchive'
BOOK = FILES['excel']
EARLIER_RUN = EARLIER / 'agent_runs/official_gpt62_sol_medium_20261122T151046060268Z'
EARLIER_SUBMISSION = EARLIER_RUN / 'stage_03_carry_forward/submission'
CAPABILITIES = (*INITIAL_CAPABILITIES, 'tiered_eligibility', 'revocable_renewal_override',
                'refund_amendment', 'quota_selection', 'historical_policy_replay')


REQUESTS = {
    4: (
        '在 Policy 中把 Willowbank 定为 Premier。填入 Premier 的新签房价线 130 元、续约房价线 125 元（K6、L6）。'
        '其他酒店仍用原新签及续约门槛 H3、H4。入住率和历史签约规则继续有效。'
        '把每家酒店的房价放到 Policy I6:I11，按 Policy A 列对应酒店引用既有指标。在 G6:G11 计算当前模式的逐家资格。'
        '原审批入口 B8 按所选酒店返回这个共用资格结果。',
        'Classify Willowbank as Premier. Put Premier new-signing/renewal ADR floors 130 and 125 in Policy K6/L6. '
        'Standard continues to use H3/H4 of the approval sheet. Occupancy and signing history still apply. '
        'Compute each hotel’s ADR in Policy I6:I11 from the earlier metrics, keyed by hotel name in A. '
        'Compute each hotel’s current-mode eligibility in Policy G6:G11. The existing B8 entry must look up that shared verdict.'),
    5: (
        '给 Willowbank 的续约录入特批编号 WB-ALPHA、撤销标志 0 和特批房价线 115 元（Policy 对应行 C、D、E）。'
        '来单编号由 Policy!B2 提供，交付默认 NONE。续约且编号与该酒店匹配、未撤销时，允许改用特批房价线。'
        '新签从不使用该特批。已签约和入住率要求继续有效。改共用资格公式，使 B8 也能反映特批和撤销。',
        'In Willowbank’s row enter renewal authorization WB-ALPHA, revoked flag 0 and override floor 115 in Policy C, D and E. '
        'The caller supplies the code in Policy!B2, delivered as NONE. A matching unrevoked code replaces the renewal ADR floor only. '
        'New signing, occupancy and prior-signing requirements are unchanged. Extend the shared eligibility formulas so B8 reflects overrides and revocation.'),
    6: (
        '收到 Willowbank 新核准的追加退款 45,000 元。把增量填入 Policy 对应行 F，保留最初的退款记录。'
        '升级原「酒店指标」平均房价公式，所有酒店都要在原退款之外扣除本轮追加退款。'
        '审批入口和逐家资格自动使用更正后的房价。保留签约历史与 M3 已发布报表的原值。如果 Excel 发布格是动态公式，请冻结成原发布值。'
        'Word 和 PPT 是原发布资料，仍应保持原样。',
        'Enter the newly approved additional refund of 45,000 for Willowbank in Policy column F. Keep the original refund source untouched. '
        'Refactor the existing ADR formulas for all hotels to subtract both the original refund and the hotel’s additional refund. '
        'The shared eligibility and B8 should use the corrected ADR. Preserve historical signing and the original milestone-three release figures. '
        'If the issued Excel report has live formulas that change the old release, freeze those cells at their previously issued values. Keep Word and PPT unchanged.'),
    7: (
        '在 Policy!B3 设置本轮试点名额 1。用同一套 G 列逐家资格和 I 列房价，在 H6:H11 计算批量入选（1 或 0）。'
        '只给符合资格的酒店名额，平均房价从高到低，同价按 Policy 酒店行的顺序。名额为 0 时全不入选。'
        'B8 仍返回单家资格，即使该酒店没抢到批量名额，也不应把单家资格改成 0。',
        'Set the pilot quota in Policy!B3 to 1. Add a live batch selection flag to Policy H6:H11 from the same G eligibility and I ADR values. '
        'Only eligible hotels can be selected. Rank by ADR, breaking ties by earlier Policy row; quota zero selects none. '
        'B8 continues to return individual eligibility without applying the batch quota.'),
    8: (
        'Policy!B4 选择 CURRENT 或 M3，交付默认 CURRENT。M3 使用 PolicyArchive 内当时的入住率、房价、历史签约标志及政策门槛，'
        '忽略当前等级、特批和退款更正。CURRENT 继续使用现行规则。'
        '同一个 B8 入口和 G 列资格随版本切换。审批表 B4/B5 显示选中版本的入住率、房价；I 列与批量排名也使用同版本房价。'
        '批量名额仍由 B3 控制，是审核模拟。保持历史快照、当前来源和已发布记录原样。',
        'In Policy!B4 support CURRENT and M3, delivered as CURRENT. M3 uses PolicyArchive’s historical occupancy, ADR, signing status, '
        'and thresholds. It ignores current tiers, current permits, and later refund adjustments. CURRENT keeps all current behavior. '
        'Use the same B8 entry and per-hotel eligibility; B4/B5 and Policy I must display the selected version’s metrics. '
        'Batch selection still uses B3 and ranks eligible hotels using the selected version’s ADR. Its quota is a review simulation. '
        'Preserve the archived snapshot, present source data, and issued records.'),
}


def instruction(stage):
    cn, en = REQUESTS[stage]
    return (f'# 酒店审批工作台 · 第 {stage} 轮 / Hotel approval milestone {stage}\n\n{cn}\n\n{en}\n\n'
            '仅修改 hotel_review.xlsx。审批办理 B3 选酒店、B6 选新签或续约、B8 返回 1 或 0。'
            '共享资格、房价和批量输出用整数标记或原生公式按题目要求重算。历史名单和已发布记录保持原值。'
            '可在审批办理或 Policy 的 N 列及之后加辅助格。Word 和 PPT 只供查阅，不修改。\n\n'
            'Edit only hotel_review.xlsx. Preserve the native interface B3 (hotel), B6 (新签 or 续约), B8 (individual 1/0). '
            'Use native formulas for the required live calculations. Preserve historical signing, issued results, and source files. '
            'Helpers may go in the approval worksheet or in Policy columns N onward. Word and PowerPoint are read-only evidence.\n')


def scaffold(book, stage, entities, original):
    if stage == 4:
        ws = book.create_sheet(POLICY)
        ws.append(['Approval policy / 审批政策', 'Value / 值'])
        for row in (2,3,4):
            ws.cell(row,1,'—')
        for column, label in (('A','Hotel'),('B','Tier'),('G','Eligible (1/0)'),('I','ADR')):
            ws[f'{column}5'] = label
        for name in entities:
            ws.append([name,'Standard'])
        ws['J5'], ws['K5'], ws['L5'] = 'Tier', 'New signing ADR', 'Renewal ADR'
        ws['J6'], ws['K6'], ws['L6'] = 'Premier', 0, 0
        for cell in ('G6:G11','I6:I11'):
            for row in ws[cell]:
                row[0].number_format = '0.00' if cell.startswith('I') else '0'
        ws.column_dimensions['A'].width = 31
        return
    policy = book[POLICY]
    if stage == 5:
        policy['A2'], policy['B2'] = 'Authorization input', 'NONE'
        policy['C5'], policy['D5'], policy['E5'] = 'Permit code', 'Revoked (1/0)', 'Permit ADR'
        for row in range(6,12):
            policy[f'C{row}'], policy[f'D{row}'], policy[f'E{row}'] = 'NONE', 0, 0
    elif stage == 6:
        policy['F5'] = 'Additional refund'
        for row in range(6,12):
            policy[f'F{row}'] = 0
    elif stage == 7:
        policy['A3'], policy['B3'] = 'Batch quota', 0
        policy['H5'] = 'Batch selected (1/0)'
        for row in range(6,12):
            policy[f'H{row}'].number_format = '0'
    elif stage == 8:
        policy['A4'], policy['B4'] = 'Policy version', 'CURRENT'
        ws = book.create_sheet(ARCHIVE)
        ws.append(['M3 policy snapshot / M3 政策档案', 'Value / 值'])
        ws.append(['Occupancy floor', 0.75])
        ws.append(['New signing ADR', 125])
        ws.append(['Renewal ADR', 120])
        ws.append(['Hotel','Occupancy','ADR','Historically signed (1/0)'])
        for item in original:
            ws.append(item)
        ws.column_dimensions['A'].width = 31


def original_snapshot():
    values = read_json(EARLIER/'stage_03/private/case.json')['after']
    entities = read_json(EARLIER/'stage_03/private/milestone.json')['entities']
    signed = {WILLOW, HARBOR}
    return entities, [[name, float(Fraction(values['occupancy_rate'][name])),
        float(Fraction(values['average_daily_rate'][name])), int(name in signed)] for name in entities]


def edit_workbook(path, callback):
    book = load_workbook(path)
    try:
        callback(book)
        book.save(path)
    finally:
        book.close()


def install(book, stage, *, permit_leaks=False):
    p, a, metrics = book[POLICY], book[APPROVAL], book[ANALYSIS]
    p['B6'], p['K6'], p['L6'] = 'Premier', 130, 125
    if stage >= 5:
        p['C6'], p['D6'], p['E6'] = 'WB-ALPHA', 0, 115
    if stage >= 6:
        p['F6'] = 45000
        source = "'已更正记录 · Corrected records'"
        for i in range(2, 8):
            metrics[f'D{i}'] = f'=(B{i}-{source}!B{i+14}-SUMIF(Policy!$A$6:$A$11,A{i},Policy!$F$6:$F$11))/{source}!B{i+2}'
    if stage >= 7:
        p['B3'] = 1
    rev = f"'{APPROVAL}'!$B$6=\"续约\""
    h2, h3, h4 = (f"'{APPROVAL}'!$H${i}" for i in (2, 3, 4))
    for row in range(6, 12):
        hotel = f'A{row}'
        match = f"MATCH({hotel},'{ANALYSIS}'!$A$2:$A$7,0)"
        adr = f"INDEX('{ANALYSIS}'!$D$2:$D$7,{match})"
        occ = f"INDEX('{ANALYSIS}'!$C$2:$C$7,{match})"
        signed = f"INDEX('{APPROVAL}'!$E$3:$E$8,MATCH({hotel},'{APPROVAL}'!$D$3:$D$8,0))"
        new = f'IF(B{row}="Premier",$K$6,{h3})'
        renewal = f'IF(B{row}="Premier",$L$6,{h4})'
        if stage >= 5:
            permit = f'AND($B$2<>"NONE",$B$2=C{row},D{row}=0)'
            renewal = f'IF({permit},E{row},{renewal})'
            if permit_leaks:
                new = f'IF({permit},E{row},{new})'
        normal = f'AND({occ}>={h2},IF({rev},AND({signed}=1,I{row}>={renewal}),I{row}>={new}))'
        if stage >= 8:
            archived = f'AND(PolicyArchive!B{row}>=PolicyArchive!$B$2,IF({rev},AND(PolicyArchive!D{row}=1,I{row}>=PolicyArchive!$B$4),I{row}>=PolicyArchive!$B$3))'
            p[f'G{row}'] = f'=IF(IF($B$4="M3",{archived},{normal}),1,0)'
            p[f'I{row}'] = f'=IF($B$4="M3",PolicyArchive!C{row},{adr})'
        else:
            p[f'G{row}'], p[f'I{row}'] = f'=IF({normal},1,0)', f'={adr}'
        if stage >= 7:
            p[f'H{row}'] = (f'=IF(AND(G{row}=1,COUNTIFS($G$6:$G$11,1,$I$6:$I$11,">"&I{row})'
                            f'+COUNTIFS($G$6:G{row},1,$I$6:I{row},I{row})<=$B$3),1,0)')
    a['B8'] = '=INDEX(Policy!$G$6:$G$11,MATCH(B3,Policy!$A$6:$A$11,0))'
    if stage >= 8:
        for idx, column in ((4,'B'),(5,'C')):
            prior = a[f'B{idx}'].value.removeprefix('=')
            a[f'B{idx}'] = f'=IF(Policy!B4="M3",INDEX(PolicyArchive!${column}$6:${column}$11,MATCH(B3,PolicyArchive!$A$6:$A$11,0)),{prior})'


def point(sheet, cell, value, origin=None):
    row = {'sheet': sheet, 'cell': cell, 'value': value}
    if isinstance(value, str):
        row['value_type'] = 'text'
    if origin is not None:
        row['milestone'] = origin
    return row


def probes(stage, earlier):
    scenarios = deepcopy(earlier)
    for case in scenarios:
        case['name'] = 'legacy_' + case['name']
        case['inputs'].append(point(POLICY,'B6','Standard'))
        if stage >= 6:
            case['inputs'].append(point(POLICY,'F6',0))
    base = deepcopy(earlier[0]['expected'])
    for row in base:
        location = row['sheet'], row['cell']
        if stage >= 6 and location in {(APPROVAL,'B5'), (ANALYSIS,'D2')}:
            row['value'] = 115
        if location == (APPROVAL,'B8'):
            row.update(value=0, milestone=4)
    base.append(point(POLICY,'G6',0,4))
    if stage >= 7:
        base += [point(POLICY,'H7',1,7), point(POLICY,'H9',0,7)]
    if stage == 8:
        base.append(point(POLICY,'H6',0,7))
    scenarios.insert(0,{'name':'current_baseline','inputs':[],'expected':base})

    def add(name, origin, result, *inputs, checks=()):
        expected = [(APPROVAL,'B8',result)] if result is not None else []
        scenarios.append({'name':name,'inputs':[point(*row) for row in inputs],
            'expected':[point(*row,origin) for row in [*expected,*checks]]})

    mode=(APPROVAL,'B6','续约')
    undo=[(POLICY,'F6',0)] if stage>=6 else []
    def gross(rate):
        return (ANALYSIS,'B2',rate*9000+45000)
    add('premier_129_denied',4,0,*undo,gross(129),checks=[(POLICY,'G6',0)])
    add('premier_130_allowed',4,1,*undo,gross(130),checks=[(POLICY,'G6',1)])
    add('premier_renewal_124_denied',4,0,*undo,mode,gross(124))
    add('premier_renewal_125_allowed',4,1,*undo,mode,gross(125))
    add('caller_reads_shared_verdict',4,1,(POLICY,'G6',1))
    if stage>=5:
        permit=(POLICY,'B2','WB-ALPHA')
        add('permit_renewal',5,1,*undo,mode,permit,gross(117))
        add('revoked_permit_denied',5,0,*undo,mode,permit,gross(117),(POLICY,'D6',1))
        add('wrong_permit_denied',5,0,*undo,mode,(POLICY,'B2','WRONG'),gross(117))
        add('permit_never_relaxes_new_signing',2,0,*undo,permit,gross(117))
        add('permit_requires_signed_history',5,0,*undo,mode,permit,gross(117),(APPROVAL,'E3',0))
    if stage>=6:
        add('refund_removal_restores_net_adr',6,0,(POLICY,'F6',0),checks=[(APPROVAL,'B5',120)])
        add('extra_refund_changes_approval',6,0,(POLICY,'F6',54000),mode,permit,checks=[(APPROVAL,'B5',114)])
        add('actual_refund_at_permit_boundary',6,1,mode,permit,checks=[(APPROVAL,'B5',115)])
    if stage>=7:
        add('quota_two',7,None,(POLICY,'B3',2),checks=[(POLICY,'H7',1),(POLICY,'H9',1)])
        add('quota_zero',7,None,(POLICY,'B3',0),checks=[(POLICY,'H7',0),(POLICY,'H9',0)])
        add('shared_eligibility_excludes_top_hotel',7,None,(POLICY,'G7',0),checks=[(POLICY,'H7',0),(POLICY,'H9',1)])
        add('batch_uses_price_order',7,None,(POLICY,'I9',200),checks=[(POLICY,'H7',0),(POLICY,'H9',1)])
        add('batch_tie_uses_roster_order',7,None,(POLICY,'I7',145),(POLICY,'I9',145),checks=[(POLICY,'H7',1),(POLICY,'H9',0)])
        add('quota_does_not_change_individual',7,1,(APPROVAL,'B3',SILVER),(POLICY,'B3',0))
    if stage==8:
        replay=(POLICY,'B4','M3')
        add('historical_willow_renewal',8,1,replay,mode,
            checks=[(APPROVAL,'B4',Fraction(9,10)),(APPROVAL,'B5',120),(POLICY,'G6',1)])
        add('historical_willow_new_signing',8,0,replay,checks=[(APPROVAL,'B5',120)])
        add('historical_ignores_revoked_current',8,1,replay,mode,(POLICY,'D6',1))
        add('historical_reads_archived_occupancy',8,0,replay,mode,(ARCHIVE,'B6',0.6),checks=[(APPROVAL,'B4',0.6)])
        add('historical_reads_archived_adr',8,0,replay,mode,(ARCHIVE,'C6',119),checks=[(APPROVAL,'B5',119)])
        add('historical_batch_uses_historical_eligibility',8,None,replay,mode,(POLICY,'B3',2),
            checks=[(POLICY,'H6',1),(POLICY,'H7',1),(POLICY,'H9',0)])
        add('current_renewal_without_permit',3,0,mode)
    return scenarios


def prepare_stage(submission, destination, package):
    submission,destination,package=map(Path,(submission,destination,package))
    meta=read_json(package/'private/milestone.json')
    if any(not (submission/f).is_file() or (submission/f).is_symlink() for f in OFFICE_FILES):
        raise ValueError('A successor needs all preceding Office files')
    destination.mkdir(parents=True)
    for file in OFFICE_FILES:
        copyfile(submission/file,destination/file)
    copyfile(package/'public/instruction.md',destination/'instruction.md')
    stage=meta['stage']
    entities, archived=original_snapshot()
    edit_workbook(destination/BOOK,lambda book:scaffold(book,stage,entities,archived))
    allowed={BOOK+':'+x for x in ('package_parts','sheets','protected_part:[Content_Types].xml',
             'protected_part:xl/workbook.xml','protected_part:xl/_rels/workbook.xml.rels','protected_part:xl/styles.xml')}
    prefix=f'{BOOK}:sheet:{POLICY}:'
    allowed.add(prefix+'structure')
    inputs = {
        5: [(2,c) for c in (1,2)] + [(r,c) for r in range(5,12) for c in (3,4,5)],
        6: [(r,6) for r in range(5,12)],
        7: [(3,c) for c in (1,2)] + [(r,8) for r in range(5,12)],
        8: [(4,c) for c in (1,2)],
    }
    allowed.update(f'{prefix}logical_row:{r-1}:column:{c}' for r,c in inputs.get(stage,[]))
    new_sheet = prefix if stage==4 else f'{BOOK}:sheet:{ARCHIVE}:' if stage==8 else None
    left,right=(snapshot(p,OFFICE_FILES) for p in (submission,destination))
    if any(key not in allowed and not (new_sheet and key.startswith(new_sheet)) and left.get(key)!=right.get(key)
           for key in left.keys()|right.keys()):
        raise ValueError('The authorized input scaffold altered prior work')
    return {'stage':stage,'scaffold':POLICY if stage<8 else ARCHIVE,
        'unchanged_file_bytes':[f for f in OFFICE_FILES if digest(submission/f)==digest(destination/f)]}


def build_phase(package, prior, base_oracle, functional, entities, archived, stage):
    public,private=package/'public',package/'private'
    ref=private/'reference'
    copytree(prior,public)
    edit_workbook(public/BOOK,lambda book:scaffold(book,stage,entities,archived))
    copytree(public,ref)
    edit_workbook(ref/BOOK,lambda book:install(book,stage))
    (public/'instruction.md').write_text(instruction(stage),encoding='utf-8')
    initial,final=(snapshot(location,OFFICE_FILES) for location in (public,ref))
    if initial.keys()!=final.keys():
        raise ValueError('Native field layout differs')
    oracle=deepcopy(base_oracle)
    cases=probes(stage,functional['scenarios'])
    objects=dict(functional['objects'])
    for col in ('G','I','H') if stage>=7 else ('G','I'):
        for i in range(6,12):
            addr=f'{col}{i}'
            binding={'file':BOOK,'location':{'kind':'cell','sheet':POLICY,'cell':addr}}
            key,=structural_key(ref,binding)
            objects[key]=f'{POLICY}!{addr}'
    oracle['functional_excel']={**functional,'objects':objects,'scenarios':cases}
    areas=[*oracle['allow_helpers_in'],f'{BOOK}:sheet:{POLICY}:']
    oracle['allow_helpers_in']=areas
    oracle['editable_files']=[BOOK]
    oracle['checks']=[{'object':key,'group':'required' if value!=initial[key] else 'protected','expected':value}
                      for key,value in sorted(final.items()) if key not in {area+'structure' for area in areas}]
    oracle['excel_publication']=publication_contract(ref,oracle,3)
    save_json(private/'oracle.json',oracle)
    origins={f"functional_excel:{case['name']}:{row['sheet']}!{row['cell']}":row.get('milestone',case.get('milestone',1))
             for case in cases for row in case['expected']}
    save_json(private/'milestone.json',{'stage':stage,'new_capabilities':[CAPABILITIES[stage-1]],
              'retained_capabilities':list(CAPABILITIES[:stage-1]),'formula_stage':{},'formula_check_stage':origins})
    save_json(private/'native_manifest.json',{'public_sha256':{f:digest(public/f) for f in OFFICE_FILES},
              'reference_sha256':{f:digest(ref/f) for f in OFFICE_FILES}})
    scores=grade(ref,read_json(private/'oracle.json'))
    if not scores['pass']:
        raise ValueError(f'{package.name} reference failed: {[r for r in scores["checks"] if not r["pass"]]}')
    save_json(private/'native_reference_evaluation.json',scores)
    return scores['groups']


def check_regression(output):
    final=output/'stage_08/private'
    with TemporaryDirectory(prefix='hotel_long_regression_') as tmp:
        area=Path(tmp)/'corrupt'
        copytree(final/'reference',area)
        # Install a faulty current rule on a phase-seven workbook, then replay
        # phase eight from that same structure so unrelated cells are unchanged.
        prior=output/'stage_07/private/reference'/BOOK
        copyfile(prior,area/BOOK)
        entities,archived=original_snapshot()
        def corrupt(book):
            scaffold(book,8,entities,archived)
            install(book,8,permit_leaks=True)
        edit_workbook(area/BOOK,corrupt)
        result=grade(area,read_json(final/'oracle.json'))
    failed=[row['object'] for row in result['checks'] if not row['pass']]
    if failed!=['functional_excel:permit_never_relaxes_new_signing:审批办理!B8']:
        raise ValueError(f'The injected fault must isolate one old approval regression: {failed}')
    return {'passed':False,'failed_checks':failed}


def build(output=DEFAULT):
    output=Path(output)
    if any((output/'agent_runs').glob('*/plan.json')):
        raise FileExistsError('Choose another fixture directory for a frozen trial')
    base_oracle,functional,_=corrected_specs(EARLIER)
    entities,archived=original_snapshot()
    prior=EARLIER/'stage_03/private/reference'
    scores={}
    for stage in range(4,9):
        package=output/f'stage_{stage:02d}'
        scores[package.name]=build_phase(package,prior,base_oracle,functional,entities,archived,stage)
        prior=package/'private/reference'
    result={'passed':True,'stages':scores,'regression_control':check_regression(output),
            'earlier_stages':str(EARLIER),'continuation_start':str(EARLIER_SUBMISSION),
            'capabilities':CAPABILITIES,'common_entry':'审批办理!B8'}
    save_json(output/'summary.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args()
    for phase,groups in build(args.output)['stages'].items():
        print(phase,groups)


if __name__=='__main__':
    main()
