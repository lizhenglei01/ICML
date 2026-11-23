"""Hotel case, registered arithmetic alternatives, and exact counterfactual truth."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .business_definitions import DEFINITIONS, SOURCES, canonical
from .engine import units_after
from .hotel_visibility import solve_public, to_json


ARTIFACTS = Path(__file__).resolve().parent / 'business_artifacts'
TARGETS = ('qualified', 'qualified_count', 'qualified_adr_mean')
BASES = ('sold_room_nights', 'available_room_nights', 'room_revenue')
RULES = ('policy_gate',)
THRESHOLDS = ('occupancy_floor', 'adr_floor')
METRIC_LABELS = {
    'sold_room_nights': '已售间夜数 / Sold room nights',
    'available_room_nights': '可售间夜数 / Available room nights',
    'room_revenue': '客房收入（元） / Room revenue (CNY)',
    'occupancy_rate': '入住率（小数） / Occupancy (decimal)',
    'average_daily_rate': '平均房价（元／间夜） / ADR (CNY per room night)',
    'revpar': '每可售间夜收入（元／间夜） / RevPAR (CNY per available room night)',
    'occupancy_floor': '最低入住率（小数） / Minimum occupancy (decimal)',
    'adr_floor': '最低平均房价（元／间夜） / Minimum ADR (CNY per room night)',
}
GATE_TEXT = '酒店须同时达到两个门槛，等于门槛也算达标。 Hotels qualify if both minimums are met. Equality qualifies.'
FORMULA_TEXT = (
    '入住率 = 已售间夜数 ÷ 可售间夜数。平均房价 = 客房收入 ÷ 已售间夜数。'
    '两者使用同一酒店、同一季度口径。 Occupancy is sold room nights divided by available room nights. '
    'ADR is room revenue divided by sold room nights, for the same hotel and quarter.'
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_json(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def input_paths():
    return {
        'forward': ARTIFACTS / 'forward_hotel/result.json',
        'backward': ARTIFACTS / 'goal_revpar/result.json',
        'one_shot': ARTIFACTS / 'hotel/author/attempt_01/proposal.json',
    }


def build_graph(paths=None):
    paths = input_paths() if paths is None else paths
    forward, backward, one_shot = (read_json(paths[key]) for key in ('forward', 'backward', 'one_shot'))
    nodes = {}
    productions = {}

    def add_node(key, kind, meaning, unit=None, source='Case configuration'):
        nodes[key] = {'id': key, 'kind': kind, 'meaning': meaning, 'unit': unit or {}, 'source': source}

    def add(key, output, op, inputs, origins, **extra):
        if op in {'ADD', 'MUL', 'AND', 'OR'}:
            inputs = sorted(inputs)
        identity = (output, op, tuple(inputs))
        if identity in productions:
            productions[identity]['origins'].extend(origins)
            return
        productions[identity] = {'id': key, 'output': output, 'op': op, 'inputs': inputs,
                                 'origins': origins, **extra}

    def ingest(channel, quantities, candidates):
        roles = {metric['id']: canonical(metric['concept']) for metric in quantities}
        for candidate in candidates:
            formula = candidate['formula']
            if any(set(arg) != {'ref'} for arg in formula['args']):
                raise ValueError('The verified hotel slice expects one binary operation per production')
            output = roles[candidate['output']]
            inputs = [roles[arg['ref']] for arg in formula['args']]
            for role in [output, *inputs]:
                definition = DEFINITIONS[role]
                add_node(role, 'base' if role in BASES else 'derived', METRIC_LABELS[role],
                         definition['unit'], SOURCES[definition['source']])
            add(candidate['id'], output, formula['op'], inputs,
                [{'mode': channel, 'candidate_id': candidate['id'], 'definition_source': DEFINITIONS[output]['source']}])

    if not forward['passed'] or not backward['passed']:
        raise ValueError('Accepted forward and backward results are required')
    ingest('LLM forward exploration', forward['quantities'], forward['productions'])
    accepted = {key for route in backward['paths'] if route['accepted'] for key in route['production_ids']}
    ingest('LLM backward search', backward['quantities'],
           [p for p in backward['productions'] if p['id'] in accepted])
    from .business_verify import verify
    verified_one_shot = verify(one_shot)
    accepted_one_shot = set(verified_one_shot['accepted_derived'])
    comparison = [{'id': 'one_shot_' + m['id'], 'output': m['id'], 'formula': m['formula']}
                  for m in one_shot['metrics'] if m['id'] in accepted_one_shot]
    ingest('LLM one-shot baseline', one_shot['metrics'], comparison)

    add_node('occupancy_floor', 'policy', METRIC_LABELS['occupancy_floor'])
    price_unit = nodes['average_daily_rate']['unit']
    add_node('adr_floor', 'policy', METRIC_LABELS['adr_floor'], price_unit)
    add_node('policy_gate', 'rule', GATE_TEXT)
    add_node('occupancy_pass', 'selection', '达到入住率门槛的酒店 / Hotels meeting occupancy floor')
    add_node('adr_pass', 'selection', '达到平均房价门槛的酒店 / Hotels meeting ADR floor')
    add_node('qualified', 'selection', '同时达到两个门槛的酒店 / Hotels meeting both floors')
    add_node('qualified_count', 'result', '达标酒店数 / Qualified hotel count', {'hotel': 1})
    add_node('qualified_adr_mean', 'result', '达标酒店平均房价的算术平均 / Unweighted mean ADR of qualifying hotels', price_unit)
    manual = [{'mode': 'human', 'source': 'Synthetic internal review policy'}]
    add('compare_occupancy_floor', 'occupancy_pass', 'GE', ['occupancy_rate', 'occupancy_floor'], manual.copy())
    add('compare_adr_floor', 'adr_pass', 'GE', ['average_daily_rate', 'adr_floor'], manual.copy())
    add('both_floors', 'qualified', 'AND', ['occupancy_pass', 'adr_pass'], manual.copy(), requires=list(RULES))
    add('count_qualified', 'qualified_count', 'COUNT', ['qualified'], manual.copy())
    add('mean_qualified_adr', 'qualified_adr_mean', 'AVG', ['qualified', 'average_daily_rate'], manual.copy())

    # Under positive room-night denominators, these equivalent compare routes
    # must not be ignored when measuring reasoning through raw evidence.
    rewrite = [{'mode': 'program audit', 'source': 'Positive-denominator cross multiplication'}]
    for suffix, count, floor, numerator, result in [
        ('occupancy', 'available_room_nights', 'occupancy_floor', 'sold_room_nights', 'occupancy_pass'),
        ('adr', 'sold_room_nights', 'adr_floor', 'room_revenue', 'adr_pass'),
    ]:
        intermediate = 'required_' + suffix + '_numerator'
        add_node(intermediate, 'arithmetic', f'门槛换算量 / {suffix} threshold in numerator units',
                 nodes[numerator]['unit'], rewrite[0]['source'])
        add('scale_' + suffix + '_floor', intermediate, 'MUL', [count, floor], rewrite.copy())
        add('compare_scaled_' + suffix, result, 'COMPARE', [numerator, intermediate], rewrite.copy(), comparison='GE')

    graph = {'schema': 'hotel-evidence-graph/1', 'entities': one_shot['entities'], 'period': one_shot['period'],
             'nodes': list(nodes.values()), 'productions': list(productions.values()), 'targets': list(TARGETS),
             'alternative_semantics': 'Several productions with the same output are alternative calculation paths. AND and OR operators combine selections.',
             'input_artifacts': {key: {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                                 for key, path in paths.items()},
             'comparison': {'one_shot_accepted': sorted(accepted_one_shot),
                            'forward_accepted_production_count': len(forward['productions']),
                            'backward_accepted_production_count': len(accepted)}}
    validate_graph(graph)
    return graph


def validate_graph(graph):
    by_id = {node['id']: node for node in graph['nodes']}
    if len(by_id) != len(graph['nodes']) or not set(graph['targets']) <= set(by_id):
        raise ValueError('Invalid graph node identities')
    for rule in graph['productions']:
        output = by_id[rule['output']]['unit']
        inputs = [by_id[key]['unit'] for key in rule['inputs']]
        op = rule['op']
        if op in {'ADD', 'SUB', 'MUL', 'DIV'} and units_after(op, inputs) != output:
            raise ValueError('Arithmetic unit mismatch for ' + rule['id'])
        if op in {'GE', 'LE', 'COMPARE'} and (inputs[0] != inputs[1] or output):
            raise ValueError('Comparison unit mismatch for ' + rule['id'])
        if op == 'AVG' and inputs[1] != output:
            raise ValueError('Average unit mismatch for ' + rule['id'])
        if any(by_id[key]['kind'] != 'rule' for key in rule.get('requires', [])):
            raise ValueError('Invalid capability binding')


def case_inputs(graph):
    # Six synthetic Q4 hotel ledgers and a single authorized correction. The
    # chosen boundary cases let us test propagation and produce counterworlds.
    observations = {
        'available_room_nights': dict(zip(graph['entities'], [10000, 9000, 10000, 11000, 9500, 12000])),
        'sold_room_nights': dict(zip(graph['entities'], [6000, 7200, 7000, 9900, 8550, 7800])),
        'room_revenue': dict(zip(graph['entities'], [1200000, 1152000, 1120000, 1287000, 1111500, 1326000])),
        'occupancy_floor': Fraction(3, 4), 'adr_floor': 140,
    }
    entity = graph['entities'][0]
    event = {'id': 'Q4-SALES-001', 'node_id': 'sold_room_nights', 'entity': entity,
             'old_value': 6000, 'new_value': 8000, 'period': graph['period'],
             'source': 'Human-defined synthetic ledger amendment'}
    return observations, event


def amended(before, event):
    after = deepcopy(before)
    if after[event['node_id']][event['entity']] != event['old_value']:
        raise ValueError('Correction does not match the previous ledger')
    after[event['node_id']][event['entity']] = event['new_value']
    return after


def reference(graph, base):
    """Independent exact calculation, separate from production execution."""
    entities = graph['entities']
    sold, capacity, revenue = (base[key] for key in BASES)
    if any(not 0 < sold[e] <= capacity[e] or revenue[e] <= 0 for e in entities):
        raise ValueError('Hotel scenario violates capacity or positive denominator constraints')
    if not 0 < base['occupancy_floor'] <= 1 or base['adr_floor'] <= 0:
        raise ValueError('Invalid policy thresholds')
    occupancy = {e: Fraction(sold[e], capacity[e]) for e in entities}
    adr = {e: Fraction(revenue[e], sold[e]) for e in entities}
    revpar = {e: Fraction(revenue[e], capacity[e]) for e in entities}
    occupancy_pass = frozenset(e for e in entities if occupancy[e] >= base['occupancy_floor'])
    adr_pass = frozenset(e for e in entities if adr[e] >= base['adr_floor'])
    qualified = occupancy_pass & adr_pass
    if not qualified:
        raise ValueError('At least one hotel must qualify for the mean question')
    return {**base, 'occupancy_rate': occupancy, 'average_daily_rate': adr, 'revpar': revpar,
            'required_occupancy_numerator': {e: capacity[e] * base['occupancy_floor'] for e in entities},
            'required_adr_numerator': {e: sold[e] * base['adr_floor'] for e in entities},
            'occupancy_pass': occupancy_pass, 'adr_pass': adr_pass, 'qualified': qualified,
            'qualified_count': len(qualified), 'qualified_adr_mean': sum(adr[e] for e in qualified) / len(qualified)}


def evaluate(graph, base):
    expected = reference(graph, base)
    proof = solve_public(graph, base, RULES, targets=list(expected))
    values = {key: record['value'] for key, record in proof['proofs'].items()}
    if values != expected or proof['conflicts'] or proof['missing_targets']:
        raise ValueError('Graph execution disagrees with the independent hotel calculation')
    return expected, proof


def stale_observations(graph, state, event):
    return {
        'prior_qualification_flags': {e: e in state['qualified'] for e in graph['entities']},
        'prior_qualified_names': ', '.join(sorted(state['qualified'])),
        'prior_qualified_count': state['qualified_count'],
        'prior_qualified_adr_mean': state['qualified_adr_mean'],
        'prior_posted_sold': state['sold_room_nights'][event['entity']],
        'event_new_sold': event['new_value'],
    }


def counterworlds(graph, before, event):
    base_state = reference(graph, before)
    history = stale_observations(graph, base_state, event)
    entity = event['entity']
    worlds = []
    for name, key, value in [
        ('different_capacity', 'available_room_nights', 12500),
        ('different_revenue', 'room_revenue', 1040000),
        ('different_adr_floor', 'adr_floor', 155),
    ]:
        alt = deepcopy(before)
        if key in THRESHOLDS:
            alt[key] = value
        else:
            alt[key][entity] = value
        old, _ = evaluate(graph, alt)
        new, _ = evaluate(graph, amended(alt, event))
        if stale_observations(graph, old, event) != history:
            raise AssertionError('Counterworld changes a pre-existing editable summary or the event')
        worlds.append({'id': name, 'observations': {**new, **history}, 'rules': list(RULES),
                       'answer': {key: new[key] for key in TARGETS},
                       'facts': {'changed_undisclosed_fact': key, 'value': value, 'before': alt,
                                 'same_event_and_old_public_summary': True}})
    return worlds


def case_record(graph):
    old_base, event = case_inputs(graph)
    new_base = amended(old_base, event)
    old, old_proof = evaluate(graph, old_base)
    new, new_proof = evaluate(graph, new_base)
    changed = []
    for key in old:
        if old[key] == new[key]:
            continue
        delta = [e for e in graph['entities'] if old[key][e] != new[key][e]] if isinstance(old[key], dict) else None
        changed.append({'node_id': key, 'changed_entities': delta, 'before': old[key], 'after': new[key]})
    graph['node_values'] = {key: {'before': old[key], 'after': new[key]} for key in old}
    graph['node_values']['policy_gate'] = {'before': GATE_TEXT, 'after': GATE_TEXT}
    return {'before_basis': old_base, 'after_basis': new_base, 'before': old, 'after': new,
            'event': event, 'changes': changed, 'preserved_nodes': sorted(k for k in old if old[k] == new[k]),
            'old_proof': old_proof, 'new_proof': new_proof,
            'counterworlds': counterworlds(graph, old_base, event)}
