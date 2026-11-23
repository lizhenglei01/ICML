"""Check the paired public layouts against independent ledger arithmetic."""

from fractions import Fraction
from io import BytesIO
from shutil import copytree
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from .hotel_graph import TARGETS, read_json
from .hotel_office import FILES, grade, read_observations
from .hotel_revenue_evidence import build
from .hotel_revenue_leaks import audit_concealed_values


WILLOWBANK = 'Willowbank Hotel'
HARBORSTONE = 'Harborstone Grand Hotel'
QUALIFIED_AFTER = {WILLOWBANK, HARBORSTONE, 'Silverbrook Garden Hotel'}
REVENUE_SOURCES = {'booked_room_receipts', 'room_refunds', 'room_revenue'}


@pytest.fixture(scope='module')
def paired_layouts(tmp_path_factory):
    root = tmp_path_factory.mktemp('hotel_revenue_evidence')
    build(root)
    return root


def _read_layout(root, layout):
    package = root / layout
    private = package / 'private'
    oracle = read_json(private / 'oracle.json')
    public = package / 'public'
    return (package, oracle, read_observations(public, oracle['bindings']),
            read_observations(private / 'reference', oracle['bindings']))


def _hotel_sources(rows):
    result = {}
    for row in rows:
        node = row['semantic_node_id']
        if node not in REVENUE_SOURCES or row['role'] != 'evidence':
            continue
        key = node, row['entity'], row['file'], row['location']['kind']
        assert key not in result, f'Duplicate source claim at {key}'
        result[key] = Fraction(str(row['value']))
    return result


def _qualification_from_public(rows, revenues, *, previous):
    def vector(node):
        return {row['entity']: int(row['value']) for row in rows
                if row['role'] == 'evidence' and row['semantic_node_id'] == node}

    policy = {row['semantic_node_id']: row['value'] for row in rows if row['role'] == 'policy'}
    floor = Fraction(str(policy['occupancy_floor']))
    premium = Fraction(str(policy['adr_premium']))
    sold, available = vector('sold_room_nights'), vector('available_room_nights')
    if previous:
        sold[WILLOWBANK] = 6000
    adr = {hotel: revenues[hotel] / nights for hotel, nights in sold.items()}
    peers = {hotel for hotel, nights in sold.items() if Fraction(nights, available[hotel]) < floor}
    mean = sum(adr[hotel] for hotel in peers) / len(peers)
    qualified = {hotel for hotel in sold.keys() - peers if adr[hotel] >= mean + premium}
    return qualified, mean, mean + premium, len(qualified), sum(adr[hotel] for hotel in qualified) / len(qualified)


