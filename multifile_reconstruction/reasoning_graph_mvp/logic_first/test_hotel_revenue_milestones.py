"""Native checks for the stage-one approval consumed by stage-two renewal."""

from fractions import Fraction

import pytest

from .hotel_graph import read_json
from .hotel_office import grade, patch_reference, read_observations
from .hotel_revenue_milestones import build


HARBORSTONE = 'Harborstone Grand Hotel'
WILLOWBANK = 'Willowbank Hotel'
SILVERBROOK = 'Silverbrook Garden Hotel'


@pytest.fixture(scope='module')
def milestones(tmp_path_factory):
    root = tmp_path_factory.mktemp('hotel_revenue_milestones')
    build(root)
    return root


def _stage(root, name):
    package = root / name
    oracle = read_json(package / 'private/oracle.json')
    public = read_observations(package / 'public', oracle['bindings'])
    final = read_observations(package / 'private/reference', oracle['bindings'])
    return package, oracle, public, final


def _reading(rows, node):
    result = [row['value'] for row in rows if row['semantic_node_id'] == node and 'entity' not in row]
    assert result and all(value == result[0] for value in result)
    return result[0]


def _roster(rows, node):
    return set(_reading(rows, node).split(', '))


def _current_qualifiers(rows):
    def vector(node):
        return {row['entity']: Fraction(str(row['value'])) for row in rows
                if row['semantic_node_id'] == node and row['role'] == 'evidence'}

    sold, available = vector('sold_room_nights'), vector('available_room_nights')
    booked, refunds = vector('booked_room_receipts'), vector('room_refunds')
    occupancy = {hotel: sold[hotel] / available[hotel] for hotel in sold}
    adr = {hotel: (booked[hotel] - refunds[hotel]) / sold[hotel] for hotel in sold}
    floor = Fraction(str(_reading(rows, 'occupancy_floor')))
    peers = {hotel for hotel, rate in occupancy.items() if rate < floor}
    hurdle = sum(adr[hotel] for hotel in peers) / len(peers) + Fraction(str(_reading(rows, 'adr_premium')))
    return sold, adr, hurdle, {hotel for hotel in sold if occupancy[hotel] >= floor and adr[hotel] >= hurdle}


def test_frozen_approval_and_current_results_are_distinct_in_native_files(milestones):
    stages = [_stage(milestones, f'stage_{stage:02d}') for stage in (1, 2, 3)]
    for idx, (package, oracle, _, _) in enumerate(stages, 1):
        final = grade(package / 'private/reference', oracle)
        assert final['pass'] is True
        assert grade(package / 'public', oracle)['pass'] is False
        formulas = {check['object'].rsplit(':', 1)[-1] for check in final['checks']
                    if check['object'].startswith('functional_excel:')}
        expected_columns = 'CD' if idx == 1 else 'CDE'
        assert formulas == {f'酒店指标!{column}{row}' for column in expected_columns for row in range(2, 8)}

    first_final, second_final, third_final = (data[3] for data in stages)
    third_public, third_oracle = stages[2][2], stages[2][1]
    assert _reading(first_final, 'approved_count') == 0 and _reading(first_final, 'renewal_count') == 0
    assert _reading(second_final, 'renewal_count') == 0
    approved = _roster(second_final, 'approved_roster')
    assert approved == {HARBORSTONE, WILLOWBANK}
    assert _roster(third_public, 'approved_roster') == approved
    assert _roster(third_final, 'approved_roster') == approved
    assert [_reading(rows, 'approved_count') for rows in (second_final, third_public, third_final)] == [2, 2, 2]

    first_sold, first_adr, _, first_current = _current_qualifiers(first_final)
    sold, adr, hurdle, current = _current_qualifiers(third_public)
    assert (first_sold[WILLOWBANK], sold[WILLOWBANK]) == (8000, 9000)
    assert (first_adr[WILLOWBANK], adr[WILLOWBANK], hurdle) == (135, 120, 125)
    assert first_current == {HARBORSTONE, WILLOWBANK, SILVERBROOK}
    assert current == {HARBORSTONE, SILVERBROOK} and _roster(third_final, 'qualified') == current
    assert _roster(third_final, 'renewal_roster') == approved & current == {HARBORSTONE}
    assert _reading(third_final, 'renewal_count') == 1

    for node in ('approved_roster', 'approved_count'):
        frozen_ids = {row['evidence_id'] for row in third_final if row['semantic_node_id'] == node}
        checks = [entry for entry in third_oracle['evidence_checks'] if entry['evidence_id'] in frozen_ids]
        assert checks and all(entry['group'] == 'protected' and entry['before'] == entry['after'] for entry in checks)


def test_current_eligibility_alone_cannot_substitute_for_renewal(milestones, tmp_path):
    third, oracle, _, final = _stage(milestones, 'stage_03')
    bindings = [row for row in oracle['bindings'] if row['semantic_node_id'] in {'renewal_roster', 'renewal_count'}]
    wrong = {'renewal_roster': ', '.join(sorted((HARBORSTONE, SILVERBROOK))), 'renewal_count': 2}
    observed = {row['node_id']: row['value'] for row in final}
    proposed = {row['node_id']: wrong[row['semantic_node_id']] for row in bindings}
    submission = tmp_path / 'renew_current_eligible_only'
    patch_reference(third / 'private/reference', submission, bindings, observed, proposed)
    report = grade(submission, oracle)
    assert report['pass'] is False
    failing_semantic = {row['object'] for row in report['checks'] if not row['pass'] and row['group'] == 'semantic'}
    assert failing_semantic == {row['evidence_id'] for row in bindings}


def test_frozen_signing_roster_changes_renewal_and_is_protected(milestones, tmp_path):
    third, oracle, public, final = _stage(milestones, 'stage_03')
    witness = read_json(milestones / 'summary.json')['dependency_witness']
    assert witness['identical_except_approved_roster'] and witness['renewals_underdetermined_without_approved_roster']
    assert len(witness['changed_native_objects']) == 1
    _, _, _, current = _current_qualifiers(public)
    assert set(witness['canonical_renewal_roster']) == _roster(public, 'approved_roster') & current == {HARBORSTONE}
    alternative = {SILVERBROOK, WILLOWBANK}
    assert set(witness['alternate_renewal_roster']) == alternative & current == {SILVERBROOK}

    bindings = [row for row in oracle['bindings'] if row['semantic_node_id'] == 'approved_roster']
    binding, = bindings
    assert binding['location']['kind'] == 'paragraph'
    observed = {row['node_id']: row['value'] for row in final}
    proposed = {binding['node_id']: ', '.join(sorted(alternative))}
    submission = tmp_path / 'rewrote_previous_approval'
    patch_reference(third / 'private/reference', submission, bindings, observed, proposed)
    report = grade(submission, oracle)
    assert report['pass'] is False
    assert report['groups']['required']['passed'] == report['groups']['required']['total']
    failed = {row['object']: row['group'] for row in report['checks'] if not row['pass']}
    assert failed == {binding['evidence_id']: 'semantic', witness['changed_native_objects'][0]: 'protected'}
