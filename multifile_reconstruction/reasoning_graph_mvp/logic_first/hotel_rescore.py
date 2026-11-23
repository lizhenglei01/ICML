"""Audit the frozen native trials with the corrected unordered-list contract."""
import argparse
from pathlib import Path

from ...build import digest
from .hotel_agent import OFFICE_FILES, PUBLIC_NAMES, file_hashes
from .hotel_evidence import DEFAULT, write_index
from .hotel_graph import read_json, save_json
from .hotel_office import grade, with_unordered_qualification_list


def rescore(root, run):
    runs = read_json(run / 'results.json')
    plan = read_json(run / 'plan.json')
    rows = {}
    for name, trial in runs.items():
        if trial['status'] != 'COMPLETED':
            raise ValueError('An incomplete trial cannot be rescored: ' + name)
        package = root / 'layouts' / name
        frozen = plan['layouts'][name]
        original_path = package / 'private/oracle.json'
        submission = run / name / 'submission'
        if (digest(original_path) != frozen['oracle_sha256'] or
                file_hashes(package / 'public', PUBLIC_NAMES) != frozen['public_sha256'] or
                file_hashes(submission, OFFICE_FILES) != trial['submission_sha256']):
            raise ValueError('Frozen trial differs from its recorded hashes: ' + name)
        original = read_json(original_path)
        prior = grade(submission, original)
        if prior['pass'] != trial['native_pass']:
            raise ValueError('Legacy score does not replay: ' + name)

        oracle = with_unordered_qualification_list(original)
        if len(oracle['unordered_lists']) != 1:
            raise ValueError('Expected one explicitly unordered result')
        oracle['based_on_sha256'] = digest(original_path)
        revised = grade(submission, oracle)
        reference = grade(package / 'private/reference', oracle)
        if not reference['pass'] or grade(package / 'public', oracle)['pass']:
            raise ValueError('Corrected rubric disagrees with controls: ' + name)
        permitted = set(oracle['unordered_lists']) | set(oracle['unordered_lists'].values())
        before = {check['object']: check['pass'] for check in prior['checks']}
        after = {check['object']: check['pass'] for check in revised['checks']}
        changed = sorted(key for key, passed in before.items() if after[key] != passed)
        if not set(changed) <= permitted:
            raise ValueError('Unrelated checks changed during rescoring')
        save_json(package / 'private/oracle_v2.json', oracle)
        save_json(run / name / 'native_evaluation_v2.json', revised)
        rows[name] = {'frozen_pass': prior['pass'], 'corrected_pass': revised['pass'],
                      'wall_seconds': trial['wall_seconds'], 'modified_office_files': trial['modified_office_files'],
                      'frozen_groups': prior['groups'], 'corrected_groups': revised['groups'], 'changed_checks': changed,
                      'submission_sha256': trial['submission_sha256'], 'source_sha256': frozen['public_sha256']}
    result = {'model': plan['model'], 'effort': plan['effort'], 'layouts': rows,
              'frozen_passes': sum(row['frozen_pass'] for row in rows.values()),
              'corrected_passes': sum(row['corrected_pass'] for row in rows.values()),
              'revision': 'Only the Word qualifying hotel list accepts permutations. Exact membership, labels, and formatting remain checked.',
              'original_inputs_submissions_and_oracle_files_unchanged': True}
    save_json(run / 'results_v2.json', result)
    write_index(root, read_json(root / 'summary.json'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--root', type=Path, default=DEFAULT)
    args = parser.parse_args()
    report = rescore(args.root.resolve(), args.run.resolve())
    print(f'{report["frozen_passes"]} frozen, {report["corrected_passes"]} corrected, {len(report["layouts"])} trials')


if __name__ == '__main__':
    main()
