"""Regression coverage for author-defined schemas and mixed operator programs."""
from copy import deepcopy

import pytest

from .program_graph import evaluate
from .program_reference import reference
from .unified_program import compile_objects, finalize, partial, verify_backward


def proposal():
    def source(key, label, pk, cols):
        return {'id': key, 'label': label, 'rows': 3,
                'key': {'id': pk, 'label': '记录号', 'examples': []}, 'fields': cols}

    def amount(key, unit):
        return {'id': key, 'label': key, 'kind': 'number', 'unit': {unit: 1}, 'min': 1, 'max': 100, 'step': 1}

    def ref(key, target):
        return {'id': key, 'label': key, 'kind': 'reference', 'target': target}

    observations = {'title': '服务评价', 'period': '2025-Q4', 'decision_object': 'clinics', 'objects': [
        source('entries', '业务记录', 'entry_code', [ref('owner_code', 'clinics'), amount('gross', 'CNY'), amount('cost', 'CNY')]),
        source('clinics', '服务单位', 'clinic_key', [ref('district', 'areas')]),
        source('areas', '地区', 'area_key', [{'id': 'zone', 'label': '分区', 'kind': 'category', 'values': ['甲', '乙', '丙']}]),
    ]}

    def op(key, name, *inputs, **params):
        return {'id': key, 'label': key, 'op': name, 'inputs': list(inputs), 'params': params}

    forward = {'nodes': [
        op('net_values', 'SUB', 'entries', left='gross', right='cost', **{'as': 'net'}),
        op('linked_clinic', 'LOOKUP', 'net_values', 'clinics', on=['owner_code', 'clinic_key'], field='district'),
        op('linked_area', 'JOIN', 'linked_clinic', 'areas', on=['district', 'area_key'], fields=['zone']),
        op('zones', 'GROUP_BY', 'linked_area', by=['zone']),
        op('zone_net', 'GROUP_SUM', 'zones', value='net', **{'as': 'amount'}),
    ], 'target': 'zone_net', 'definition': '按分区统计记录净额'}
    policy = {'nodes': [
        op('rank_order', 'RANK', 'zone_net', score='amount', order='DESC'),
        op('best_zones', 'TOP_K', 'rank_order', k=1),
        op('zone_ids', 'PROJECT_SET', 'best_zones', field='zone'),
        op('winning_entries', 'FILTER_IN', 'linked_area', 'zone_ids', field='zone'),
        op('chosen_clinics', 'PROJECT_SET', 'winning_entries', field='owner_code'),
        op('chosen_net', 'TOTAL', 'winning_entries', field='net'),
    ], 'roster': 'chosen_clinics', 'reports': ['chosen_net']}
    return observations, forward, policy


def test_authored_three_table_chain_with_different_key_names_and_arithmetic():
    raw, forward, policy = proposal()
    base, schemas = compile_objects(raw)
    assert list(base['objects']) == ['areas', 'clinics', 'entries']
    target, _ = partial(base, schemas, forward)
    assert target.columns['amount'].unit == (('CNY', 1),)
    graph = finalize(base, schemas, forward, policy, 4)
    sources = {
        'areas': ({'area_key': 'x', 'zone': '甲'}, {'area_key': 'y', 'zone': '乙'}),
        'clinics': ({'clinic_key': 'a', 'district': 'x'}, {'clinic_key': 'b', 'district': 'y'}),
        'entries': ({'entry_code': '1', 'owner_code': 'a', 'gross': 5, 'cost': 1},
                    {'entry_code': '2', 'owner_code': 'b', 'gross': 7, 'cost': 2}),
    }
    state = evaluate(graph, sources)
    assert state == reference(graph, sources)
    assert state['chosen_clinics'] == {'b'} and state['chosen_net'] == 5
    backward = deepcopy(forward)
    backward['nodes'][0]['params']['left'] = 'cost'
    backward['nodes'][0]['params']['right'] = 'gross'
    with pytest.raises(ValueError, match='disagrees'):
        verify_backward(base, schemas, forward, backward, 912)


def test_prompted_metadata_cannot_override_foreign_key_types_or_units():
    raw, forward, _ = proposal()
    changed = deepcopy(raw)
    changed['objects'][0]['fields'][-1]['unit'] = {'day': 1}
    with pytest.raises(ValueError, match='UNIT_MISMATCH'):
        partial(*compile_objects(changed), forward)
    changed = deepcopy(raw)
    changed['objects'][0]['fields'][0]['target'] = 'areas'
    with pytest.raises(ValueError, match='declared reference domain'):
        partial(*compile_objects(changed), forward)


def test_same_compiler_accepts_an_arithmetic_only_source_without_relationships():
    raw, _, _ = proposal()
    source = raw['objects'][0]
    source['fields'] = source['fields'][1:]
    raw['objects'] = [source]
    raw['decision_object'] = source['id']
    base, schemas = compile_objects(raw)
    forward = {'target': 'net', 'nodes': [
        {'id': 'net', 'label': '净额', 'op': 'SUB', 'inputs': ['entries'],
         'params': {'left': 'gross', 'right': 'cost', 'as': 'net_amount'}},
    ]}
    policy = {'roster': 'qualifying', 'reports': ['published_count'], 'nodes': [
        {'id': 'large_net', 'label': '满足净额门槛', 'op': 'FILTER_NUM', 'inputs': ['net'],
         'params': {'field': 'net_amount', 'comparison': 'GE', 'threshold': 5}},
        {'id': 'qualifying', 'label': '入选记录', 'op': 'PROJECT_SET', 'inputs': ['large_net'], 'params': {'field': 'entry_code'}},
        {'id': 'published_count', 'label': '发布数量', 'op': 'COUNT', 'inputs': ['qualifying'], 'params': {}},
    ]}
    graph = finalize(base, schemas, forward, policy, 1)
    sources = {'entries': ({'entry_code': 'a', 'gross': 12, 'cost': 4},
                           {'entry_code': 'b', 'gross': 12, 'cost': 8})}
    assert evaluate(graph, sources) == reference(graph, sources)
    assert evaluate(graph, sources)['qualifying'] == {'a'}
