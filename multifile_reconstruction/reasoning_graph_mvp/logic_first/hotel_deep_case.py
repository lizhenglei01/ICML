"""Construct one native peer-benchmark task and verify its real dependencies."""
import argparse
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

from .hotel_evidence import draw_overview, make_slots, native_bundles
from .hotel_deep_shortcuts import audit_event_shortcuts
from .hotel_graph import (ARTIFACTS, BASES, METRIC_LABELS, RULES, TARGETS,
                          amended, build_graph, case_inputs, save_json, stale_observations, validate_graph)
from .hotel_office import FILES, build_layout, display, grade, native_controls
from .hotel_propagation import write_propagation
from .hotel_visibility import audit_layout, solve_public


DEFAULT = ARTIFACTS / 'hotel_deep_case'
POLICY = (
    '以入住率低于最低门槛的酒店作为价格对照组，取其平均房价的算术平均，再加价格溢价，得到本次评审的房价线。'
    '入选酒店须同时满足入住率不低于最低门槛、平均房价不低于房价线。两项判断均含等号。 '
    'Use hotels below the minimum occupancy as the price peer group. Add the price premium to their '
    'unweighted mean ADR to get the rate hurdle. Qualifying hotels must meet both the minimum occupancy '
    'and the rate hurdle. Equality qualifies for both minimums.'
)
LABELS = {**METRIC_LABELS, 'adr_premium': '价格溢价（元／间夜） / Price premium (CNY per room night)'}
EXTRA_RESULTS = (
    ('peer_cohort', '价格对照组 / Price peer hotels'),
    ('peer_adr_mean', '对照组平均房价（元／间夜） / Price peer mean ADR (CNY per room night)'),
    ('review_adr_floor', '本期房价线（元／间夜） / Rate hurdle (CNY per room night)'),
)


def graph_for_peers():
    graph = build_graph()
    removed = {'adr_floor', 'adr_pass', 'required_adr_numerator'}
    graph['nodes'] = [node for node in graph['nodes'] if node['id'] not in removed]
    graph['productions'] = [rule for rule in graph['productions'] if rule['output'] not in removed
                            and not set(rule['inputs']) & removed]
    nodes = {node['id']: node for node in graph['nodes']}
    price = nodes['average_daily_rate']['unit']
    source = 'Synthetic Q4 pilot review policy'
    nodes['policy_gate'].update(meaning=POLICY, source=source, diagram_label='政策｜低入住率组定基准，双门槛入选')
    graph['nodes'].extend([
        {'id': key, 'kind': kind, 'meaning': meaning, 'unit': unit, 'source': source}
        for key, kind, meaning, unit in [
            ('adr_premium', 'policy', LABELS['adr_premium'], price),
            ('peer_cohort', 'selection', '低入住率价格对照组 / Low-occupancy price peers', {}),
            ('peer_adr_mean', 'result', '对照组等权平均房价 / Mean peer ADR', price),
            ('review_adr_floor', 'arithmetic', '对照组平均房价加溢价 / Peer mean plus premium', price),
            ('adjusted_adr', 'arithmetic', '各酒店房价减溢价 / ADR less premium', price),
            ('rate_pass', 'selection', '达到本期房价线的酒店 / Hotels meeting the rate hurdle', {}),
        ]
    ])

    def rule(identifier, output, op, inputs, *, audit=None, comparison=None):
        spec = {'id': identifier, 'output': output, 'op': op, 'inputs': inputs,
                'origins': [{'mode': 'program audit' if audit else 'human', 'source': audit or source}],
                'requires': list(RULES)}
        if comparison:
            spec['comparison'] = comparison
        return spec

    graph['productions'].extend([
        rule('select_peer_cohort', 'peer_cohort', 'COMPARE', ['occupancy_rate', 'occupancy_floor'], comparison='LT'),
        rule('select_peers_without_division', 'peer_cohort', 'COMPARE', ['sold_room_nights', 'required_occupancy_numerator'],
             comparison='LT', audit='Positive-denominator cross multiplication'),
        rule('compute_mean_peer_adr', 'peer_adr_mean', 'AVG', ['peer_cohort', 'average_daily_rate']),
        rule('add_peer_premium', 'review_adr_floor', 'ADD', ['peer_adr_mean', 'adr_premium']),
        rule('test_rate_hurdle', 'rate_pass', 'GE', ['average_daily_rate', 'review_adr_floor']),
        rule('subtract_premium_first', 'adjusted_adr', 'SUB', ['average_daily_rate', 'adr_premium'],
             audit='Move the premium to the left side of the inequality'),
        rule('test_rate_against_peer_mean', 'rate_pass', 'GE', ['adjusted_adr', 'peer_adr_mean'],
             audit='Move the premium to the left side of the inequality'),
        rule('both_floors', 'qualified', 'AND', ['occupancy_pass', 'rate_pass']),
    ])
    graph['schema'] = 'hotel-peer-evidence-graph/1'
    graph['policy'] = {'origin': 'Human-authored synthetic review policy', 'description': POLICY}
    validate_graph(graph)
    return graph