def test_revenue_encoding_native_readback_and_shared_gold(paired_layouts):
    direct, direct_oracle, direct_before, direct_after = _read_layout(paired_layouts, 'direct')
    indirect, indirect_oracle, indirect_before, indirect_after = _read_layout(paired_layouts, 'indirect')
    direct_sources = _hotel_sources(direct_before)
    indirect_sources = _hotel_sources(indirect_before)
    assert direct_sources == _hotel_sources(direct_after)
    assert indirect_sources == _hotel_sources(indirect_after)
    assert {key[0] for key in direct_sources} == {'room_revenue', 'room_refunds'}
    assert {key[0] for key in indirect_sources} == {'booked_room_receipts', 'room_refunds'}

    for kind in ('table', 'chart_cache', 'chart_workbook'):
        assert direct_sources['room_revenue', WILLOWBANK, FILES['ppt'], kind] == 1_080_000
        assert indirect_sources['booked_room_receipts', WILLOWBANK, FILES['ppt'], kind] == 1_125_000
    assert direct_sources['room_refunds', WILLOWBANK, FILES['excel'], 'cell'] == 45_000

    hotels = {entity for node, entity, _, _ in direct_sources if node == 'room_revenue'}
    assert len(hotels) == 6
    for entity in hotels:
        refunds = direct_sources['room_refunds', entity, FILES['excel'], 'cell']
        assert refunds == indirect_sources['room_refunds', entity, FILES['excel'], 'cell']
        for kind in ('table', 'chart_cache', 'chart_workbook'):
            booked = indirect_sources['booked_room_receipts', entity, FILES['ppt'], kind]
            assert booked - refunds == direct_sources['room_revenue', entity, FILES['ppt'], kind]

    assert (direct / 'public/instruction.md').read_bytes() == (indirect / 'public/instruction.md').read_bytes()
    slots = ('evidence', 'policy', 'context')
    for file in (FILES['excel'], FILES['word']):
        direct_shared = {row['evidence_id']: row['value'] for row in direct_before
                         if row['file'] == file and row['role'] in slots}
        indirect_shared = {row['evidence_id']: row['value'] for row in indirect_before
                           if row['file'] == file and row['role'] in slots}
        assert direct_shared == indirect_shared

    word_policy = ' '.join(str(row['value']) for row in indirect_before
                           if row['file'] == FILES['word'] and row['role'] in ('policy', 'context'))
    assert 'refund' in word_policy.casefold()

    direct_net = {hotel: direct_sources['room_revenue', hotel, FILES['ppt'], 'chart_workbook'] for hotel in hotels}
    indirect_net = {hotel: indirect_sources['booked_room_receipts', hotel, FILES['ppt'], 'chart_workbook']
                    - indirect_sources['room_refunds', hotel, FILES['excel'], 'cell'] for hotel in hotels}
    for rows, net in ((direct_before, direct_net), (indirect_before, indirect_net)):
        assert _qualification_from_public(rows, net, previous=True) == ({HARBORSTONE}, 140, 145, 1, 160)
        assert _qualification_from_public(rows, net, previous=False) == (QUALIFIED_AFTER, 120, 125, 3, 140)

    expected_review = {'qualified_count': (1, 3), 'peer_adr_mean': (140, 120),
                       'review_adr_floor': (145, 125), 'qualified_adr_mean': (160, 140)}
    for rows, phase in ((direct_before, 0), (direct_after, 1), (indirect_before, 0), (indirect_after, 1)):
        issued_word = {row['semantic_node_id']: row['value'] for row in rows
                       if row['file'] == FILES['word'] and row['role'] == 'output'}
        for name, expected in expected_review.items():
            assert Fraction(str(issued_word[name])) == expected[phase]
        assert set(issued_word['qualified'].split(', ')) == ({HARBORSTONE} if phase == 0 else QUALIFIED_AFTER)

    for package, oracle in ((direct, direct_oracle), (indirect, indirect_oracle)):
        checks = {check['evidence_id']: check for check in oracle['evidence_checks']}
        source_ids = {row['evidence_id'] for row in oracle['bindings'] if row['role'] in slots}
        assert source_ids and all(checks[key]['group'] == 'protected' for key in source_ids)
        assert grade(package / 'private/reference', oracle)['pass'] is True
        assert grade(package / 'public', oracle)['pass'] is False

    indirect_report = read_json(indirect / 'private/revenue_evidence.json')
    withheld = indirect_report['without_refunds']
    assert set(withheld['files']) == {'excel', 'ppt', 'word'} and not withheld['sufficient']
    witness = withheld['counterworld']
    assert set(witness['changed_answers']) == set(TARGETS)
    assert witness['facts']['same_published_history'] and witness['facts']['same_correction']
    booked = {hotel: indirect_sources['booked_room_receipts', hotel, FILES['ppt'], 'chart_workbook']
              for hotel in hotels}
    assert booked == witness['facts']['unchanged_receipts']
    alternate = {hotel: amount - witness['facts']['alternative_refunds'][hotel] for hotel, amount in booked.items()}
    assert _qualification_from_public(indirect_before, alternate, previous=True) == ({HARBORSTONE}, 140, 145, 1, 160)
    assert _qualification_from_public(indirect_before, alternate, previous=False) == (
        {HARBORSTONE, WILLOWBANK}, 121, 126, 2, Fraction(587, 4))


