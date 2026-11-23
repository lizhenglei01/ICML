"""Build and validate consecutive indirect-evidence hotel corrections offline."""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
from posixpath import normpath
from shutil import copyfile, copytree
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from docx import Document
from lxml import etree

from .hotel_deep_case import EXTRA_RESULTS, exact_reference, task_text
from .hotel_evidence import make_slots, native_bundles
from .hotel_graph import ARTIFACTS, RULES, TARGETS, amended, read_json, save_json
from .hotel_office import (
    FILES, STABLE_NOTE, build_layout, digest, grade, patch_reference,
    read_observations, replace_span, snapshot, structural_key,
)
from .hotel_propagation import write_propagation
from .hotel_revenue_evidence import GATE, NET, RECEIPTS, REFUNDS, REVENUE_LABELS, audit_revenue, shared_case
from .hotel_revenue_leaks import audit_concealed_values
from .hotel_visibility import audit_layout, solve_public


DEFAULT = ARTIFACTS / 'hotel_revenue_sequence'
SOLD = 'sold_room_nights'
SPREADSHEET_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RELATIONSHIP_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
OFFICE_FILES = tuple(FILES.values())


def next_case(graph, previous):
    """Use the first correct world for absolute truth independently of any submission."""
    entity = 'Amberfield Plaza Hotel'
    before_basis = previous['after_basis']
    event = {**previous['event'], 'id': 'Q4-PEER-002', 'entity': entity,
             'old_value': before_basis[SOLD][entity], 'new_value': 9600}
    after_basis = amended(before_basis, event)
    net = {hotel: after_basis[RECEIPTS][hotel] - after_basis[REFUNDS][hotel] for hotel in graph['entities']}
    after = {**exact_reference(graph, {**after_basis, NET: net}), 'policy_gate': GATE}
    proof = solve_public(graph, {**after_basis, 'policy_gate': GATE}, RULES, targets=after)
    if proof['missing_targets'] or proof['conflicts'] or {key: result['value'] for key, result in proof['proofs'].items()} != after:
        raise ValueError('Second correction disagrees with the independent ledger calculation')
    before = previous['after']
    if (event['old_value'], after['peer_adr_mean'], after['review_adr_floor'], after['qualified_count'],
            after['qualified_adr_mean'], after['average_daily_rate'][entity]) != (7800, 110, 115, 4, Fraction(535, 4), Fraction(845, 8)):
        raise ValueError('Second correction differs from the hand-calculated control')
    if after['peer_cohort'] != {'Maplecrest City Hotel'} or after['qualified'] - before['qualified'] != {'Cedarhaven Riverside Hotel'}:
        raise ValueError('Second correction does not produce the expected membership transitions')
    if after['revpar'] != before['revpar']:
        raise ValueError('Revenue and capacity were preserved, so RevPAR must remain fixed')
    changes = [{'node_id': key, 'before': value, 'after': after[key]} for key, value in before.items() if after[key] != value]
    return {'event': event, 'before_basis': before_basis, 'after_basis': after_basis,
            'before': before, 'after': after, 'old_proof': previous['new_proof'], 'new_proof': proof,
            'changes': changes, 'preserved_nodes': sorted(set(after) - {row['node_id'] for row in changes})}


def sequence_slots(graph, case):
    """Reserve a fixed issued-ledger row per hotel so stage two can retain every object."""
    current = case['after']
    layout = {'files': {
        'excel': {'observations': {key: current[key] for key in (SOLD, 'available_room_nights', REFUNDS)}, 'rules': []},
        'word': {'observations': {key: current[key] for key in ('occupancy_floor', 'adr_premium')}, 'rules': list(RULES)},
        'ppt': {'observations': {RECEIPTS: current[RECEIPTS]}, 'rules': []},
    }}
    slots, before, after = make_slots(graph, case, layout, metric_labels=REVENUE_LABELS, gate_text=GATE,
                                      extra_word_results=EXTRA_RESULTS)
    for slot in slots:
        if slot.get('chart') and slot['semantic_node_id'] == 'qualified':
            slot['series'] = '资格标记（1=入选，0=未入选） / Qualification flag (1=yes, 0=no)'
    index, original = next((index, row) for index, row in enumerate(slots) if row.get('history_id') == 'prior_posted_sold')
    del before[original['node_id']], after[original['node_id']]
    ledger = []
    for position, hotel in enumerate(graph['entities']):
        node = f'output.{SOLD}.{position:02}'
        ledger.append({**original, 'node_id': node, 'entity': hotel,
                       'label': f'{hotel} · 已登记已售间夜数 / Posted sold room nights'})
        before[node] = case['before'][SOLD][hotel]
        after[node] = case['after'][SOLD][hotel]
    slots[index:index + 1] = ledger
    return slots, before, after


