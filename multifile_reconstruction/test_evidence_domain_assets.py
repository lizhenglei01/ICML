"""Cross-domain validation of the native evidence compiler."""
import json
from shutil import copytree

from openpyxl import load_workbook
import pytest

from .evidence_domain_assets import FILES, build, native_readback
from .evidence_domain_cases import domain_cases
from .evidence_transform import DifferenceEvidence, MinimumCondition


@pytest.fixture(scope='module')
def transfer_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp('native_evidence_transfer')
    return root, build(root)


def test_transfer_uses_native_sources_for_all_database_states(transfer_bundle):
    root, report = transfer_bundle
    assert report['passed']
    for domain, case in domain_cases().items():
        assert len(report['states'][domain]) == 3
        for idx, state in enumerate(case['states']):
            package = root / domain / f'state_{idx}'
            truth = json.loads((package / 'private/truth.json').read_text())
            verification = report['states'][domain][idx]
            assert truth['net'] == state['net']
            assert truth['flags'] == {key: number >= 4 for key, number in state['net'].items()}
            assert verification['declared_visible_metrics'] == ['excluded', 'gross']
            assert set(verification['full_task_needs_files']) == set(FILES)
            assert set(verification['missing_file_counterexamples']) == set(FILES)
            if idx:
                assert len(truth['changed_groups']) == 2
                assert truth['preserved_groups']


def test_medical_and_sports_event_results():
    cases = domain_cases()
    medical = cases['medical']['states']
    assert [tuple(state['net'].values()) for state in medical] == [(5, 3, 1), (4, 4, 1), (3, 4, 2)]
    venues = ['Nou Mestalla', 'Gazprom Arena', 'Baku Olympic Stadium']
    assert [tuple(state['net'][venue] for venue in venues) for state in cases['sports']['states']] == [
        (3, 5, 1), (8, 0, 1), (5, 0, 4),
    ]


def test_readback_rejects_missing_exclusion_evidence(transfer_bundle, tmp_path):
    root, _ = transfer_bundle
    public = tmp_path / 'public'
    copytree(root / 'medical/state_0/public', public)
    case = domain_cases()['medical']
    rule = DifferenceEvidence('gross', 'excluded', 'net', case['labels'])
    condition = MinimumCondition('达到复核门槛 / meets the review threshold', 4)
    scope = rule.describe('按同批记录、同一组别核对', 'For the same batch and group')
    policy = f'{scope} {condition.describe(case["labels"]["net"])}'
    workbook = load_workbook(public / FILES[0])
    for row in workbook.active.iter_rows(min_col=1, max_col=2):
        if row[0].value == 'General Medicine':
            row[1].value = None
            break
    workbook.save(public / FILES[0])
    workbook.close()
    with pytest.raises(ValueError, match='Invalid exclusion row'):
        native_readback(public, case['states'][0]['net'], rule, policy)

