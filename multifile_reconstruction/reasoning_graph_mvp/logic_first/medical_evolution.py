"""Three native Office milestones that extend a clinic's shared booking interface."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from shutil import copyfile, copytree
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook

from ...evidence_control import table
from ...evidence_domain_cases import DATASET, _declared_links, _provenance
from schema_cf.input import load
from .hotel_graph import ARTIFACTS, read_json, save_json
from .hotel_office import digest, grade, snapshot
from .hotel_publication import workbook_signature
from worldloom.models import ArtifactIR, ArtifactSection, Chart, ChartKind
from worldloom.presentation import READER
from worldloom.render import docx, pptx


DEFAULT = ARTIFACTS / 'medical_evolution'
FILES = ('medical_schedule.xlsx', 'medical_policy.docx', 'medical_capacity.pptx')
CAPABILITIES = ('shift_lookup', 'new_appointment', 'reschedule_at_the_same_interface')
HEADERS = ('Appointment ID', 'Doctor ID', 'Day', 'Slot', 'Room')
SHIFTS = [(1,1,1), (1,1,2), (1,1,3), (1,2,1), (1,2,3), (2,1,2), (2,1,3), (2,2,1), (2,2,2)]
ACTIVE_SEED = [('EXIST-02', 2, 1, 2, 'A'), ('EXIST-03', 1, 1, 3, 'B')]
PENDING = ('AP-01', 0, 0, 0, 'Pending')
BOOKED = ('AP-01', 1, 1, 1, 'A')
MOVED = ('AP-01', 1, 1, 2, 'B')
INPUTS = dict(doctor='B2', day='B3', slot='B4', room='B5', mode='B6', appointment='B7')
BASE_REQUEST = dict(doctor=1, day=1, slot=1, room='A', mode='NEW', appointment='NONE')


def provenance():
    dataset = load(DATASET / 'medical/case1')
    links = {'Appointment.Physician': 'Physician.EmployeeID'}
    _declared_links(dataset, links)
    appointments = [row for row in dataset.rows['Appointment'] if
                    row['AppointmentID'] in (13216584, 26548913) and
                    datetime.fromisoformat(row['Start']) < datetime.fromisoformat(row['End'])]
    if {(row['Physician'], row['ExaminationRoom']) for row in appointments} != {(1, 'A'), (2, 'B')}:
        raise ValueError('Unexpected public clinic source')
    return _provenance(dataset, ('Physician', 'Appointment'), links,
        [{'AppointmentID': row['AppointmentID'], 'doctor': row['Physician'], 'room': row['ExaminationRoom']}
         for row in appointments],
        {'reused': 'Doctor identifiers and room labels from two public case rows',
         'synthetic': 'Shift matrix, enabled-room flags, active demonstration ledger, AP-01 and all three milestones',
         'excluded': 'Patient and nurse identifiers, patient-level information, and unrelated appointments'})


def active_rows(level):
    return [BOOKED if level == 2 else MOVED if level == 3 else PENDING, *ACTIVE_SEED]


def decision(request, shifts, active, rooms, *, allow_move):
    physician, day, slot, room = (request[key] for key in ('doctor', 'day', 'slot', 'room'))
    on_duty = (physician, day, slot) in shifts
    result = {'B10': int(on_duty)}
    mode, appointment = request['mode'], request['appointment']
    handled = mode == 'NEW' or (allow_move and mode == 'MOVE' and
              sum(row[0] == appointment and row[1] == physician for row in active) == 1)
    occupied = any(
        row[2:4] == (day, slot) and (row[1] == physician or row[4] == room)
        and (mode != 'MOVE' or row[0] != appointment) for row in active)
    result['B11'] = int(handled and on_duty and rooms.get(room, False) and not occupied)
    return result


def workbook(path, level):
    book = Workbook()
    request = book.active
    request.title = 'Request'
    request.append(['Clinic booking interface / 门诊预约入口', 'Value / 值'])
    for i, (key, value) in enumerate(BASE_REQUEST.items(), 2):
        request.cell(i, 1, key)
        request.cell(i, 2, value)
    request['A10'], request['A11'] = 'Doctor on duty (1/0)', 'Decision (1/0)'
    request['B10'].number_format = request['B11'].number_format = '0'
    shift = book.create_sheet('Shifts')
    shift.append(['Doctor ID', 'Day', 'Slot', 'On duty (1/0)'])
    for doctor in (1, 2):
        for day in (1, 2):
            for slot in (1, 2, 3):
                shift.append([doctor, day, slot, int((doctor, day, slot) in SHIFTS)])
    rooms = book.create_sheet('Rooms')
    rooms.append(['Room', 'Enabled (1/0)'])
    for room in ('A', 'B'):
        rooms.append([room, int(level >= 2)])
    active = book.create_sheet('Active')
    active.append(HEADERS)
    for row in active_rows(level):
        active.append(row)
    archive = book.create_sheet('History')
    archive.append(HEADERS)
    archive.append(BOOKED if level >= 2 else PENDING)
    if level >= 1:
        request['B10'] = '=IF(COUNTIFS(Shifts!A2:A13,B2,Shifts!B2:B13,B3,Shifts!C2:C13,B4,Shifts!D2:D13,1)>0,1,0)'
    enabled = 'COUNTIFS(Rooms!A2:A3,B5,Rooms!B2:B3,1)=1'
    doctor = 'Active!B2:B4,B2,Active!C2:C4,B3,Active!D2:D4,B4'
    room = 'Active!E2:E4,B5,Active!C2:C4,B3,Active!D2:D4,B4'
    new = f'AND(B10=1,{enabled},COUNTIFS({doctor})=0,COUNTIFS({room})=0)'
    if level >= 2:
        request['B11'] = f'=IF(AND(B6="NEW",{new}),1,0)'
    if level >= 3:
        other = 'Active!A2:A4,"<>"&B7'
        known = 'COUNTIFS(Active!A2:A4,B7,Active!B2:B4,B2)=1'
        move = f'AND(B10=1,{enabled},{known},COUNTIFS({doctor},{other})=0,COUNTIFS({room},{other})=0)'
        request['B11'] = f'=IF(IF(B6="MOVE",{move},AND(B6="NEW",{new})),1,0)'
    for sheet in book:
        for col in 'ABCDE':
            sheet.column_dimensions[col].width = 22
    request.column_dimensions['A'].width = 40
    book.save(path)
    book.close()


def policy_documents(directory):
    metadata = {'worldloom_created': '2026-01-01T00:00:00+00:00'}
    policy = ArtifactIR(id='medical-policy', intent_id='clinic-policy', title='Clinic booking policy / 门诊预约口径',
        metadata=metadata, sections=[ArtifactSection(heading='Availability and conflicts / 出诊与冲突', body=(
            '每个预约占用一个时段。医生须在当日该时段出诊，所选诊室须开放。医生和诊室都不能在同一日期和时段重复占用。'
            'Active 是当前预约，History 是正式确认时的原记录。历史行不会随当前安排而变化。 / '
            'Each booking uses one slot. The physician must be on duty and the room must be enabled. A different appointment '
            'in the same day and slot conflicts if it uses the same physician or the same room. Active is the live ledger. '
            'History records the original confirmation and is fixed.'))])
    (directory / FILES[1]).write_bytes(docx.render(policy, presentation=READER))
    availability = table('rooms', 'Enabled examination rooms / 开放诊室', [('A', 1), ('B', 1)], 'Enabled (1/0)')
    chart = Chart(key='rooms-availability', title='Room availability', kind=ChartKind.BAR,
                  table='rooms', series=['value'], value_axis='Enabled (1/0)')
    capacity = ArtifactIR(id='room-capacity', intent_id='clinic-capacity', title='Room availability / 诊室开放表',
        metadata=metadata, sections=[ArtifactSection(heading='Enabled rooms / 开放诊室', table=availability, charts=[chart])])
    (directory / FILES[2]).write_bytes(pptx.render(capacity, profile=READER, artifact_type='source_summary'))


def instruction(stage):
    tasks = (
        '''## 排班查询 / Build shift lookup

在 Request 的 B10 建立 Excel 原生公式。B2 医生、B3 日期编号、B4 时段编号对应 Shifts 的三列。只有对应行 On duty 为 1 才返回 1，否则返回 0。要随这些输入或排班变化重新计算。只填 B10。

Create a native formula in Request!B10 that returns 1 when the selected doctor (B2) is on duty on the selected day (B3) and slot (B4) according to Shifts, and 0 otherwise. It must recalculate when the inputs or Shifts change. Fill only B10.''',
        '''## 普通预约 / Add new booking

从 PPT 把 A、B 诊室开放状态填入 Rooms 已有行。在 Request!B11 做可重算的 NEW 审批公式，使用上一轮 B10 的结果，根据 Word 的诊室、医生冲突规则判断 B5 的房间。检查预约 AP-01（医生 1、日期 1、时段 1、诊室 A），把批准后的记录写进 Active 和 History 的 AP-01 行。两张表分别保存当前和原始记录。保持 B2:B7 初始输入、第一轮功能及其他材料。

Populate the existing Rooms flags from PowerPoint. Add a native decision formula returning 1 or 0 in Request!B11 for NEW mode. Use the earlier on-duty result B10, the room selected in B5, and the Word booking policy. Check AP-01 for doctor 1, day 1, slot 1, room A and enter the accepted booking in the reserved AP-01 rows of Active and History. Those rows record the live booking and its original confirmation. Preserve the starting B2:B7 values, the shift function, and unrelated material.''',
        '''## 改期 / Upgrade the same interface to reschedule

升级同一个 Request!B11，让 B6 可选择 NEW 或 MOVE。NEW 沿用普通预约规则。MOVE 用 B7 指定预约编号，只能改同一医生名下已有的那条预约。检查目标时段时，仅排除该预约本身，其他预约仍正常占用医生和诊室。未知编号或医生不同返回 0。两种模式都要使用 B10 和 Rooms，返回 1 或 0。

本次将 AP-01 改到医生 1、日期 1、时段 2、诊室 B。确认后仅更新 Active 同一行，保留 History 原时段和原诊室。完成后保留 B2:B7 的初始输入，旧模式与排班查询均继续可用。可以在 Request 内增加辅助单元格，保持入口位置及其他材料。

Upgrade the same Request!B11 so B6 handles NEW and MOVE. NEW keeps all existing rules. MOVE takes the appointment ID in B7 and requires an existing appointment for the selected physician. When checking the destination, ignore only that same appointment; all other physician and room conflicts still count. Return 0 for an unknown ID or different physician. Both modes use B10 and Rooms and return 1 or 0.

For this release, move AP-01 to doctor 1, day 1, slot 2, room B. Write the confirmed move to the same Active row. Keep History at the original slot and room. Restore the starting B2:B7 values after working and preserve the older booking and shift features. Helpers may be added within Request. Preserve the interface addresses and other assets.''',
    )
    return ('# 门诊预约工作台 / Clinic scheduling workspace\n\n' + tasks[stage - 1]
            + '\n\n交付时只修改 medical_schedule.xlsx。Word 和 PPT 是只读依据，两份文件保持原样。'
            ' Save edits only to medical_schedule.xlsx. The Word and PowerPoint files are read-only evidence; leave both unchanged.\n')


def scenarios_for(stage):
    scenarios = []

    def add(name, milestone, request=None, *, change=(), shifts=None, overnight=None, rooms=None):
        supplied = request or {}
        state = {**BASE_REQUEST, **supplied}
        active = overnight if overnight is not None else active_rows(stage)
        results = decision(state, SHIFTS if shifts is None else shifts, active, rooms or {'A': True, 'B': True}, allow_move=stage == 3)
        inputs = [('Request', INPUTS[key], value) for key, value in supplied.items() if BASE_REQUEST[key] != value]
        inputs.extend(change)
        def point(sheet, cell, value):
            row = {'sheet': sheet, 'cell': cell, 'value': value}
            if isinstance(value, str):
                row['value_type'] = 'text'
            return row
        expected = [{**point('Request', 'B10', results['B10']), 'milestone': 1}]
        if stage >= 2:
            expected.append({**point('Request', 'B11', results['B11']), 'milestone': milestone})
        scenarios.append({'name': name, 'inputs': [point(*row) for row in inputs], 'expected': expected})

    add('published_inputs', 2)
    add('off_duty_physician', 2, {'doctor': 2})
    add('working_next_day', 2, {'day': 2})
    add('different_slot', 2, {'slot': 2})
    add('shift_update', 2, change=[('Shifts', 'D2', 0)], shifts=[s for s in SHIFTS if s != (1,1,1)])
    if stage >= 2:
        add('doctor_conflict', 2, {'slot': 3})
        add('room_conflict', 2, {'doctor': 2, 'slot': 3, 'room': 'B'})
        add('open_parallel_room', 2, {'slot': 2, 'room': 'B'})
        add('closed_room', 2, {'day': 2}, change=[('Rooms', 'B2', 0)], rooms={'A': False, 'B': True})
        add('prior_lookup_signal_controls_booking', 2, {'day': 2},
            change=[('Request', 'B10', 0)], shifts=[s for s in SHIFTS if s != (1,2,1)])
        # The public shift result is overridden for this linkage test, so only check its consumer.
        scenarios[-1]['expected'] = [row for row in scenarios[-1]['expected'] if row['cell'] == 'B11']
    if stage == 3:
        add('reschedule_keeps_current_slot', 3, {'mode': 'MOVE', 'appointment': 'AP-01', 'slot': 2, 'room': 'B'})
        add('reschedule_released_original_slot', 3, {'mode': 'MOVE', 'appointment': 'AP-01'})
        add('reschedule_room_conflict', 3, {'mode': 'MOVE', 'appointment': 'AP-01', 'slot': 2})
        add('reschedule_doctor_conflict', 3, {'mode': 'MOVE', 'appointment': 'AP-01', 'slot': 3})
        add('reschedule_unknown_id', 3, {'mode': 'MOVE', 'appointment': 'UNKNOWN'})
        add('reschedule_different_doctor', 3, {'mode': 'MOVE', 'appointment': 'AP-01', 'doctor': 2, 'slot': 3})
    return scenarios


def prepare_stage(submission, destination, package):
    submission, destination, package = map(Path, (submission, destination, package))
    if any(not (submission / f).is_file() or (submission / f).is_symlink() for f in FILES):
        raise ValueError('The earlier submission must contain the three regular Office files')
    destination.mkdir(parents=True)
    for file in FILES:
        copyfile(submission / file, destination / file)
    copyfile(package / 'public/instruction.md', destination / 'instruction.md')
    return {'carried_files': list(FILES), 'changed_native_objects': [], 'unchanged_file_bytes': list(FILES)}


def build_phase(package, stage):
    public, private = package / 'public', package / 'private'
    reference = private / 'reference'
    for directory, level in ((public, stage - 1), (reference, stage)):
        directory.mkdir(parents=True)
        workbook(directory / FILES[0], level)
        policy_documents(directory)
    (public / 'instruction.md').write_text(instruction(stage), encoding='utf-8')
    initial, final = (snapshot(path, FILES) for path in (public, reference))
    if initial.keys() != final.keys():
        raise ValueError('A reference changed the prepared object inventory')
    scenarios = scenarios_for(stage)
    origins = {f"functional_excel:{scenario['name']}:Request!{point['cell']}": point['milestone']
               for scenario in scenarios for point in scenario['expected']}
    area = FILES[0] + ':sheet:Request:'
    functions = {'file': FILES[0], 'scenarios': scenarios, 'allow_text_inputs': True, 'flexible_formula_cells': True,
        'objects': {f'{area}logical_row:{row}:column:2': f'Request!B{address}'
                    for row, address in ([(7, 10), (8, 11)] if stage >= 2 else [(7, 10)])}}
    oracle = {'files': FILES, 'editable_files': FILES[:1], 'bindings': [], 'evidence_checks': [],
        'checks': [{'object': key, 'expected': value, 'group': 'required' if initial[key] != value else 'protected'}
                   for key, value in sorted(final.items()) if key != area + 'structure'],
        'functional_excel': functions, 'allow_helpers_in': [area],
        'excel_publication': {'file': FILES[0], 'cells': [],
            'reference_workbook_part_hash': final[FILES[0] + ':protected_part:xl/workbook.xml'],
            'default_calc_workbook_signature': workbook_signature(reference / FILES[0])}}
    save_json(private / 'oracle.json', oracle)
    meta = {'stage': stage, 'new_capabilities': [CAPABILITIES[stage-1]], 'retained_capabilities': CAPABILITIES[:stage-1],
            'formula_stage': {}, 'formula_check_stage': origins}
    save_json(private / 'milestone.json', meta)
    scores = grade(reference, read_json(private / 'oracle.json'))
    if not scores['pass']:
        raise ValueError(f'Invalid {package.name} reference: {[row for row in scores["checks"] if not row["pass"]]}')
    save_json(private / 'native_reference_evaluation.json', scores)
    save_json(private / 'native_manifest.json', {
        'public_sha256': {file: digest(public / file) for file in FILES},
        'reference_sha256': {file: digest(reference / file) for file in FILES}})
    return scores['groups']


def control_checks(final):
    private = final / 'private'
    oracle = read_json(private / 'oracle.json')
    controls = {}
    with TemporaryDirectory(prefix='clinic_control_') as temporary:
        for label in ('new_booking_ignores_room_conflict', 'rewritten_archive'):
            directory = Path(temporary) / label
            copytree(private / 'reference', directory)
            file = directory / FILES[0]
            book = load_workbook(file)
            if label == 'rewritten_archive':
                book['History']['D2'], book['History']['E2'] = MOVED[3:]
            else:
                current = book['Request']['B11'].value
                old_rule = 'COUNTIFS(Active!E2:E4,B5,Active!C2:C4,B3,Active!D2:D4,B4)=0'
                if current.count(old_rule) != 1:
                    raise ValueError('Expected to mutate only the ordinary booking branch')
                book['Request']['B11'] = current.replace(old_rule, '1=1')
            book.save(file)
            book.close()
            result = grade(directory, oracle)
            failed = [row['object'] for row in result['checks'] if not row['pass']]
            if result['pass']:
                raise ValueError(f'Oracle accepted {label}')
            if label == 'new_booking_ignores_room_conflict':
                if failed != ['functional_excel:room_conflict:Request!B11']:
                    raise ValueError(f'Unexpected scope of regression: {failed}')
            elif not all(':sheet:History:' in name for name in failed):
                raise ValueError(f'Unexpected archive changes: {failed}')
            controls[label] = {'passed': result['pass'], 'failed': failed, 'groups': result['groups']}
    return controls


def build(output):
    output = Path(output)
    if any((output / 'agent_runs').glob('*/plan.json')):
        raise FileExistsError('Choose a fresh fixture directory')
    trace = provenance()
    stages = [output / f'stage_{i:02d}' for i in (1, 2, 3)]
    scores = {p.name: build_phase(p, i) for i, p in enumerate(stages, 1)}
    for prior, after in zip(stages, stages[1:]):
        if snapshot(prior / 'private/reference', FILES) != snapshot(after / 'public', FILES):
            raise ValueError('Prior gold and the next starting state disagree')
    checks = control_checks(stages[-1])
    if decision({**BASE_REQUEST, 'mode': 'MOVE', 'appointment': 'AP-01', 'slot': 2, 'room': 'B'},
                SHIFTS, active_rows(2), {'A': True, 'B': True}, allow_move=True)['B11'] != 1:
        raise ValueError('The specified move is not feasible from the original booking')
    save_json(output / 'provenance.json', trace)
    save_json(output / 'summary.json', {'passed': True, 'stages': scores, 'controls': checks,
        'archive': BOOKED, 'active': MOVED, 'same_entry': 'Request!B11'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    build(args.output)
    print(f'Built and validated clinic milestones in {args.output}')


if __name__ == '__main__':
    main()
