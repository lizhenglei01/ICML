"""Run supplementary formula probes on frozen hotel milestone artifacts."""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path

from openpyxl import load_workbook

from . import hotel_milestone_formulas, hotel_revenue_milestones
from .hotel_graph import read_json, save_json
from .hotel_office import FILES, digest
from .hotel_revenue_milestones import ANALYSIS, cases, formula_spec, source_cells
from .hotel_revenue_milestones_agent import PHASES, START
from .hotel_visibility import to_json


EXCEL = FILES['excel']


def _check_hash(path, expected, role):
    if not path.is_file() or path.is_symlink() or not expected or digest(path) != expected:
        raise ValueError(f'{role} does not match the recorded SHA-256: {path}')


def _scenario_inputs(records):
    def values(entries):
        return {(row['sheet'], row['cell']): Fraction(str(row['value'])) for row in entries}

    return [{'name': row['name'], 'inputs': values(row['inputs']), 'expected': values(row['expected'])}
            for row in records]


def _formula_text(workbook_path, cells):
    workbook = load_workbook(workbook_path, data_only=False, read_only=True)
    try:
        if ANALYSIS not in workbook:
            return dict.fromkeys(cells)
        sheet = workbook[ANALYSIS]
        formulas = {}
        for address in cells:
            cell = sheet[address]
            value = cell.value if cell.data_type == 'f' else None
            formulas[address] = value if isinstance(value, str) else getattr(value, 'text', None)
        return formulas
    finally:
        workbook.close()


def _retention(start, final, origins, stage):
    retained = [address for address, origin in origins.items() if origin < stage]
    if not retained:
        return {'status': 'not_applicable', 'unchanged': 0, 'total': 0, 'checks': []}
    try:
        before, after = (_formula_text(path, retained) for path in (start, final))
    except hotel_milestone_formulas.WORKBOOK_ERRORS as exc:
        return {'status': 'unreadable_workbook', 'unchanged': None, 'total': len(retained),
                'error': f'{type(exc).__name__}: {exc}'}
    checks = []
    for address in retained:
        prior, following = before[address], after[address]
        if prior is None:
            change = 'missing_at_both' if following is None else 'added_after_start'
        elif following is None:
            change = 'removed'
        else:
            change = 'unchanged' if prior == following else 'replaced'
        checks.append({'cell': f'{ANALYSIS}!{address}', 'before': prior, 'after': following,
                       'change': change, 'unchanged': change == 'unchanged'})
    count = sum(check['unchanged'] for check in checks)
    return {'status': 'checked', 'unchanged': count, 'total': len(checks),
            'all_unchanged': count == len(checks), 'checks': checks}


def _audit(start, final, spec, stage, origins):
    checked = hotel_milestone_formulas.verify_formula_function(final, spec)
    failure_locations = [f"{case['name']}:{check['sheet']}!{check['cell']}"
                         for case in checked['scenarios'] for check in case['checks'] if not check['pass']]
    mutation_failures = [f"{case['name']}:{change['sheet']}!{change['cell']}"
                         for case in checked['scenarios'] for change in case['mutations'] if not change['pass']]
    return {'expanded_formula_pass': checked['pass'], 'formula_failures': failure_locations,
            'mutation_failures': mutation_failures, 'formula_verification': checked,
            'retained_formula_text': _retention(start, final, origins, stage)}


def _audit_submission(run_folder, label, inputs, record, phase_spec):
    status = {'run_status': record['status'], 'official_native_pass': record['native_pass']}
    if record['status'] != 'COMPLETED' or EXCEL not in record.get('submission_sha256', {}):
        return {**status, 'audit_status': 'no_workbook_submission'}
    start = Path(inputs['package']) / 'public' / EXCEL
    final = run_folder / label / 'submission' / EXCEL
    _check_hash(start, inputs['public_sha256'][EXCEL], 'Actual run input')
    _check_hash(final, record['submission_sha256'][EXCEL], 'Actual run submission')
    stage, spec, origins = phase_spec
    result = _audit(start, final, spec, stage, origins)
    _check_hash(final, record['submission_sha256'][EXCEL], 'Audited submission')
    return {**status, 'audit_status': 'checked', **result}


