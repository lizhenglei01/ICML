"""Protect cross-table types, rank cutoffs, and the two independent executors."""
from fractions import Fraction

import pytest

from .program_operators import Column, OPERATORS, compile_operator, table
from .program_reference import execute_step


def test_join_group_and_rank_from_stable_entity_keys():
    supplier = Column('entity:supplier')
    region = Column('group:region')
    money = Column('number', '元')
    orders = table({'order_id': Column('record:order_id'), 'supplier': supplier, 'amount': money}, ['order_id'])
    suppliers = table({'supplier': supplier, 'region': region}, ['supplier'])
    records = ({'order_id': 'R1', 'supplier': 'A', 'amount': 11},
               {'order_id': 'R2', 'supplier': 'B', 'amount': 21},
               {'order_id': 'R3', 'supplier': 'A', 'amount': 10})
    directory = ({'supplier': 'A', 'region': 'West'}, {'supplier': 'B', 'region': 'East'})

    def step(name, schemas, values, params):
        schema, compiled = compile_operator(name, schemas, params)
        computed = OPERATORS[name].execute(values, compiled)
        assert computed == execute_step(name, compiled, values)
        return schema, computed

    linked, rows = step('JOIN', [orders, suppliers], [records, directory], {'on': 'supplier', 'fields': ['region']})
    grouped, parts = step('GROUP_BY', [linked], [rows], {'by': ['region']})
    totals_schema, totals = step('GROUP_SUM', [grouped], [parts], {'value': 'amount', 'as': 'total'})
    assert totals == ({'region': 'East', 'total': 21}, {'region': 'West', 'total': 21})
    mean_schema, means = step('GROUP_AVG', [grouped], [parts], {'value': 'amount', 'as': 'mean'})
    assert means[1]['mean'] == Fraction(21, 2) and mean_schema.columns['mean'] == money
    ranked, order = step('RANK', [totals_schema], [totals], {'score': 'total', 'order': 'DESC'})
    _, top = step('TOP_K', [ranked], [order], {'k': 1})
    _, max_row = step('ARGMAX', [totals_schema], [totals], {'score': 'total'})
    assert top == max_row == ({'region': 'East', 'total': 21, 'rank': 1},)

    region_set, top_regions = step('PROJECT_SET', [totals_schema], [top], {'field': 'region'})
    supplier_set, supplier_names = step('PROJECT_SET', [suppliers], [directory], {'field': 'supplier'})
    with pytest.raises(ValueError, match='same key domain'):
        compile_operator('FILTER_IN', [suppliers, supplier_set], {'field': 'region'})
    with pytest.raises(ValueError, match='same key domain'):
        compile_operator('AND', [region_set, supplier_set], {})
    logic, roster = step('OR', [supplier_set] * 3, [frozenset(), frozenset({'C'}), supplier_names], {})
    _, restricted = step('AND', [logic, supplier_set], [roster, supplier_names], {})
    assert restricted == {'A', 'B'} and top_regions == {'East'}

    with pytest.raises(ValueError, match='Missing lookup key'):
        OPERATORS['LOOKUP'].execute([records, directory[:1]], {'on': 'supplier', 'field': 'region'})
    with pytest.raises(ValueError, match='Duplicate lookup key'):
        OPERATORS['LOOKUP'].execute([records, (*directory, directory[0])], {'on': 'supplier', 'field': 'region'})
