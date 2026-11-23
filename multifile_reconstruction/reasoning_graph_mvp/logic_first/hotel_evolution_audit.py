"""Audit supported inputs and published values without changing archived grades."""
import argparse
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from .hotel_evolution import DEFAULT, functional_groups
from .hotel_evolution_cases import formula_spec
from .hotel_graph import read_json, save_json
from .hotel_milestone_formulas import _recalculate
from .hotel_office import FILES, _formula_function_checks, digest, equal_value, read_observations
from .hotel_revenue_milestones import cases, source_cells


def corrected_specs(source):
    graph, _, corrected, approved, _ = cases()
    phase_three = source / 'stage_03/private'
    oracle = read_json(phase_three / 'oracle.json')
    stored = read_json(phase_three / 'case.json')
    if corrected['event'] != stored['event']:
        raise ValueError('The report must use the frozen pilot event')
    scenarios, _ = formula_spec(graph, corrected['after'], source_cells(oracle), 3, approved=approved)
    functional = {**oracle['functional_excel'], 'scenarios': scenarios}
    metadata = read_json(phase_three / 'milestone.json')
    origins = {f"functional_excel:{scenario['name']}:{point['sheet']}!{point['cell']}":
               point.get('milestone', scenario.get('milestone', 1))
               for scenario in scenarios for point in scenario['expected']}
    if origins != metadata['formula_check_stage']:
        raise ValueError('The audit must preserve the functional coverage and capability identities')
    return oracle, functional, metadata


def published_results(submission, oracle):
    bindings = [binding for binding in oracle['bindings'] if binding['role'] == 'output']
    expected = {row['evidence_id']: row['after'] for row in oracle['evidence_checks']}
    with TemporaryDirectory(prefix='evolution_publication_') as tmp:
        area = Path(tmp)
        sources, recalculated = area / 'input', area / 'output'
        sources.mkdir()
        recalculated.mkdir()
        for file in FILES.values():
            copyfile(submission / file, (sources if file == FILES['excel'] else recalculated) / file)
        _recalculate(area, [FILES['excel']], 30)
        readings = []
        errors = []
        for file in FILES.values():
            try:
                readings.extend(read_observations(recalculated, bindings, files=[file], excel_data_only=True))
            except (ValueError, KeyError, IndexError, OSError) as exc:
                errors.append({'file': file, 'error': str(exc)})
    checks = [{'id': item['evidence_id'], 'file': item['file'], 'expected': expected[item['evidence_id']],
               'observed': item['value'], 'pass': equal_value(item['value'], expected[item['evidence_id']])}
              for item in readings]
    return {'passed': sum(check['pass'] for check in checks), 'total': len(bindings),
            'checks': checks, 'readback_errors': errors}


def audit_submission(submission, oracle, spec, metadata):
    before = {name: digest(submission / name) for name in FILES.values()}
    checks = _formula_function_checks(submission, spec)
    report = {'functional_groups': functional_groups({'checks': checks}, metadata), 'functional_checks': checks,
              'publication_values': published_results(submission, oracle)}
    if any(digest(submission / name) != sha for name, sha in before.items()):
        raise ValueError('An audit must only read the submitted files')
    report['submission_sha256'] = before
    return report


def run(source, official, output):
    oracle, spec, metadata = corrected_specs(source)
    archived = read_json(official / 'results.json')
    results = {'reference': audit_submission(source / 'stage_03/private/reference', oracle, spec, metadata)}
    for arm in ('stage_03_teacher_forced', 'stage_03_carry_forward'):
        result = archived.get(arm, {})
        submission = official / arm / 'submission'
        if result.get('status') == 'COMPLETED' and all((submission / name).is_file() for name in FILES.values()):
            results[arm] = {'original_native_pass': result['native_pass'], **audit_submission(submission, oracle, spec, metadata)}
        else:
            results[arm] = {'status': result.get('status', 'unavailable')}
    report = {'method': 'Use caller and upstream analysis inputs instead of replacing the downstream display formulas. Recalculate submitted Excel values in copies.',
              'original_scores_modified': False, 'results': results}
    save_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.run / 'supported_interface_audit.json'
    run(args.source, args.run, output)
    print(output)


if __name__ == '__main__':
    main()
