"""Generate and test a three-milestone hotel workflow with native Office assets."""
from __future__ import annotations

import argparse
from pathlib import Path
from shutil import copy2, copytree

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.cell import get_column_letter

from .hotel_agent import file_hashes
from .hotel_dependency_scope import cell_key, points
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_milestone_formulas import verify_formula_function
from .hotel_office import FILES, build_layout, grade, snapshot
from .hotel_publication import workbook_signature
from .hotel_workflow_cases import (
    APPS, COLS, EVENT_ROWS, FEATURE_CASES, HOTELS, LEDGER, POLICY, REFUNDS, REVIEW, ROWS,
    SCENARIOS, case_inputs, compute, stage_features,
)

DEFAULT = ARTIFACTS / 'hotel_workflow_complexity'
OFFICE = tuple(FILES.values())
PACKAGES = {'m1': (1, 'linked'), 'm2': (2, 'linked'), 'm3_manual': (3, 'manual'), 'm3_linked': (3, 'linked')}
TITLES = {LEDGER: ('Hotel ID', 'Period', 'Sold nights', 'Inventory', 'Out of service', 'Gross room revenue',
                  'Tax', 'Credits', 'Posted (0/1)', 'Active (0/1)'),
          APPS: ('Hotel ID', 'Requested (0/1)', 'Priority (lower first)', 'Withdrawn (0/1)', 'Issued this period (0/1)',
                 'Prior signed (0/1)', 'Renewal request (0/1)', 'Approved ADR handoff', 'Approved eligible handoff (0/1)'),
          POLICY: ('Parameter', 'Value'),
          REFUNDS: ('Transaction ID', 'Hotel ID', 'Effective period', 'Amount', 'Posted (0/1)', 'Valid transaction (0/1)')}
PARAMETERS = ('Report month', 'Minimum occupancy', 'New-sign ADR', 'Period capacity', 'Renewal ADR', 'Renewal effective month')


RULES = {
    'A': '1 when the Ledger row is active and its period equals the report month.',
    'B': '1 when the hotel ID and period occur for the first time in ledger row order. A duplicate never replaces an earlier row.',
    'C': 'Sellable nights = max(0, inventory minus out-of-service nights).',
    'D': '1 when closures are between zero and inventory, sellable nights are positive and sold nights are between zero and sellable nights inclusive.',
    'E': '1 when gross, tax and credits are all nonnegative and tax plus credits do not exceed gross.',
    'F': 'Recognize gross minus tax and credits for in-period, first, amount-valid, posted rows. Otherwise use 0.',
    'G': 'For in-period, first, nights-valid rows, calculate sold divided by sellable. Otherwise use 0.',
    'H': 'For in-period, first, nights-valid rows with positive sold nights, divide recognized net by sold nights. Otherwise use 0.',
    'I': '1 when the row is in period, first, nights-valid, amount-valid and posted.',
    'J': 'Return the first problem in this order: 1 out of period/inactive, 2 duplicate, 3 invalid nights, 4 invalid amounts, 5 unposted. Use 0 if ready.',
    'K': '1 when the same row of Applications requests review and the ledger is ready.',
    'L': '1 if a request is received and meets the Policy minimum occupancy and new-sign ADR (inclusive).',
    'M': '1 for a requested withdrawal on an unissued request. Withdrawal cannot revoke an issued historical decision.',
    'N': 'A candidate is new-sign eligible, not effectively withdrawn and not already issued.',
    'O': 'Rank only candidates (1 first, 0 for non-candidates). Lower numeric priority comes first; break ties by workbook row order.',
    'P': 'A candidate is reserved when its rank fits the available seats. Available seats are max(0, period capacity minus issued count).',
    'Q': 'Waitlist rank is max(0, priority rank minus available seats).',
    'R': 'Read the immutable issued flag for the current period from Applications. A changed qualification, withdrawal or request must not change it.',
    'S': 'Status priority: 3 issued, 2 reserved, 1 waitlisted, 0 otherwise.',
    'T': 'Report remaining available seats after reservations on every review row.',
    'U': 'Count accepted posted refund/reversal rows for the same hotel and report month, if its ledger row is in period and first. Ignore pending, zero or invalid IDs. Accept the earliest valid row per positive transaction ID across the log (hotel IDs 1–4, nonzero amount). A negative amount is a reversal.',
    'V': 'Sum accepted signed amounts for the hotel and report month. Limit the effective deduction to the range from zero through recognized net.',
    'W': 'Corrected net = recognized net minus effective refund. Preserve the original recognized-net calculation.',
    'X': 'If the ledger row is ready with positive sold nights, divide corrected net by sold nights. Otherwise use zero.',
    'Y': 'Renewal policy applies when requested, tagged as renewal, previously signed, in the report period and the period is at or after the renewal effective month. Otherwise apply the new-sign floor.',
    'Z': 'Reassess received applications on the original occupancy and the accepted ADR at the applicable inclusive floor.',
    'AA': 'Release a prior reservation when that hotel is no longer in the revised candidate pool. An issued seat remains committed.',
    'AB': 'Rank revised candidates in the original priority and tie order. The received request must be approved for revision, unwithdrawn, and unissued.',
    'AC': 'Assign revised reservations to the highest revised candidates who fit within the original capacity minus issued seats.',
    'AD': 'Publish revised status using the same codes: 3 issued, 2 reserved, 1 waitlisted, 0 otherwise.',
}


