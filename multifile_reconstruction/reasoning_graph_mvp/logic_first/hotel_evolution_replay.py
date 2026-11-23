"""Replay the frozen prior formula checks on the completed evolution workbook."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from lxml import etree

from . import hotel_evolution_cases
from .hotel_evolution import DEFAULT, functional_groups, prepare_stage
from .hotel_graph import read_json, save_json
from .hotel_office import FILES, _formula_function_checks, digest, read_observations, structural_key
from .hotel_revenue_milestones import cases, source_cells
from .hotel_revenue_sequence import SPREADSHEET_NS, _sheet_part, _write_source_value
from .hotel_visibility import to_json


EXCEL = FILES['excel']
FINAL_ARMS = ('stage_03_teacher_forced', 'stage_03_carry_forward')


def _assert_hash(file, expected):
    if not file.is_file() or file.is_symlink() or digest(file) != expected:
        raise ValueError(f'Frozen file differs from its recorded SHA-256: {file}')


def _binding(oracle, event):
    binding, = [row for row in oracle['bindings']
                if row['role'] == 'evidence' and row['file'] == EXCEL
                and row['semantic_node_id'] == event['node_id'] and row.get('entity') == event['entity']]
    return binding


def _read_value(directory, binding):
    result, = read_observations(directory, [binding])
    return result['value']


def _verify_one_cell(original, restored, location, old_value):
    with ZipFile(original) as before, ZipFile(restored) as after:
        sheet_part = _sheet_part(before, location['sheet'])
        if before.namelist() != after.namelist() or _sheet_part(after, location['sheet']) != sheet_part:
            raise ValueError('The temporary replay changed the workbook inventory')
        changed = [part for part in before.namelist() if before.read(part) != after.read(part)]
        if changed != [sheet_part]:
            raise ValueError('The temporary replay changed other native workbook parts')
        expected = etree.fromstring(before.read(sheet_part))
        value, = expected.findall(f'.//{SPREADSHEET_NS}c[@r="{location["cell"]}"]/{SPREADSHEET_NS}v')
        value.text = str(old_value)
        observed = etree.fromstring(after.read(sheet_part))
        if etree.tostring(expected, method='c14n') != etree.tostring(observed, method='c14n'):
            raise ValueError('The temporary replay changed worksheet content outside the authorized value')
    return changed


def _formula_report(directory, spec, milestone):
    checks = _formula_function_checks(directory, spec)
    return {'functional_groups': functional_groups({'checks': checks}, milestone),
            'prior_functions_pass': bool(checks) and all(row['pass'] for row in checks),
            'formula_checks': checks}


def _formatted_groups(report):
    return {name: f"{group['passed']}/{group['total']}"
            for name, group in report.get('functional_groups', {}).items()}


def _corrected_cases(second, old_oracle, old_milestone):
    graph, first, _, _, _ = cases()
    state = first['after']
    if to_json(state) != read_json(second / 'private/case.json')['after']:
        raise ValueError('Corrected Phase 2 cases do not match the frozen business values')
    scenarios, _ = hotel_evolution_cases.formula_spec(graph, state, source_cells(old_oracle), 2)
    phases = {
        f"functional_excel:{scenario['name']}:{point['sheet']}!{point['cell']}": point.get('milestone', scenario.get('milestone', 1))
        for scenario in scenarios for point in scenario['expected']
    }
    metadata = {**old_milestone, 'formula_check_stage': phases}
    external_spec = {**old_oracle['functional_excel'], 'scenarios': scenarios}
    serialized = json.dumps(to_json(external_spec), sort_keys=True, ensure_ascii=False, allow_nan=False)
    return external_spec, metadata, hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _replay(original, temporary, binding, event, specs, expected_hash):
    _assert_hash(original, expected_hash)
    before = _read_value(original.parent, binding)
    if before != event['new_value']:
        return {'status': 'unexpected_stage_three_input', 'original_xlsx_sha256': expected_hash,
                'source_before': before, 'expected_before': event['new_value'], 'source_restored': None}
    temporary.mkdir()
    restored = temporary / EXCEL
    _write_source_value(original, restored, binding['location'], event['old_value'])
    after = _read_value(temporary, binding)
    if after != event['old_value']:
        raise ValueError('The frozen prior value did not survive native readback')
    changed = _verify_one_cell(original, restored, binding['location'], after)
    frozen, corrected = (_formula_report(temporary, *spec) for spec in specs)
    _assert_hash(original, expected_hash)
    return {
        'status': 'replayed', 'source_before': before, 'source_restored': after,
        'restored_location': binding['location'], 'changed_zip_members': changed,
        'original_xlsx_sha256': expected_hash, 'replay_xlsx_sha256': digest(restored),
        **frozen, 'corrected_prior_caller': corrected,
    }


def replay(source=DEFAULT, *, run_folder=None):
    source = Path(source).resolve()
    run_folder = Path(run_folder).resolve() if run_folder is not None else None
    second, third = (source / name for name in ('stage_02', 'stage_03'))
    oracles = {phase.name: phase / 'private/oracle.json' for phase in (second, third)}
    oracle_hashes = {key: digest(path) for key, path in oracles.items()}
    old_oracle, new_oracle = (read_json(oracles[phase.name]) for phase in (second, third))
    old_milestone = read_json(second / 'private/milestone.json')
    event = read_json(third / 'private/case.json')['event']
    if old_milestone['stage'] != 2 or read_json(third / 'private/milestone.json')['event'] != event:
        raise ValueError('Stage metadata does not describe the frozen edit')
    previous, latest = (_binding(oracle, event) for oracle in (old_oracle, new_oracle))
    if previous['location'] != latest['location'] or any(
        _read_value(second / relative, previous) != event['old_value'] for relative in ('public', 'private/reference')
    ):
        raise ValueError('Stage two does not expose the same authoritative location and old value')

    records = {}
    results_hash = None
    if run_folder is not None:
        plan = read_json(run_folder / 'plan.json')
        if Path(plan['source']).resolve() != source:
            raise ValueError('Run used a different frozen source')
        for phase in ('stage_02', 'stage_03'):
            label = f'{phase}_teacher_forced'
            _assert_hash(oracles[phase], plan['frozen_inputs'][label]['oracle_sha256'])
        score_path = run_folder / 'results.json'
        if score_path.is_file():
            records = read_json(score_path)
            if (run_folder / 'comparisons.json').is_file():
                results_hash = digest(score_path)

    old_spec = old_oracle['functional_excel']
    corrected, corrected_meta, corrected_hash = _corrected_cases(second, old_oracle, old_milestone)
    specs = ((old_spec, old_milestone), (corrected, corrected_meta))
    reference_hash = read_json(third / 'private/native_manifest.json')['reference_sha256'][EXCEL]
    with TemporaryDirectory(prefix='hotel_prior_input_replay_') as scratch:
        area = Path(scratch)
        forward = prepare_stage(second / 'private/reference', area / 'forward_control', third)
        if (forward['event'] != event or forward['location'] != latest['location']
                or forward['source_value_read_from_submission'] != event['old_value']
                or forward['injected_value'] != event['new_value']
                or forward['changed_native_objects'] != structural_key(second / 'private/reference', previous)):
            raise ValueError('Frozen predecessor handoff disagrees with the replay setup')
        reference = _replay(third / 'private/reference' / EXCEL, area / 'reference',
                            latest, event, specs, reference_hash)
        live = {}
        if run_folder is not None:
            for label in FINAL_ARMS:
                record = records.get(label, {})
                expected_hash = record.get('submission_sha256', {}).get(EXCEL)
                if record.get('status') != 'COMPLETED' or not expected_hash:
                    live[label] = {'status': 'no_completed_workbook', 'official_run_status': record.get('status')}
                    continue
                live[label] = {'official_native_pass': record['native_pass'], **_replay(
                    run_folder / label / 'submission' / EXCEL, area / label, latest,
                    event, specs, expected_hash)}

    for phase, path in oracles.items():
        _assert_hash(path, oracle_hashes[phase])
    if results_hash is not None:
        _assert_hash(score_path, results_hash)
    report = {'scope': 'Frozen and corrected Stage 2 caller cases on Stage 3 output with only the changed ledger input restored.',
              'oracles_sha256': oracle_hashes, 'official_results_sha256': results_hash,
              'corrected_formula_spec_sha256': corrected_hash,
              'corrected_formula_source_sha256': digest(hotel_evolution_cases.__file__),
              'forward_control': forward, 'reference': reference, 'live': live}
    destination = (run_folder or source) / 'prior_input_replay.json'
    if destination.is_symlink():
        raise ValueError('Replay report may not be a symbolic link')
    save_json(destination, report)
    for label, row in {'reference': reference, **live}.items():
        print(label, row['status'], 'frozen', _formatted_groups(row),
              'corrected', _formatted_groups(row.get('corrected_prior_caller', {})), flush=True)
    print('HOTEL_EVOLUTION_PRIOR_INPUT_REPLAY', destination, flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--run', type=Path)
    args = parser.parse_args()
    report = replay(args.source, run_folder=args.run)
    if not all(result.get('prior_functions_pass') for result in (
        report['reference'], report['reference'].get('corrected_prior_caller', {}),
    )):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
