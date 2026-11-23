"""Build paired native Office tasks differing in how net room revenue is revealed."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from ...evidence_transform import DifferenceEvidence
from .hotel_deep_case import (
    EXTRA_RESULTS, LABELS, POLICY, exact_reference, graph_for_peers, make_case,
    old_public_values, task_text,
)
from .hotel_deep_shortcuts import audit_event_shortcuts
from .hotel_evidence import draw_overview, make_slots, native_bundles
from .hotel_graph import ARTIFACTS, RULES, TARGETS, amended, save_json, validate_graph
from .hotel_office import FILES, build_layout, native_controls, snapshot
from .hotel_propagation import write_propagation
from .hotel_revenue_leaks import audit_concealed_values
from .hotel_visibility import audit_layout, solve_public


DEFAULT = ARTIFACTS / 'hotel_revenue_evidence'
NET = 'room_revenue'
RECEIPTS = 'booked_room_receipts'
REFUNDS = 'room_refunds'
RULE_ID = 'net_room_revenue_after_refunds'
NET_POLICY = (
    ' 计算平均房价时，客房收入按同一酒店、同一季度的客房入账金额扣除已确认客房退款后的净额计算。'
    ' 收入简报若已列净额，直接采用该净额。'
    ' For ADR, use net room revenue after deducting confirmed room refunds from booked room receipts'
    ' for the same hotel and quarter. Use the net amount directly when the briefing already reports it.'
)
GATE = POLICY + NET_POLICY
REVENUE_LABELS = {
    **LABELS,
    NET: '净客房收入（元） / Net room revenue (CNY)',
    RECEIPTS: '客房入账金额（元） / Booked room receipts (CNY)',
    REFUNDS: '已确认客房退款（元） / Confirmed room refunds (CNY)',
}
REVENUE_EVIDENCE = DifferenceEvidence(RECEIPTS, REFUNDS, NET, REVENUE_LABELS)


def shared_case():
    graph = graph_for_peers()
    case = make_case(graph)
    nodes = {node['id']: node for node in graph['nodes']}
    nodes['policy_gate']['meaning'] = GATE
    graph['policy']['description'] = GATE
    nodes[NET].update(kind='arithmetic', meaning=REVENUE_LABELS[NET], source='Quarterly netting policy')
    for node in (RECEIPTS, REFUNDS):
        graph['nodes'].append({'id': node, 'kind': 'base', 'meaning': REVENUE_LABELS[node],
                               'unit': nodes[NET]['unit'], 'source': 'Synthetic Q4 settlement ledger'})
    graph['productions'].append(REVENUE_EVIDENCE.production(
        RULE_ID, rules=RULES, source='Quarterly netting policy'))
    validate_graph(graph)

    # Refunds are independent of the room-night correction. The first amount
    # is the Willowbank example; the remaining amounts form a synthetic ledger.
    refunds = dict(zip(graph['entities'], [45_000, 24_000, 36_000, 15_000, 22_000, 29_000], strict=True))
    canonical_before = case['before_basis']
    for when, proof_key in (('before', 'old_proof'), ('after', 'new_proof')):
        basis = REVENUE_EVIDENCE.realize_from_ledger(case[f'{when}_basis'], refunds)
        del basis[NET]
        expected = REVENUE_EVIDENCE.realize_from_ledger(case[when], refunds)
        expected['policy_gate'] = GATE
        proof = solve_public(graph, {**basis, 'policy_gate': GATE}, RULES, targets=expected)
        actual = {key: entry['value'] for key, entry in proof['proofs'].items()}
        if actual != expected or proof['conflicts']:
            raise ValueError(f'Netting execution disagrees with independent business truth in {when}')
        case[f'{when}_basis'] = basis
        case[when], case[proof_key] = expected, proof

    graph['node_values'] = {key: {when: case[when][key] for when in ('before', 'after')}
                            for key in case['before']}
    case['preserved_nodes'] = sorted(key for key, pair in graph['node_values'].items()
                                     if pair['before'] == pair['after'])
    for world in case['counterworlds']:
        world['observations'] = REVENUE_EVIDENCE.realize_from_ledger(world['observations'], refunds)

    # Change only refunds, holding receipts and every other visible fact fixed.
    # The compensated pre-event rates preserve the published old peer mean.
    willow, _, maplecrest = graph['entities'][:3]
    alternate_net = dict(canonical_before[NET])
    alternate_net[willow] -= 12_000
    alternate_net[maplecrest] += 14_000
    alternate_basis = {**canonical_before, NET: alternate_net}
    alternate_before = exact_reference(graph, alternate_basis)
    alternate_after = exact_reference(graph, amended(alternate_basis, case['event']))
    historical = old_public_values(graph, alternate_before, case['event'])
    if historical != old_public_values(graph, case['before'], case['event']):
        raise ValueError('Refund-only alternative changes a published historical result')
    receipts = case['after'][RECEIPTS]
    alternate_refunds = {key: receipts[key] - alternate_net[key] for key in receipts}
    alternate_observations = REVENUE_EVIDENCE.realize_from_ledger(
        {**alternate_after, **historical}, alternate_refunds)
    if alternate_observations[RECEIPTS] != receipts:
        raise ValueError('Refund-only alternative changes receipts')
    refund_world = {
        'id': 'counterexample_without_refunds',
        'observations': alternate_observations, 'rules': list(RULES),
        'answer': {key: alternate_after[key] for key in TARGETS},
        'facts': {'unchanged_receipts': receipts, 'alternative_refunds': alternate_refunds,
                  'same_correction': True, 'same_published_history': True},
    }
    return graph, case, refund_world


def observations(bundles):
    return {key: value for bundle in bundles.values() for key, value in bundle['observations'].items()}


def audit_revenue(graph, bundles, expected, indirect):
    values = observations(bundles)
    rules = [rule for bundle in bundles.values() for rule in bundle['rules']]
    proof = solve_public(graph, values, rules, targets=[NET])
    node = proof['proofs'].get(NET)
    if node is None or node['value'] != expected or proof['conflicts'] or node['depth'] != int(indirect):
        raise ValueError('The rendered evidence fails the declared net revenue calculation')
    if indirect:
        # Independently compute from the materialized operands; no graph or oracle data.
        reconstructed = REVENUE_EVIDENCE.recover(values)
        if NET in values or reconstructed != expected or node['production'] != RULE_ID:
            raise ValueError('Independent native readback did not recover the concealed revenue')
    else:
        reconstructed = values[NET]
    return {'room_revenue_depth': node['depth'], 'reconstructed_room_revenue': reconstructed,
            'proof': node}


def build_variant(root, graph, case, refund_world, *, indirect):
    current = case['after']
    expected = {key: current[key] for key in TARGETS}
    common = {'observations': {'event_new_sold': case['event']['new_value']}, 'rules': []}
    operands = REVENUE_EVIDENCE.operands(current, indirect=indirect)
    placement = {'files': {
        'excel': {'observations': {**{key: current[key] for key in
                                  ('sold_room_nights', 'available_room_nights')}, **operands['excel']}, 'rules': []},
        'word': {'observations': {key: current[key] for key in ('occupancy_floor', 'adr_premium')},
                 'rules': list(RULES)},
        'ppt': {'observations': operands['ppt'], 'rules': []},
    }}
    slots, old_surfaces, new_surfaces = make_slots(
        graph, case, placement, metric_labels=REVENUE_LABELS, gate_text=GATE, extra_word_results=EXTRA_RESULTS)
    native = build_layout(root, slots, old_surfaces, new_surfaces, task_text(case['event']))
    oracle = native['oracle']
    original = native_bundles(graph, native['before_observations'], include_prior=True, gate_text=GATE)
    revised = native_bundles(graph, native['after_observations'], include_prior=False, gate_text=GATE)
    visibility = audit_layout(graph, original, expected, common=common, worlds=case['counterworlds'])
    reference = audit_layout(graph, revised, expected, common=common, enumerate_paths=False)
    if not (visibility['passed'] and reference['passed'] and visibility['minimum_files'] == reference['minimum_files'] == 3):
        raise ValueError('Native Office sources no longer support the same answers with three necessary files')
    for subset in visibility['subsets']:
        if not subset['sufficient'] and set((subset['counterworld'] or {}).get('changed_answers', ())) != set(TARGETS):
            raise ValueError('An incomplete file set lacks a verified counterexample for all targets')
    revenue = audit_revenue(graph, original, current[NET], indirect)
    revenue['reference_readback'] = audit_revenue(graph, revised, current[NET], indirect)
    if indirect:
        for name, directory in (('public', root / 'public'), ('reference', root / 'private/reference')):
            scan = audit_concealed_values(directory, current[NET])
            revenue[f'{name}_concealed_value_audit'] = scan
            if not scan['passed']:
                raise ValueError(f'Net revenue surfaced in {name}: {scan["findings"]}')
        masked = deepcopy(original)
        del masked['excel']['observations'][REFUNDS]
        without_refunds = audit_layout(
            graph, masked, expected, common=common, worlds=[refund_world], enumerate_paths=False)
        full_masked = without_refunds['subsets'][-1]
        witness = full_masked['counterworld']
        if full_masked['sufficient'] or not witness or set(witness['changed_answers']) != set(TARGETS):
            raise ValueError('Could not establish necessity of the refund evidence with other sources fixed')
        revenue['without_refunds'] = full_masked

    public_values = observations(original)
    prior = {key: public_values['prior_' + source] for key, source in (
        ('peer_cohort', 'peer_cohort'), ('peer_adr_mean', 'peer_adr_mean'),
        ('qualified', 'qualified_names'), ('qualified_count', 'qualified_count'),
        ('qualified_adr_mean', 'qualified_adr_mean'))}
    for key in ('peer_cohort', 'qualified'):
        prior[key] = prior[key].split(', ')
    shortcuts = audit_event_shortcuts(public_values, prior, case['event'], expected)
    trace = write_propagation(root, graph, case, oracle)
    controls = native_controls(root, oracle)
    private = root / 'private'
    for name, data in (('case', case), ('visibility', visibility), ('revenue_evidence', revenue),
                       ('shortcuts', shortcuts)):
        save_json(private / f'{name}.json', data)
    depth = {key: proof['depth'] for key, proof in visibility['proof']['proofs'].items()}
    result = {'passed': True, 'room_revenue_depth': revenue['room_revenue_depth'],
              'target_depths': depth, 'minimum_files': visibility['minimum_files'],
              'verified_incomplete_subsets': visibility['insufficient_subsets_with_counterworlds'],
              'reference_grade': native['reference_grade']['groups'],
              'propagation_counts': trace['counts'],
              'negative_controls': [key for key, control in controls.items() if not control['pass']],
              'indirect_source_necessary': indirect and bool(revenue['without_refunds']['counterworld'])}
    save_json(root / 'summary.json', result)
    return result


def build(root=DEFAULT):
    root = Path(root)
    graph, case, refund_world = shared_case()
    variants = {kind: build_variant(root / kind, graph, case, refund_world, indirect=kind == 'indirect')
                for kind in ('direct', 'indirect')}
    common_prefixes = tuple(FILES[kind] + ':' for kind in ('excel', 'word'))
    for relative in ('public', 'private/reference'):
        direct, indirect = (root / kind / relative for kind in variants)
        states = [{key: value for key, value in snapshot(directory, tuple(FILES.values())).items()
                   if key.startswith(common_prefixes)} for directory in (direct, indirect)]
        if states[0] != states[1]:
            raise ValueError('The paired Excel and Word states differ')
    if (root / 'direct/public/instruction.md').read_bytes() != (root / 'indirect/public/instruction.md').read_bytes():
        raise ValueError('Paired prompts differ')
    save_json(root / 'private/graph.json', graph)
    save_json(root / 'private/refund_counterexample.json', refund_world)
    draw_overview(root / 'private', graph, case, title='酒店定价复核｜直接与间接收入证据')
    result = {'passed': True, 'event': case['event'], 'layouts': variants,
              'paired_excel_word_state_equal': True, 'same_instruction': True,
              'business_truth_before': {key: case['before'][key] for key in TARGETS},
              'business_truth_after': {key: case['after'][key] for key in TARGETS},
              'agent_calls': 0}
    save_json(root / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = build(args.output)
    print(f"Passed paired hotel task at {args.output}")
    for kind, layout in result['layouts'].items():
        print(kind, 'net revenue depth', layout['room_revenue_depth'],
              'target depths', layout['target_depths'])


if __name__ == '__main__':
    main()