def instructions(stage, mode):
    header = f'# 酒店审批工作簿 · 第 {stage} 轮 / Hotel approval workbook · M{stage}'
    rules = '\n'.join(f'{i}. **{label}** — {RULES[col]}' for i, (col, label) in enumerate(stage_features(stage), 1))
    shared = (
        '用 Excel 公式完成 Review 中本轮新增的功能，输入更正后结果应能重新计算。保留之前的功能及原始记录。\n\n'
        'Implement the following ten output columns in Review for every existing data row. Keep the headers, '
        'row order, input data, earlier functions and original file layout. The data rows in Review, Applications '
        'and Ledger are aligned. All outputs must remain live native Excel formulas. Use the existing input '
        'sheets to recalculate when fields or policy inputs change. You may use auxiliary formulas on Review and, '
        'for refund validation, the existing Valid transaction column on Refunds. Binary inputs are 0/1.'
    )
    publication = (
        'Edit the existing Word **Current published decisions** item with the four hotel statuses from the current '
        'Excel report in Alder, Birch, Cedar, Dale order, using the format `Alder=<code>, Birch=<code>, Cedar=<code>, Dale=<code>`. '
        'Keep its label and all other Word content. In PPT update the existing **Reserved or issued count**, '
        'all four points of the editable status chart and the matching companion table. Count status 2 and 3 as occupied. '
        'Keep chart labels, layout and unrelated content. This is an issued snapshot of the current Excel inputs.'
    )
    extra = {
        1: 'Edit only Excel. Word and PPT are read-only at this milestone.',
        2: publication,
        3: publication + ('\n\nThis site uses a reviewed handoff. In **Reassessed eligible**, take the accepted ADR '
                          'from Applications → Approved ADR handoff. Revised candidate membership consumes the separately '
                          'approved eligibility from Applications → Approved eligible handoff. A finalized operator handoff '
                          'can legitimately differ from the draft calculation.'
                          if mode == 'manual' else
                          '\n\nThis site uses automatic processing. In **Reassessed eligible**, take the accepted ADR '
                          'from Review → Corrected ADR. Revised candidate membership consumes the resulting reassessment. '
                          'The two Applications handoff fields are for other sites and have no role here.'),
    }
    return f'{header}\n\n{shared}\n\n## 本轮功能 / Milestone requirements\n\n{rules}\n\n{extra[stage]}\n'


def slot_spec():
    specs = [
        {'node_id': 'current', 'file': 'word', 'role': 'output', 'label': 'Current published decisions'},
        {'node_id': 'archive', 'file': 'word', 'role': 'context', 'label': 'June archive / Fixed historical record'},
        {'node_id': 'occupied', 'file': 'ppt', 'role': 'output', 'label': 'Reserved or issued count'},
    ]
    specs.extend({'node_id': f'status_{name}', 'role': 'output', 'file': 'ppt', 'label': name,
                  'chart': 'Hotel application status', 'category': name,
                  'series': 'Status (0 none, 1 waitlist, 2 reserve, 3 issued)'} for name in HOTELS)
    return specs


