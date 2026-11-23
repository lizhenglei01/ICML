import json
from pathlib import Path
import pytest

from .test_topic_pipeline import metadata_fixture
from .topic_synthesis import generate_topic_task
from .chart_rules import execute,answers
from .graph_view import diagram_model,export_graph


def fixture_task():
    task=generate_topic_task(metadata_fixture(),1,3,chart_count=4)
    document={'title_zh':'离线关系图测试','sections':[{'node_id':n['id'],'name_zh':f'候选组{i+1}'} for i,n in enumerate(task['nodes'])]}
    return task,document


def test_edges_are_registered_dependencies_and_scope_is_explicit():
    task,document=fixture_task();diagram=diagram_model(task,document)
    edges={(e['source'],e['target']) for e in diagram['edges']}
    assert diagram['private']
    for node in task['nodes']:
        rule='rule_'+node['id'];result='set_'+node['id']
        assert (rule,result) in edges
        for previous in node['inputs']:assert ('set_'+previous,rule) in edges
        if node['op']=='ATOM':assert (node['chart_id'],rule) in edges
    assert any(n['inputs'] for n in task['nodes'] if n['op']=='ATOM')
    labels={n['id']:n['label'] for n in diagram['nodes']}
    assert f"{answers(task)['count']} 个" in labels['answer_count']
    assert 'AVG' not in labels['answer_entities']


def test_private_exports_include_readable_png_and_svg_and_no_public_graph(tmp_path):
    task,document=fixture_task();result=export_graph(tmp_path,task,document)
    assert result['private'] and not (tmp_path/'public').exists()
    private=tmp_path/'private'
    assert (private/'dependency_graph.png').read_bytes().startswith(b'\x89PNG')
    assert '<svg' in (private/'dependency_graph.svg').read_text()
    saved=json.loads((private/'dependency_graph.json').read_text())
    assert len(saved['nodes'])==result['nodes'] and len(saved['edges'])==result['edges']