def _audit_clarified(run_folder, canonical, phase_spec):
    run_folder = Path(run_folder).resolve()
    label = 'stage_02_standard_clarified'
    layouts = read_json(run_folder / 'plan.json')['layouts']
    supplied = layouts[label]
    result_path = run_folder / 'results.json'
    result_sha256 = digest(result_path)
    record = read_json(result_path)[label]
    if (supplied['oracle_sha256'] != canonical['oracle_sha256']
            or any(supplied['public_sha256'][name] != canonical['public_sha256'][name] for name in FILES.values())):
        raise ValueError('The clarified run changed canonical native inputs or the Oracle')
    oracle_path = Path(supplied['package']) / 'private' / supplied['oracle_file']
    _check_hash(oracle_path, canonical['oracle_sha256'], 'Clarified run Oracle')
    scored = _audit_submission(run_folder, label, supplied, record, phase_spec)
    _check_hash(result_path, result_sha256, 'Clarified official results')
    return {'run_folder': str(run_folder), 'official_results_sha256': result_sha256, **scored}


def audit(run_folder, *, references_only=False, clarified_run=None):
    run_folder = Path(run_folder).resolve()
    plan_path = run_folder / 'plan.json'
    results_path = run_folder / 'results.json'
    plan = read_json(plan_path)
    source = Path(plan['source'])
    if not references_only and not (run_folder / 'comparisons.json').is_file():
        raise RuntimeError('Official run has not finished its post-run scoring')
    results = {} if references_only else read_json(results_path)
    if not references_only and not set(PHASES) <= results.keys():
        raise RuntimeError('Official run has not recorded terminal results for every phase and arm')
    results_sha256 = None if references_only else digest(results_path)
    frozen = plan['frozen_inputs']
    graph, first, last, _, _ = cases()
    formula_specs = {}
    references = {}
    for label in START:
        phase = PHASES[label]
        stage = int(phase.rsplit('_', 1)[-1])
        package = source / phase
        oracle_file = package / 'private' / frozen[label]['oracle_file']
        _check_hash(oracle_file, frozen[label]['oracle_sha256'], 'Frozen Oracle')
        state = (last if stage == 3 else first)['after']
        frozen_state = read_json(package / 'private/case.json')['after']
        if to_json(state) != frozen_state:
            raise ValueError(f'{phase} formula probes are based on different business data')
        rows, origins = formula_spec(graph, state, source_cells(read_json(oracle_file)), stage)
        spec = _scenario_inputs(rows)
        formula_specs[phase] = stage, spec, origins
        start = package / 'public' / EXCEL
        final = package / 'private/reference' / EXCEL
        _check_hash(start, frozen[label]['public_sha256'][EXCEL], 'Frozen reference input')
        _check_hash(final, frozen[label]['reference_sha256'][EXCEL], 'Frozen reference output')
        references[phase] = _audit(start, final, spec, stage, origins)

    live = {}
    for label, phase in PHASES.items():
        if references_only:
            break
        live[label] = _audit_submission(run_folder, label, frozen.get(label), results[label], formula_specs[phase])
    if clarified_run is not None:
        live['stage_02_standard_clarified'] = _audit_clarified(
            clarified_run, frozen['stage_02_teacher_forced'], formula_specs['stage_02'])

    if not references_only:
        _check_hash(results_path, results_sha256, 'Official results')
    report = {
        'purpose': 'Post-run expanded formula behavior and exact retained formula text. Official scores are separate.',
        'retained_formula_comparison': 'Compare submitted worksheet formula strings without case or whitespace normalization.',
        'formula_source_sha256': digest(hotel_revenue_milestones.__file__),
        'calculator_source_sha256': digest(hotel_milestone_formulas.__file__),
        'official_results_sha256': results_sha256,
        'references': references,
        'all_reference_formulas_pass': all(result['expanded_formula_pass'] for result in references.values()),
        'live': live,
    }
    name = 'reference_formula_audit.json' if references_only else 'formula_audit.json'
    save_json(run_folder / name, report)
    for label, row in {**references, **live}.items():
        retention = row.get('retained_formula_text', {})
        print(label, 'expanded_formula_pass=', row.get('expanded_formula_pass'),
              'retained_formula_text=', f"{retention.get('unchanged', '-')}/{retention.get('total', '-')}",
              'formula_failures=', row.get('formula_failures', []))
    print('HOTEL_MILESTONE_FORMULA_AUDIT', run_folder / name)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Official milestone agent run directory')
    parser.add_argument('--references-only', action='store_true', help='Audit only the frozen references')
    parser.add_argument('--clarified-run', type=Path, help='Also audit the supplementary clarified stage-two run')
    args = parser.parse_args()
    report = audit(args.run, references_only=args.references_only, clarified_run=args.clarified_run)
    if not report['all_reference_formulas_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