def snapshot_values(stage, mode):
    states = compute(case_inputs(SCENARIOS['current'], stage, mode), mode)
    statuses = [row[29 if stage == 3 else 18] if stage >= 2 else 0 for row in states]
    return {'archive': 'June signed record: Alder and Cedar.',
            'current': ', '.join(f'{hotel}={state}' for hotel, state in zip(HOTELS, statuses)) if stage >= 2 else 'Pending',
            'occupied': sum(status >= 2 for status in statuses),
            **{f'status_{hotel}': status for hotel, status in zip(HOTELS, statuses)}}


def style_header(sheet):
    for cell in sheet[1]:
        cell.font = Font(color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor='244865')
        sheet.column_dimensions[cell.column_letter].width = max(15, len(cell.value) + 3)
    sheet.freeze_panes = 'B2'


def formulas(r, stage, mode):
    lines = [
        f'--AND(Ledger!J{r}=1,Ledger!B{r}=Policy!$B$2)',
        f'--(COUNTIFS(Ledger!$A$2:A{r},Ledger!A{r},Ledger!$B$2:B{r},Ledger!B{r})=1)',
        f'MAX(0,Ledger!D{r}-Ledger!E{r})',
        f'--AND(Ledger!E{r}>=0,Ledger!E{r}<=Ledger!D{r},Ledger!C{r}>=0,Ledger!C{r}<=C{r},C{r}>0)',
        f'--AND(MIN(Ledger!F{r}:H{r})>=0,Ledger!F{r}>=SUM(Ledger!G{r}:H{r}))',
        f'IF(AND(A{r}=1,B{r}=1,E{r}=1,Ledger!I{r}=1),Ledger!F{r}-SUM(Ledger!G{r}:H{r}),0)',
        f'IF(AND(A{r}=1,B{r}=1,D{r}=1),Ledger!C{r}/C{r},0)',
        f'IF(AND(A{r}=1,B{r}=1,D{r}=1,Ledger!C{r}>0),F{r}/Ledger!C{r},0)',
        f'--AND(A{r}=1,B{r}=1,D{r}=1,E{r}=1,Ledger!I{r}=1)',
        f'IF(A{r}=0,1,IF(B{r}=0,2,IF(D{r}=0,3,IF(E{r}=0,4,IF(Ledger!I{r}=0,5,0)))))',
    ]
    if stage <= 1:
        return lines
    vacancy = 'MAX(0,Policy!$B$5-SUM($R$2:$R$5))'
    lines += [
        f'--AND(I{r}=1,Applications!B{r}=1)',
        f'--AND(K{r}=1,G{r}>=Policy!$B$3,H{r}>=Policy!$B$4)',
        f'--AND(Applications!B{r}=1,Applications!D{r}=1,R{r}=0)',
        f'--AND(L{r}=1,M{r}=0,R{r}=0)',
        priority_rank(r, 'N'),
        f'--AND(O{r}>0,O{r}<={vacancy})',
        f'MAX(0,O{r}-{vacancy})',
        f'--(Applications!E{r}=1)',
        f'IF(R{r}=1,3,IF(P{r}=1,2,IF(Q{r}>0,1,0)))',
        f'{vacancy}-SUM($P$2:$P$5)',
    ]
    if stage == 2:
        return lines
    args = f'Refunds!$F$2:$F$7,1,Refunds!$B$2:$B$7,Ledger!A{r},Refunds!$C$2:$C$7,Policy!$B$2'
    adr = f'Applications!H{r}' if mode == 'manual' else f'X{r}'
    decided = f'Applications!I{r}' if mode == 'manual' else f'Z{r}'
    return lines + [
        f'IF(AND(A{r}=1,B{r}=1),COUNTIFS({args}),0)',
        f'MIN(F{r},MAX(0,SUMIFS(Refunds!$D$2:$D$7,{args})))',
        f'F{r}-V{r}',
        f'IF(AND(I{r}=1,Ledger!C{r}>0),W{r}/Ledger!C{r},0)',
        f'--AND(Applications!B{r}=1,Applications!F{r}=1,Applications!G{r}=1,Ledger!B{r}=Policy!$B$2,Policy!$B$2>=Policy!$B$7)',
        f'--AND(K{r}=1,G{r}>=Policy!$B$3,{adr}>=IF(Y{r}=1,Policy!$B$6,Policy!$B$4))',
        f'--AND(P{r}=1,AE{r}=0)',
        priority_rank(r, 'AE'),
        f'--AND(AB{r}>0,AB{r}<={vacancy})',
        f'IF(R{r}=1,3,IF(AC{r}=1,2,IF(AB{r}>0,1,0)))',
        f'--AND(K{r}=1,{decided}=1,M{r}=0,R{r}=0)',
    ]