def inputs(graph):
    basis, event = case_inputs(graph)
    del basis['adr_floor']
    # Base revenue uses six chosen ledgers. A single sales correction changes
    # group membership without altering either revenue or available capacity.
    basis['room_revenue'] = dict(zip(graph['entities'], [1080000, 1152000, 770000, 1237500, 983250, 1014000]))
    basis['adr_premium'] = 5
    event['id'] = 'Q4-PEER-001'
    return basis, event


def exact_reference(graph, base):
    sold, capacity, revenue = (base[key] for key in BASES)
    entities = graph['entities']
    if any(not 0 < sold[e] <= capacity[e] or revenue[e] <= 0 for e in entities):
        raise ValueError('Invalid hotel ledger')
    floor, premium = base['occupancy_floor'], base['adr_premium']
    if not 0 < floor < 1 or premium < 0:
        raise ValueError('Invalid review terms')
    occupancy = {e: Fraction(sold[e], capacity[e]) for e in entities}
    adr = {e: Fraction(revenue[e], sold[e]) for e in entities}
    peers = frozenset(e for e in entities if occupancy[e] < floor)
    if not peers:
        raise ValueError('Peer mean needs a nonempty cohort')
    peer_mean = sum(adr[e] for e in peers) / len(peers)
    rate_hurdle = peer_mean + premium
    occupancy_pass = frozenset(e for e in entities if occupancy[e] >= floor)
    rate_pass = frozenset(e for e in entities if adr[e] >= rate_hurdle)
    qualified = frozenset(e for e in entities if occupancy[e] >= floor and adr[e] >= rate_hurdle)
    if not qualified:
        raise ValueError('Qualified mean needs a nonempty cohort')
    return {**base, 'occupancy_rate': occupancy, 'average_daily_rate': adr,
            'revpar': {e: Fraction(revenue[e], capacity[e]) for e in entities},
            'required_occupancy_numerator': {e: floor * capacity[e] for e in entities},
            'peer_cohort': peers, 'peer_adr_mean': peer_mean, 'review_adr_floor': rate_hurdle,
            'adjusted_adr': {e: adr[e] - premium for e in entities}, 'occupancy_pass': occupancy_pass,
            'rate_pass': rate_pass, 'qualified': qualified, 'qualified_count': len(qualified),
            'qualified_adr_mean': sum(adr[e] for e in qualified) / len(qualified)}


def execute_checked(graph, base):
    expected = {**exact_reference(graph, base), 'policy_gate': POLICY}
    proof = solve_public(graph, {**base, 'policy_gate': POLICY}, RULES, targets=list(expected))
    actual = {key: result['value'] for key, result in proof['proofs'].items()}
    if actual != expected or proof['conflicts'] or proof['missing_targets']:
        raise ValueError('Unified graph disagrees with independent Fraction reference')
    return expected, proof