def build_stage(directory, graph, case):
    graph = {**graph, 'node_values': {key: {'before': value, 'after': case['after'][key]}
                                     for key, value in case['before'].items()}}
    slots, before, after = sequence_slots(graph, case)
    native = build_layout(directory, slots, before, after, task_text(case['event']))
    bundles = native_bundles(graph, native['before_observations'], include_prior=True, gate_text=GATE)
    expected = {key: case['after'][key] for key in TARGETS}
    common = {'observations': {'event_new_sold': case['event']['new_value']}, 'rules': []}
    visibility = audit_layout(graph, bundles, expected, common=common)
    depths = {name: row['depth'] for name, row in visibility['proof']['proofs'].items()}
    if not visibility['passed'] or visibility['minimum_files'] != 3 or depths['qualified'] < 4:
        raise ValueError('The staged public evidence no longer supports the intended three-file task')
    revenue = audit_revenue(graph, bundles, case['after'][NET], True)
    leak_audit = audit_concealed_values(directory / 'public', case['after'][NET])
    if not leak_audit['passed']:
        raise ValueError('The public package reveals concealed net revenue')
    trace = write_propagation(directory, graph, case, native['oracle'])
    private = directory / 'private'
    for name, value in (('case', case), ('graph', graph), ('visibility', visibility), ('revenue_evidence', revenue),
                        ('concealed_value_check', leak_audit)):
        save_json(private / f'{name}.json', value)
    return {'event': case['event'], 'targets': expected, 'target_depths': depths,
            'minimum_files': visibility['minimum_files'], 'reference_grade': native['reference_grade']['groups'],
            'propagation_counts': trace['counts']}


def _sheet_part(workbook, sheet_name):
    metadata = etree.fromstring(workbook.read('xl/workbook.xml'))
    sheet, = metadata.findall(f'.//{SPREADSHEET_NS}sheet[@name="{sheet_name}"]')
    relationship = sheet.get(RELATIONSHIP_ID)
    targets = {(node.get('Id')): node.get('Target')
               for node in etree.fromstring(workbook.read('xl/_rels/workbook.xml.rels'))
               if node.get('Type', '').endswith('/worksheet')}
    target = targets[relationship]
    return target.lstrip('/') if target.startswith('/') else normpath('xl/' + target)


def _write_source_value(source, destination, location, expected_value):
    """Rewrite the single worksheet part, retaining chart data and Excel formula caches."""
    with ZipFile(source) as original, ZipFile(destination, 'w') as revised:
        sheet_part = _sheet_part(original, location['sheet'])
        for member in original.infolist():
            data = original.read(member)
            if member.filename == sheet_part:
                sheet = etree.fromstring(data)
                cell, = sheet.findall(f'.//{SPREADSHEET_NS}c[@r="{location["cell"]}"]')
                value, = cell.findall(f'{SPREADSHEET_NS}v')
                if cell.get('t', 'n') != 'n' or cell.find(f'{SPREADSHEET_NS}f') is not None:
                    raise ValueError('Authoritative source cell must be numeric and retain its identity')
                value.text = str(expected_value)
                data = etree.tostring(sheet)
            revised.writestr(member, data)


def prepare_stage_two(submission, destination, stage_two=DEFAULT / 'stage_02'):
    """Carry forward actual native outputs and inject only the newly authorized source value."""
    submission, destination, stage_two = map(Path, (submission, destination, stage_two))
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if any(not (submission / name).is_file() or (submission / name).is_symlink() for name in OFFICE_FILES):
        raise ValueError('Stage-one history is unavailable: expected three regular Office files')
    oracle = read_json(stage_two / 'private/oracle.json')
    event = read_json(stage_two / 'private/case.json')['event']
    binding, = [row for row in oracle['bindings'] if row.get('entity') == event['entity']
                and row['semantic_node_id'] == event['node_id'] and row['role'] == 'evidence']
    old_observation, = read_observations(submission, [binding])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='hotel_sequence_', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'carry'
        staging.mkdir()
        for file in (FILES['word'], FILES['ppt']):
            copyfile(submission / file, staging / file)
        _write_source_value(submission / FILES['excel'], staging / FILES['excel'], binding['location'], event['new_value'])
        copyfile(stage_two / 'public/instruction.md', staging / 'instruction.md')
        revised, = read_observations(staging, [binding])
        if revised['value'] != event['new_value']:
            raise ValueError('Stage-two authorized value did not survive native readback')
        old_objects = snapshot(submission, OFFICE_FILES)
        new_objects = snapshot(staging, OFFICE_FILES)
        expected_diff = structural_key(submission, binding) if old_observation['value'] != revised['value'] else set()
        actual_diff = {key for key in old_objects.keys() | new_objects.keys() if old_objects.get(key) != new_objects.get(key)}
        if actual_diff != set(expected_diff):
            raise ValueError('Event injection changed unrelated native objects')
        staging.rename(destination)
    return {'event': event, 'location': binding['location'], 'source_value_read_from_submission': old_observation['value'],
            'injected_value': revised['value'], 'changed_native_objects': sorted(actual_diff),
            'unchanged_file_bytes': [file for file in OFFICE_FILES if digest(submission / file) == digest(destination / file)]}