def priority_rank(row, col):
    return (f'IF({col}{row}=1,COUNTIFS(${col}$2:${col}$5,1,Applications!$C$2:$C$5,"<"&Applications!C{row})'
            f'+COUNTIFS(${col}$2:{col}{row},1,Applications!$C$2:C{row},Applications!C{row}),0)')


def set_inputs(book, raw):
    for sheet, records in raw.items():
        for row, record in enumerate(records, 2):
            for col, value in enumerate(record, 2 if sheet == POLICY else 1):
                book[sheet].cell(row, col, value=value)


def workbook(path, level, inputs_stage, mode):
    book = Workbook()
    book.remove(book.active)
    for sheet, fields in TITLES.items():
        book.create_sheet(sheet).append(fields)
    sheet = book.create_sheet(REVIEW)
    sheet.append([label for _, label in COLS] + ['Optional helper', 'Hotel'])
    for name in HOTELS:
        sheet.append([0] * 31 + [name])
    set_inputs(book, case_inputs(SCENARIOS['current'], inputs_stage, mode))
    for r, label in enumerate(PARAMETERS, 2):
        book[POLICY][f'A{r}'] = label
    for r in EVENT_ROWS:
        book[REFUNDS][f'F{r}'] = ('=--AND(A{0}>0,B{0}>=1,B{0}<=4,D{0}<>0,E{0}=1,'
                                    'SUMIF($A$1:A{1},A{0},$F$1:F{1})=0)'.format(r, r - 1) if level >= 3 else 0)
    if level:
        for r in ROWS:
            for col, expr in enumerate(formulas(r, level, mode), 1):
                sheet.cell(r, col, '=' + expr)
    # Formula formatting exists before the exercise, so implementation needs
    # no unrelated style or XML changes.
    for r in ROWS:
        sheet[f'G{r}'].number_format = '0.00%'
    book[POLICY]['B3'].number_format = '0%'
    for table in book:
        style_header(table)
    book.save(path)
    book.close()


def input_diff(before, after):
    return {(sheet, f'{get_column_letter(c)}{r}'): value
            for sheet in sorted(after) for r, (old_row, new_row) in enumerate(zip(before[sheet], after[sheet]), 2)
            for c, (old, value) in enumerate(zip(old_row, new_row), 2 if sheet == POLICY else 1) if old != value}


def formula_probes(stage, mode):
    active = COLS[:stage * 10]
    probe_map = {code: set(FEATURE_CASES[code].split()) for code, _ in active}
    present = set().union(*probe_map.values()) | {'current'}
    baseline = case_inputs(SCENARIOS['current'], stage, mode)
    scenarios, origins = [], {}
    for name, case in SCENARIOS.items():
        if name not in present:
            continue
        raw = case_inputs(case, stage, mode)
        values = compute(raw, mode)
        wanted = {}
        for index, (code, _) in enumerate(active):
            if name not in probe_map[code]:
                continue
            for row, record in zip(ROWS, values):
                address = f'{code}{row}'
                wanted[REVIEW, address] = float(record[index]) if index in (6, 7, 23) else int(record[index])
                origins[f'functional_excel:{name}:{REVIEW}!{address}'] = 1 + index // 10
        changes = input_diff(baseline, raw)
        # Linked sites ignore the handoff even when those inputs are tested.
        if not changes and name != 'current':
            raise ValueError(f'Test {name} does not change an input')
        scenarios.append({'name': name, 'inputs': points(changes), 'expected': points(wanted)})
    objects = {cell_key(REVIEW, f'{code}{row}'): f'{REVIEW}!{code}{row}' for code, _ in active for row in ROWS}
    return {'file': FILES['excel'], 'objects': objects, 'scenarios': scenarios, 'flexible_formula_cells': True,
            'require_sensitivity': False, 'timeout_seconds': 120}, origins