def old_public_values(graph, state, event):
    result = stale_observations(graph, state, event)
    for node, _ in EXTRA_RESULTS:
        value = state[node]
        result['prior_' + node] = ', '.join(sorted(value)) if isinstance(value, frozenset) else value
    return result


def hidden_worlds(graph, base, event, before):
    old_public = old_public_values(graph, before, event)
    entity, _, maplecrest = graph['entities'][:3]
    changes = {
        'excel': {'available_room_nights': {entity: 12500}},
        'ppt': {'room_revenue': {entity: 960000, maplecrest: 910000}},
        'word': {'adr_premium': 20},
    }
    worlds = []
    for file, edit in changes.items():
        alternative = deepcopy(base)
        for key, value in edit.items():
            if isinstance(value, dict):
                alternative[key].update(value)
            else:
                alternative[key] = value
        alt_before, _ = execute_checked(graph, alternative)
        alt_after, _ = execute_checked(graph, amended(alternative, event))
        alt_public = old_public_values(graph, alt_before, event)
        if 'prior_review_adr_floor' in alt_public and file == 'word':
            # The old rate-hurdle output is in Word, alongside the hidden
            # premium that changes in this particular counterexample.
            comparable = set(old_public) - {'prior_review_adr_floor'}
        else:
            comparable = set(old_public)
        if any(alt_public[key] != old_public[key] for key in comparable):
            raise ValueError('Counterexample changes an observable prior result')
        worlds.append({'id': f'counterexample_without_{file}', 'observations': {**alt_after, **alt_public},
                       'rules': list(RULES), 'answer': {node: alt_after[node] for node in TARGETS},
                       'facts': {'file_with_alternative_evidence': FILES[file], 'changed_hidden_basis': edit,
                                 'same_correction': True, 'comparable_prior_fields': sorted(comparable)}})
    return worlds


def make_case(graph):
    old_basis, event = inputs(graph)
    new_basis = amended(old_basis, event)
    before, old_proof = execute_checked(graph, old_basis)
    after, new_proof = execute_checked(graph, new_basis)
    graph['node_values'] = {key: {'before': before[key], 'after': after[key]} for key in before}
    changes = [{'node_id': key, 'before': value, 'after': after[key]} for key, value in before.items() if value != after[key]]
    case = {'event': event, 'before_basis': old_basis, 'after_basis': new_basis, 'before': before, 'after': after,
            'old_proof': old_proof, 'new_proof': new_proof, 'changes': changes,
            'preserved_nodes': sorted(key for key in before if before[key] == after[key]),
            'counterworlds': hidden_worlds(graph, old_basis, event, before)}
    w, h, _, s = graph['entities'][:4]
    if (before['peer_adr_mean'], after['peer_adr_mean'], before['review_adr_floor'], after['review_adr_floor']) != (140, 120, 145, 125):
        raise AssertionError('Peer threshold hand calculation failed')
    if before['qualified'] != {h} or after['qualified'] != {h, w, s} or after['qualified_adr_mean'] != 140:
        raise AssertionError('Qualification hand calculation failed')
    if before['revpar'] != after['revpar'] or before['revpar'][w] != 108:
        raise AssertionError('RevPAR must remain invariant under this correction')
    return case


def task_text(event):
    hotel = event['entity']
    return f'''# 季度酒店定价试点复核 / Quarterly pricing pilot review

本次更正将 {hotel} 的已售间夜数从 {event['old_value']:,} 调整为 {event['new_value']:,}。其他基础记录保持原口径。文件中的来源记录是已更正的依据，已发布的复核结果仍是上个版本。

请依据材料中的政策重新核对所有酒店。更正 Excel 的已登记间夜数和受影响的资格标记。更新 Word 中的价格对照组、对照组平均房价、房价线、入选名单、家数和入选酒店的等权平均房价。同步修改 PPT 的资格表与可编辑图表。名单用英文逗号和空格分隔。保留现有公式、格式、来源记录，以及无需变化的内容。

The correction changes sold room nights for {hotel} from {event['old_value']:,} to {event['new_value']:,}. The other base records retain their prior basis. Source records already reflect the correction. Issued review results reflect the previous version.

Apply the policy in the materials to the full roster. Correct the posted amount and affected qualification flags in Excel. Update the price peer roster, mean peer ADR, rate hurdle, qualifying names, qualified count and unweighted qualified mean ADR in Word. Update the corresponding qualification table and editable chart in PowerPoint. Use comma and space between names. Preserve formulas, formatting, source evidence and unaffected contents.
'''