def test_revenue_proof_depth_file_necessity_and_exact_business_values(paired_layouts):
    same_business = ('qualified', 'qualified_count', 'qualified_adr_mean', 'peer_cohort',
                     'peer_adr_mean', 'review_adr_floor', 'room_revenue', 'sold_room_nights')
    cases = {name: read_json(paired_layouts / name / 'private/case.json') for name in ('direct', 'indirect')}
    assert cases['direct']['event'] == cases['indirect']['event']
    for when in ('before', 'after'):
        assert all(cases['direct'][when][node] == cases['indirect'][when][node] for node in same_business)
    before, after = (cases['direct'][when] for when in ('before', 'after'))
    assert (before['sold_room_nights'][WILLOWBANK], after['sold_room_nights'][WILLOWBANK]) == (6000, 8000)
    assert (set(before['qualified']), set(after['qualified'])) == ({HARBORSTONE}, QUALIFIED_AFTER)
    assert (before['qualified_count'], after['qualified_count']) == (1, 3)
    for key, expected in {'peer_adr_mean': (140, 120), 'review_adr_floor': (145, 125),
                          'qualified_adr_mean': (160, 140)}.items():
        assert tuple(Fraction(str(state[key])) for state in (before, after)) == expected

    for name in ('direct', 'indirect'):
        private = paired_layouts / name / 'private'
        report = read_json(private / 'visibility.json')
        assert report['passed'] and report['minimum_files'] == 3
        assert report['minimal_file_sets'] == [['excel', 'ppt', 'word']]
        assert len(report['subsets']) == 8
        assert not report['proof']['direct_answer_leaks'] and not report['proof']['conflicts']
        for subset in report['subsets']:
            if not subset['sufficient']:
                assert set(subset['counterworld']['changed_answers']) >= set(TARGETS)
        exposure = read_json(private / 'revenue_evidence.json')
        expected_depth = (0 if name == 'direct' else 1)
        assert exposure['room_revenue_depth'] == expected_depth
        assert exposure['reconstructed_room_revenue'] == after['room_revenue']
        leaves = {'room_revenue'} if name == 'direct' else {'booked_room_receipts', 'room_refunds'}
        assert set(exposure['proof']['observations']) == leaves
        depths = {node: report['proof']['proofs'][node]['depth'] for node in TARGETS}
        assert depths == {'qualified': 5, 'qualified_count': 6, 'qualified_adr_mean': 6}


def _inject_in_hidden_sheet(workbook):
    sheet = workbook.create_sheet('Planner scratch')
    sheet.sheet_state = 'hidden'
    sheet['A1'] = 1_080_000


@pytest.mark.parametrize('container', ('public_workbook', 'ppt_chart_workbook'))
def test_indirect_layout_rejects_hidden_net_revenue(paired_layouts, tmp_path, container):
    package = paired_layouts / 'indirect'
    submission = tmp_path / 'leaky_input'
    copytree(package / 'public', submission)
    case = read_json(package / 'private/case.json')
    current_revenue = case['after']['room_revenue']
    assert audit_concealed_values(submission, current_revenue)['passed'] is True
    if container == 'public_workbook':
        workbook_path = submission / FILES['excel']
        workbook = load_workbook(workbook_path)
        _inject_in_hidden_sheet(workbook)
        workbook.save(workbook_path)
        workbook.close()
    else:
        oracle = read_json(package / 'private/oracle.json')
        part, = {row['location']['part'].lstrip('/') for row in oracle['bindings']
                 if row['semantic_node_id'] == 'booked_room_receipts' and row['location']['kind'] == 'chart_workbook'}
        deck = submission / FILES['ppt']
        with ZipFile(BytesIO(deck.read_bytes())) as original, ZipFile(deck, 'w') as edited:
            for name in original.namelist():
                content = original.read(name)
                if name == part:
                    workbook = load_workbook(BytesIO(content))
                    _inject_in_hidden_sheet(workbook)
                    encoded = BytesIO()
                    workbook.save(encoded)
                    workbook.close()
                    content = encoded.getvalue()
                edited.writestr(name, content)
    result = audit_concealed_values(submission, current_revenue)
    assert result['passed'] is False
    expected_file = FILES['excel'] if container == 'public_workbook' else FILES['ppt']
    assert any(finding['file'] == expected_file and finding['value'] == '1080000'
               and WILLOWBANK in finding['entities'] for finding in result['findings'])