def _controls(packages):
    first, second = packages
    first_oracle, second_oracle = (read_json(package / 'private/oracle.json') for package in packages)
    first_reference = first / 'private/reference'
    with TemporaryDirectory(prefix='hotel_sequence_control_') as temporary:
        area = Path(temporary)
        clean = area / 'carry_clean'
        clean_injection = prepare_stage_two(first_reference, clean, second)
        equal_clean_start = snapshot(clean, OFFICE_FILES) == snapshot(second / 'public', OFFICE_FILES)
        if not equal_clean_start or set(clean_injection['unchanged_file_bytes']) != {FILES['word'], FILES['ppt']}:
            raise ValueError('Carried first-stage gold does not match second-stage teacher-forced start')
        altered = area / 'protected_error_from_stage_one'
        copytree(first_reference, altered)
        document = Document(altered / FILES['word'])
        paragraph, = [row for row in document.paragraphs if row.text == STABLE_NOTE]
        replace_span(paragraph, 0, len(STABLE_NOTE), STABLE_NOTE.replace('三年', '五年').replace('three years', 'five years'))
        document.save(altered / FILES['word'])
        before_failure = grade(altered, first_oracle)
        errors_before = {row['object'] for row in before_failure['checks'] if not row['pass']}
        carry = area / 'carry_with_prior_error'
        altered_injection = prepare_stage_two(altered, carry, second)
        correct_issued_edits = area / 'stage_two_correct_issued_edits'
        old_values = {row['node_id']: row['before'] for row in second_oracle['evidence_checks']}
        new_values = {row['node_id']: row['after'] for row in second_oracle['evidence_checks']}
        patch_reference(carry, correct_issued_edits, second_oracle['bindings'], old_values, new_values)
        after_failure = grade(correct_issued_edits, second_oracle)
        errors_after = {row['object'] for row in after_failure['checks'] if not row['pass']}
        if (not errors_before or errors_before != errors_after or after_failure['pass']
                or after_failure['groups']['required']['passed'] != after_failure['groups']['required']['total']):
            raise ValueError('The carry-forward control failed to preserve and report the prior protected error')
        absent_destination = area / 'must_not_be_created'
        try:
            prepare_stage_two(area / 'missing_submission', absent_destination, second)
        except ValueError as exc:
            absent_record = {'status': 'history_unavailable', 'reason': str(exc)}
        else:
            raise ValueError('Missing history should reject the carry-forward transition')
        if absent_destination.exists():
            raise ValueError('Missing history unexpectedly produced a substitute package')
        return {'gold_stage_one_equals_stage_two_start_after_injection': equal_clean_start,
                'clean_injection': clean_injection, 'altered_injection': altered_injection,
                'stage_one_protected_failure': before_failure['groups'],
                'stage_two_after_correct_current_edits': after_failure['groups'],
                'persistent_failed_objects': sorted(errors_after), 'missing_history_control': absent_record}


def build(root=DEFAULT):
    root = Path(root)
    graph, first, _ = shared_case()
    second = next_case(graph, first)
    packages = (root / 'stage_01', root / 'stage_02')
    stages = {package.name: build_stage(package, graph, case) for package, case in zip(packages, (first, second))}
    controls = _controls(packages)
    protocol = {
        'optimization_scope': 'Single fixed two-event indirect-evidence scenario. Model execution is a separate experiment.',
        'teacher_forced': {'stage_01': 'stage_01/public', 'stage_02': 'stage_02/public',
                           'input_source': 'Stage two starts from stage-one gold plus the second authorized source correction.'},
        'carry_forward': {'stage_01': 'stage_01/public', 'stage_02': 'Output of prepare_stage_two(actual_stage_one_submission, destination).',
                          'input_source': 'Keep submitted native history. Inject the second approved source and the new prompt only.'},
        'common_scoring': {'canonical_truth': 'Compare stage two submissions to stage_02/private/oracle.json in both arms.',
                           'outcomes': ['new event applied correctly', 'prior wrong objects retained or repaired', 'unrelated edits introduced'],
                           'timing': 'Freeze the stage-one submission, its file hashes and grade before introducing the next event.'},
        'unknown_history': {'treatment': 'Record history_unavailable on missing, unreadable or location-incompatible submissions.',
                            'substitution': 'Never substitute gold or repair inherited outputs in the carry-forward arm.',
                            'analysis': 'Retain unavailable cases in denominators and report attrition separately.'},
        'agent_calls': 0,
    }
    result = {'passed': True, 'stages': stages, 'controls': controls, 'agent_calls': 0}
    save_json(root / 'protocol.json', protocol)
    save_json(root / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = build(args.output)
    print(f"Built {len(result['stages'])} stages in {args.output}")


if __name__ == '__main__':
    main()