def prepare_stage(submission, destination, package):
    destination.mkdir(parents=True)
    for filename in OFFICE:
        copy2(submission / filename, destination / filename)
    copy2(package / 'public/instruction.md', destination / 'instruction.md')
    stage, mode = PACKAGES[package.name]
    if stage == 3:
        path = destination / FILES['excel']
        book = load_workbook(path)
        raw = case_inputs(SCENARIOS['current'], stage, mode)
        for row, record in zip(EVENT_ROWS, raw[REFUNDS]):
            for col, value in enumerate(record, 1):
                book[REFUNDS].cell(row, col, value)
        for row, entry in zip(ROWS, raw[APPS]):
            book[APPS][f'H{row}'], book[APPS][f'I{row}'] = entry[7:9]
        book.save(path)
        book.close()
    return {'milestone': stage, 'integration': mode, 'transition': 'Load third-round refund log and handoff inputs when applicable'}


def build_package(package, predecessor):
    stage, mode = PACKAGES[package.name]
    public, private = package / 'public', package / 'private'
    ref = private / 'reference'
    rendered = build_layout(package, slot_spec(), snapshot_values(stage - 1, mode), snapshot_values(stage, mode),
                            instructions(stage, mode))
    workbook(public / FILES['excel'], stage - 1, stage, mode)
    workbook(ref / FILES['excel'], stage, stage, mode)
    if predecessor:
        for filename in OFFICE[1:]:
            copy2(predecessor / filename, public / filename)
    before, after = snapshot(public, OFFICE), snapshot(ref, OFFICE)
    if before.keys() != after.keys():
        raise ValueError(f'{package.name}: native object inventory changed')
    spec, origins = formula_probes(stage, mode)
    oracle = rendered['oracle']
    allowed = [f'{FILES["excel"]}:sheet:{REVIEW}:']
    ignored = {area + 'structure' for area in allowed}
    if stage == 3:
        for r in ROWS:
            ignored.add(cell_key(REVIEW, f'AE{r}'))
        allowed.append(f'{FILES["excel"]}:sheet:{REFUNDS}:')
        for r in EVENT_ROWS:
            ignored.add(cell_key(REFUNDS, f'F{r}'))
    oracle.update(
        functional_excel=spec, editable_files=list(OFFICE[:1] if stage == 1 else OFFICE),
        allow_helpers_in=allowed,
        checks=[{'object': key, 'expected': value, 'group': 'required' if before[key] != value else 'protected'}
                for key, value in sorted(after.items()) if key not in ignored],
        excel_publication={'file': FILES['excel'], 'cells': [],
                           'reference_workbook_part_hash': after[FILES['excel'] + ':protected_part:xl/workbook.xml'],
                           'default_calc_workbook_signature': workbook_signature(ref / FILES['excel'])},
    )
    save_json(private / 'oracle.json', oracle)
    save_json(private / 'milestone.json', {'stage': stage, 'new_capabilities': [label for _, label in stage_features(stage)],
        'retained_capabilities': [label for _, label in COLS[:(stage - 1) * 10]], 'formula_check_stage': origins, 'formula_stage': {}})
    manifest = read_json(private / 'native_manifest.json')
    manifest.update(public_sha256=file_hashes(public, OFFICE), reference_sha256=file_hashes(ref, OFFICE),
                    native_formula='Input worlds recalculate live Excel formulas in LibreOffice.')
    save_json(private / 'native_manifest.json', manifest)
    return oracle


