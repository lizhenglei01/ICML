"""Verify publication scoring on frozen submissions and isolated negative controls."""
import argparse
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from docx import Document
from openpyxl import load_workbook

from .hotel_evolution import APPROVAL, DEFAULT, publication_contract
from .hotel_evolution_audit import corrected_specs
from .hotel_graph import read_json, save_json
from .hotel_office import FILES, TEXT_SEPARATOR, digest, grade, read_observations, replace_span
from .hotel_publication import publication_readback


def copy_submission(source, destination):
    destination.mkdir()
    for file in FILES.values():
        copyfile(source / file, destination / file)


def edit_cell(directory, location, value):
    path = directory / FILES['excel']
    workbook = load_workbook(path)
    try:
        workbook[location['sheet']][location['cell']] = value
        workbook.save(path)
    finally:
        workbook.close()


def history_probe(carry, scratch, oracle, case, history):
    source = next(row['location'] for row in oracle['bindings'] if row['role'] == 'evidence'
                  and row['semantic_node_id'] == case['event']['node_id'] and row.get('entity') == case['event']['entity'])
    expected = {row['evidence_id']: row['after'] for row in oracle['evidence_checks']}
    observations = []
    for label, sold in [('current', case['event']['new_value']), ('later_change', 10_000)]:
        directory = scratch / label
        copy_submission(carry, directory)
        edit_cell(directory, source, sold)
        edit_cell(directory, {'sheet': APPROVAL, 'cell': 'B6'}, '续约')
        result, _ = publication_readback(directory / FILES['excel'], [
            {'sheet': APPROVAL, 'cell': 'B8', 'evidence_id': 'decision'}])
        records = read_observations(directory, history)
        historical = {row['semantic_node_id']: row['value'] for row in records}
        observations.append({'sold_room_nights': sold, 'renewal_verdict': result['decision']['value'],
            'history': historical,
            'history_correct': all(row['value'] == expected[row['evidence_id']] for row in records)})
    if [row['renewal_verdict'] for row in observations] != [1, 0] or not all(row['history_correct'] for row in observations):
        raise ValueError('The changed input should affect renewal while preserving both historical records')
    return observations


def run(source, official, output):
    third = source / 'stage_03/private'
    oracle, functional, _ = corrected_specs(source)
    oracle['functional_excel'] = functional
    oracle['excel_publication'] = publication_contract(third / 'reference', oracle, 3)
    states = {'literal_reference': third / 'reference'}
    for arm in ('stage_03_teacher_forced', 'stage_03_carry_forward'):
        states[arm] = official / arm / 'submission'
    frozen = [official / 'results.json', third / 'oracle.json',
              *(directory / file for directory in states.values() for file in FILES.values())]
    before = {str(path): digest(path) for path in frozen}
    results = {name: grade(directory, oracle) for name, directory in states.items()}
    if not all(report['pass'] for report in results.values()):
        errors = {name: [row for row in report['checks'] if not row['pass']] for name, report in results.items() if not report['pass']}
        raise ValueError(f'Expected correct reference and archived submissions to pass: {errors}')

    carry = states['stage_03_carry_forward']
    history = [row for row in oracle['bindings'] if row['semantic_node_id'] in {'approved_roster', 'approved_count'}]
    with TemporaryDirectory(prefix='hotel_publication_controls_') as temp:
        scratch = Path(temp)
        wrong_count = scratch / 'wrong_count'
        copy_submission(carry, wrong_count)
        count = next(row for row in oracle['bindings'] if row['semantic_node_id'] == 'renewal_count' and row['file'] == FILES['excel'])
        edit_cell(wrong_count, count['location'], '=3')
        results['wrong_renewal_count'] = grade(wrong_count, oracle)

        changed_history = scratch / 'changed_history'
        copy_submission(carry, changed_history)
        roster = next(row for row in history if row['semantic_node_id'] == 'approved_roster')
        path = changed_history / roster['file']
        document = Document(path)
        paragraph = document.paragraphs[roster['location']['paragraph']]
        replace_span(paragraph, len(roster['label'] + TEXT_SEPARATOR), len(paragraph.text), 'Harborstone Grand Hotel')
        document.save(path)
        results['changed_historical_roster'] = grade(changed_history, oracle)

        for name, binding in [('wrong_renewal_count', count), ('changed_historical_roster', roster)]:
            failed = [row for row in results[name]['checks'] if not row['pass']]
            if results[name]['pass'] or binding['evidence_id'] not in {row['object'] for row in failed}:
                raise ValueError(f'Expected the specific invalid field to fail in {name}')
        probe = history_probe(carry, scratch, oracle, read_json(third / 'case.json'), history)

    if any(digest(Path(path)) != sha for path, sha in before.items()):
        raise ValueError('Frozen submissions or original scores changed')
    save_json(output, {'baseline_sha256': before, 'history_after_input_change': probe, 'results': results})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.run / 'publication_rescore.json'
    results = run(args.source, args.run, output)
    for name, result in results.items():
        print(name, result['pass'], result['groups'])
    print(output)


if __name__ == '__main__':
    main()
