"""Native round-trip checks for the event-sensitive peer-review task."""
from fractions import Fraction

from .hotel_deep_case import build
from .hotel_graph import read_json


def test_deep_case_depth_counterexamples_and_mixed_propagation(tmp_path):
    summary = build(tmp_path)
    audit = read_json(tmp_path / 'private/visibility.json')
    trace = read_json(tmp_path / 'private/propagation.json')
    graph = read_json(tmp_path / 'private/graph.json')
    nodes = {node['id']: node for node in trace['nodes']}
    assert summary['passed'] and summary['agent_calls'] == 0
    assert summary['target_depths'] == {'qualified': 5, 'qualified_count': 6, 'qualified_adr_mean': 6}
    assert summary['event_shortcuts']['qualified'] == 5
    assert audit['minimal_file_sets'] == [['excel', 'ppt', 'word']]
    assert len(audit['subsets']) == 8
    for row in audit['subsets']:
        if not row['sufficient']:
            assert set(row['counterworld']['changed_answers']) == set(graph['targets'])

    assert set(nodes) == set(graph['node_values'])
    assert (Fraction(nodes['peer_adr_mean']['before']), Fraction(nodes['peer_adr_mean']['after'])) == (140, 120)
    assert nodes['peer_adr_mean']['state'] == 'changed_dependent'
    assert nodes['revpar']['state'] == 'related_unchanged' and nodes['revpar']['materialization'] == 'internal_only'
    assert nodes['room_revenue']['state'] == 'unrelated_unchanged'
    assert any(location['native_action'] == 'recalculates' for location in nodes['qualified_count']['locations'])
    assert any(location['native_action'] == 'required_edit' and location['file_location']['kind'] == 'chart_workbook'
               for location in nodes['qualified']['locations'])
    assert summary['control_results']['changed_still_qualified_hotel']['pass'] is False