def validate_package(package, oracle):
    private = package / 'private'
    scores = {}
    for name, path in [('reference', private / 'reference'), ('unedited', package / 'public')]:
        score = grade(path, oracle)
        save_json(private / f'native_{name}_evaluation.json', score)
        failures = [item for item in score['checks'] if not item['pass']]
        if score['pass'] != (name == 'reference') or any(item['group'] == 'structure' for item in failures):
            raise ValueError(f'{package.name} / {name} unexpected score: {failures[:8]}')
        scores[name] = score['groups']
    return scores


def controls(output):
    package = output / 'm3_linked'
    oracle = read_json(package / 'private/oracle.json')
    cases = {
        'refund_without_reapproval': (REVIEW, 'Z2', '=L2', 'functional_excel:current:Review!Z2'),
        'reevaluation_without_reallocation': (REVIEW, 'AC3', '=P3', 'functional_excel:current:Review!AC3'),
        'historical_archive_recomputed': (REVIEW, 'R4', '=L4', 'functional_excel:archive_removes_capacity:Review!R4'),
        'old_new_sign_floor_replaced': (REVIEW, 'L2', '=--AND(K2=1,G2>=Policy!$B$3,H2>=Policy!$B$6)',
                                        'functional_excel:below_new_adr:Review!L2'),
    }
    result = {}
    for name, (sheet, address, value, required_failure) in cases.items():
        directory = output / 'controls' / name
        copytree(package / 'private/reference', directory)
        path = directory / FILES['excel']
        book = load_workbook(path)
        book[sheet][address] = value
        book.save(path)
        book.close()
        score = grade(directory, oracle)
        save_json(directory / 'score.json', score)
        failed = [item['object'] for item in score['checks'] if not item['pass']]
        if score['pass'] or required_failure not in failed or score['groups']['structure']['total']:
            raise ValueError(f'Fault control {name} missed: {failed[:24]}')
        result[name] = failed
    return result


def audit_pair(manual_public, linked_public):
    manual, linked = (snapshot(path, OFFICE) for path in (manual_public, linked_public))
    handoff_fields = {cell_key(APPS, f'{col}{row}') for row in ROWS for col in ('H', 'I')}
    differences = {key for key in manual.keys() & linked.keys() if manual[key] != linked[key]}
    if manual.keys() != linked.keys() or not differences or differences - handoff_fields:
        raise ValueError(f'Unexpected differences in paired assets: {sorted(differences - handoff_fields)}')
    instructions = [(path / 'instruction.md').read_text(encoding='utf-8').partition('\n\nThis site uses ')
                    for path in (manual_public, linked_public)]
    if not all(separator for _, separator, _ in instructions) or instructions[0][0] != instructions[1][0]:
        raise ValueError('The paired instructions changed outside the description of the handoff')
    return {'differing_source_objects': sorted(differences), 'same_other_instructions': True}


def verify_design(output):
    from .hotel_workflow_cases import ledger, app, refund, Case

    event = case_inputs(SCENARIOS['current'], 3, 'linked')
    current = compute(event, 'linked')
    assert current[0][7] == 130 and current[0][23] == 119
    assert (current[0][15], current[0][26], current[0][28], current[1][28], current[2][29]) == (1, 1, 0, 1, 3)
    signed = compute(case_inputs(Case('signed', app(issued=1)), 3, 'linked'), 'linked')
    assert (signed[0][23], signed[0][26], signed[0][29], signed[1][28]) == (119, 0, 3, 0)
    exact = compute(case_inputs(Case('edge', refund(amount=800) + ledger(sold=80)), 3, 'linked'), 'linked')
    assert exact[0][23] == 120 and exact[0][25] == 1
    manual = compute(case_inputs(SCENARIOS['stale_handoff_decision'], 3, 'manual'), 'manual')
    linked = compute(case_inputs(SCENARIOS['stale_handoff_decision'], 3, 'linked'), 'linked')
    assert (manual[0][28], linked[0][28]) == (1, 0)
    assert all(6 <= len(set(names.split())) <= 10 for names in FEATURE_CASES.values())
    variants = {}
    for name in ('m3_manual', 'm3_linked'):
        variants[name] = read_json(output / name / 'private/oracle.json')['functional_excel']
    assert set(variants['m3_manual']['objects']) == set(variants['m3_linked']['objects'])
    assert [(case['name'], [f"{e['sheet']}!{e['cell']}" for e in case['expected']])
            for case in variants['m3_manual']['scenarios']] == [
                (case['name'], [f"{e['sheet']}!{e['cell']}" for e in case['expected']])
                for case in variants['m3_linked']['scenarios']]
    paired = list(zip(variants['m3_manual']['scenarios'], variants['m3_linked']['scenarios']))
    handoff_probes = [a['name'] for a, b in paired if a['expected'] != b['expected']]
    if set(handoff_probes) != {'stale_handoff_adr', 'stale_handoff_decision'}:
        raise ValueError(f'Unmatched business targets: {handoff_probes}')
    return {'matched_input_audit': audit_pair(output / 'm3_manual/public', output / 'm3_linked/public'),
            'event': {'original_adr': 130, 'corrected_adr': 119, 'released': 'Alder', 'promoted': 'Birch',
                      'archived_status': 'Cedar=3'}, 'signed_variant': 'Alder stays issued, Birch stays unreserved',
            'inclusive_renewal_boundary': 120, 'matched_answer_worlds': len(paired) - len(handoff_probes),
            'diagnostic_handoff_worlds': handoff_probes}


