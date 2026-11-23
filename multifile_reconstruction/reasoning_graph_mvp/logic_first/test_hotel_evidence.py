"""End-to-end controls using the saved model proposals and real Office readback."""
from fractions import Fraction
import shutil

from docx import Document

from .hotel_evidence import build
from .hotel_graph import read_json
from .hotel_office import FILES, TEXT_SEPARATOR, grade


def test_four_native_layouts_preserve_truth_and_independently_control_visibility(tmp_path):
    summary = build(tmp_path)
    assert summary['passed']
    assert summary['revpar_minimum_depth_from_base'] == 1

    case = read_json(tmp_path / 'private/case.json')
    assert case['before']['qualified_count'] == 1
    assert case['after']['qualified_count'] == 2
    assert Fraction(case['after']['qualified_adr_mean']) == 155
    assert case['before']['revpar'] == case['after']['revpar']
    for name, layout in summary['layouts'].items():
        cross_file = name.endswith('cross_file')
        shallow = name.startswith('shallow')
        assert layout['necessary_files'] == (['excel', 'ppt', 'word'] if cross_file else ['excel'])
        assert layout['target_depths']['qualified'] == (2 if shallow else 3)
        assert layout['minimum_depth'] == (3 if shallow else 4)
        assert set(layout['controls'].values()) == {True, False}
        suite = tmp_path / 'layouts' / name
        oracle = read_json(suite / 'private/oracle.json')
        assert (oracle['count_formula']['before'], oracle['count_formula']['after']) == (1, 2)
        assert len(list((suite / 'public').iterdir())) == 4
        report = read_json(suite / 'private/visibility.json')
        assert len(report['subsets']) == 8
        assert all(row['sufficient'] or row['counterworld'] for row in report['subsets'])

    # The public edit request permits any order for the two qualifying hotels.
    suite = tmp_path / 'layouts/deep_cross_file/private'
    oracle = read_json(suite / 'oracle.json')
    submission = tmp_path / 'permuted_submission'
    shutil.copytree(suite / 'reference', submission)
    word = submission / FILES['word']
    doc = Document(word)
    binding = next(row for row in oracle['bindings'] if row.get('history_id') == 'prior_qualified_names')
    paragraph = doc.paragraphs[binding['location']['paragraph']]
    label, _, value = paragraph.text.partition(TEXT_SEPARATOR)
    names = value.split(', ')
    paragraph.runs[0].text = label + TEXT_SEPARATOR + ', '.join(reversed(names))
    doc.save(word)
    assert grade(submission, oracle)['pass']
    paragraph.runs[0].text = label + TEXT_SEPARATOR + ', '.join([names[0], names[0]])
    doc.save(word)
    assert not grade(submission, oracle)['pass']