def check_preserved_qualification(package, oracle, positions):
    stable_flag = next(row for row in positions if row['file'] == FILES['excel'])
    loc = stable_flag['file_location']
    with TemporaryDirectory(prefix='hotel_unchanged_flag_') as temp:
        probe = Path(temp) / 'submission'
        shutil.copytree(package / 'private/reference', probe)
        workbook_path = probe / stable_flag['file']
        workbook = load_workbook(workbook_path)
        workbook[loc['sheet']][loc['cell']] = display(False)
        workbook.save(workbook_path)
        workbook.close()
        result = grade(probe, oracle)
    rejected = {check['object'] for check in result['checks'] if not check['pass']}
    if result['pass'] or stable_flag['evidence_id'] not in rejected:
        raise ValueError('Oracle accepted a change to a related but stable status')
    return {'pass': result['pass'], 'expected': False, 'groups': result['groups'], 'failed_objects': sorted(rejected)}


def write_index(root):
    links = []
    for title, prefix in [('初始材料', 'public'), ('标准参考文件', 'private/reference')]:
        entries = ' · '.join(f'<a href="{prefix}/{name}">{label}</a>'
                             for name, label in zip(FILES.values(), ('Excel', 'Word', 'PPT')))
        links.append(f'<p><strong>{title}</strong>　{entries}</p>')
    links.extend([
        '<p><a href="public/instruction.md">修改任务</a></p>',
        '<p><a href="private/dependency_graph.svg">依赖图</a> · <a href="private/graph.json">完整图数据</a> · '
        '<a href="private/propagation.md">传播明细</a> · <a href="private/propagation.json">节点真值和证据位置</a></p>',
        '<p><a href="private/visibility.json">文件子集验证</a> · <a href="private/shortcuts.json">推理捷径检查</a> · '
        '<a href="private/oracle.json">Oracle</a> · <a href="summary.json">验证总表</a></p>',
    ])
    body = '\n'.join(links)
    (root / 'index.html').write_text(f'''<!doctype html>
<html lang="zh"><meta charset="utf-8"><title>酒店定价试点复核</title>
<style>body{{font:17px system-ui;line-height:1.7;max-width:820px;margin:3rem auto;padding:0 1rem}}</style>
<h1>酒店定价试点复核</h1><p>按入住率确定价格对照组，依其平均房价确定试点门槛。</p>
{body}</html>
''', encoding='utf-8')