def trace_integration(workbook_file, mode):
    """Intervene on individual draft outputs to verify both data-flow edges."""
    baseline = case_inputs(SCENARIOS['current'], 3, 'manual')
    override = case_inputs(SCENARIOS['current'], 3, 'manual')
    override[APPS][0][7:9] = [130, 1]
    initial, propagated = (compute(records, 'manual') for records in (baseline, override))
    targets = {(REVIEW, 'Z2'): (0, 25), (REVIEW, 'AA2'): (0, 26), (REVIEW, 'AC2'): (0, 28),
               (REVIEW, 'AD2'): (0, 29), (REVIEW, 'AD3'): (1, 29)}

    def expected(records):
        return {key: records[row][col] for key, (row, col) in targets.items()}

    initial_expected = expected(initial)
    resulting = expected(propagated if mode == 'linked' else initial)
    rank_only = {key: number for key, number in resulting.items() if key != (REVIEW, 'Z2')}
    return verify_formula_function(workbook_file, [
        {'name': 'current', 'inputs': {}, 'expected': initial_expected},
        {'name': 'intervene_on_corrected_adr', 'inputs': {(REVIEW, 'X2'): 130}, 'expected': resulting},
        {'name': 'intervene_on_reassessment', 'inputs': {(REVIEW, 'Z2'): 1}, 'expected': rank_only},
    ], require_sensitivity=False)


def build(output):
    output.mkdir(parents=True)
    packages = {}
    for name, (stage, mode) in PACKAGES.items():
        predecessor = {1: None, 2: output / 'm1/private/reference', 3: output / 'm2/private/reference'}[stage]
        oracle = build_package(output / name, predecessor)
        result = validate_package(output / name, oracle)
        packages[name] = {
            'reference': result['reference'], 'unedited': result['unedited'], 'mode': mode,
            'new_features': {label: len(FEATURE_CASES[code].split()) for code, label in stage_features(stage)},
            'input_worlds': len(oracle['functional_excel']['scenarios']),
        }
        print('HOTEL_WORKFLOW_VALIDATED', name, flush=True)
    verified = verify_design(output)
    for arm, mode in [('m3_manual', 'manual'), ('m3_linked', 'linked')]:
        private = output / arm / 'private'
        trace = trace_integration(private / 'reference' / FILES['excel'], mode)
        save_json(private / 'integration_trace.json', trace)
        if not trace['pass']:
            raise ValueError(f'{arm} expected dependency path failed: {trace}')
    faults = controls(output)
    save_json(output / 'summary.json', {'passed': True, 'packages': packages, 'verified_business_examples': verified,
        'rejected_controls': faults, 'feature_cases': FEATURE_CASES,
        'comparison': {'manually_confirmed_edges': ['corrected ADR → reassessment', 'reassessment → revised nominations'],
                       'linked_mode': 'Resolve both edges from the preceding computed workflow outputs.'}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == '__main__':
    main()