def build(root=DEFAULT):
    root = Path(root)
    graph = graph_for_peers()
    case = make_case(graph)
    current = case['after']
    placement = {'files': {
        'excel': {'observations': {key: current[key] for key in ('sold_room_nights', 'available_room_nights')}, 'rules': []},
        'ppt': {'observations': {'room_revenue': current['room_revenue']}, 'rules': []},
        'word': {'observations': {key: current[key] for key in ('occupancy_floor', 'adr_premium')}, 'rules': list(RULES)},
    }}
    slots, old_surfaces, new_surfaces = make_slots(graph, case, placement, metric_labels=LABELS, gate_text=POLICY,
                                                   extra_word_results=EXTRA_RESULTS)
    native = build_layout(root, slots, old_surfaces, new_surfaces, task_text(case['event']))
    oracle = native['oracle']
    controls = native_controls(root, oracle)
    observed = native_bundles(graph, native['before_observations'], include_prior=True, gate_text=POLICY)
    common = {'observations': {'event_new_sold': case['event']['new_value']}, 'rules': []}
    expected = {node: current[node] for node in TARGETS}
    audit = audit_layout(graph, observed, expected, common=common, worlds=case['counterworlds'])
    minimum = audit['proof']['proofs']['qualified']['depth']
    if not audit['passed'] or audit['minimum_files'] != 3 or minimum < 4:
        raise ValueError('Public artifact subset/depth audit failed')
    if any(not row['sufficient'] and not row['counterworld'] for row in audit['subsets']):
        raise ValueError('A strict Office subset lacks an independently checked counterexample')
    if any(not row['sufficient'] and set(row['counterworld']['changed_answers']) != set(TARGETS)
           for row in audit['subsets']):
        raise ValueError('All evaluated answers must differ in the indistinguishable counterexamples')
    ref = native_bundles(graph, native['after_observations'], include_prior=False, gate_text=POLICY)
    ref_audit = audit_layout(graph, ref, expected, common=common, enumerate_paths=False)
    if not ref_audit['passed'] or ref_audit['minimum_files'] != 3:
        raise ValueError('Reference evidence changed')

    public_values = {node: value for source in observed.values() for node, value in source['observations'].items()}
    prior = {'peer_cohort': public_values['prior_peer_cohort'].split(', '),
             'peer_adr_mean': public_values['prior_peer_adr_mean'],
             'qualified': public_values['prior_qualified_names'].split(', '),
             'qualified_count': public_values['prior_qualified_count'],
             'qualified_adr_mean': public_values['prior_qualified_adr_mean']}
    shortcuts = audit_event_shortcuts(public_values, prior, case['event'], expected)
    target_depths = {key: value['depth'] for key, value in audit['proof']['proofs'].items()}
    if not shortcuts['passed'] or min(target_depths['qualified'], shortcuts['best_tested_elementary_depths']['qualified']) < 4:
        raise ValueError('Alternative inference audit no longer supports the required depth')
    trace = write_propagation(root, graph, case, oracle)
    related_stable = [node for node in trace['nodes'] if node['state'] == 'related_unchanged']
    stable_office = [location for node in trace['nodes'] for location in node['locations']
                     if location['native_action'] == 'preserve' and location.get('entity') == graph['entities'][1]
                     and node['id'] == 'qualified']
    if not related_stable or not stable_office:
        raise ValueError('Required stable dependency and protection case is absent')
    controls['changed_still_qualified_hotel'] = check_preserved_qualification(root, oracle, stable_office)
    save_json(root / 'private/native_controls.json', controls)

    save_json(root / 'private/graph.json', graph)
    save_json(root / 'private/case.json', case)
    save_json(root / 'private/visibility.json', audit)
    save_json(root / 'private/shortcuts.json', shortcuts)
    draw_overview(root / 'private', graph, case, title='酒店定价试点复核｜数值、规则与变更依赖')
    result = {'passed': True, 'case': 'hotel_peer_rate_hurdle', 'event': case['event'],
              'target_depths': target_depths, 'event_shortcuts': shortcuts['best_tested_elementary_depths'],
              'minimum_files': audit['minimum_files'], 'necessary_files': audit['necessary_files'],
              'counterexample_count': sum(row.get('counterworld') is not None for row in audit['subsets']),
              'reference_grade': native['reference_grade']['groups'], 'control_results': controls,
              'changed_nodes': [item['node_id'] for item in case['changes']],
              'propagation_counts': trace['counts'], 'protected_related_objects': [row['evidence_id'] for row in stable_office],
              'stable_example': {'node_id': 'revpar', 'before': 108, 'after': 108},
              'agent_calls': 0}
    write_index(root)
    save_json(root / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    result = build(parser.parse_args().output)
    print(f"Verified native task. Target depths {result['target_depths']}, files {result['minimum_files']}")


if __name__ == '__main__':
    main()
