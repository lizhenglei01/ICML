"""Build a Word-only presentation pair from the frozen procurement experiment."""
from __future__ import annotations

import argparse
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory

from docx import Document

from multifile_reconstruction.revision import snapshot
from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_office import (
    TEXT_SEPARATOR, grade, native_controls, read_observations, structural_key,
)
from research_archive import digest, read, write


VARIANTS = ('steps', 'definitions')
WORD = 'policy.docx'
EXPECTED_OPERATORS = (
    'GROUP_BY', 'GROUP_AVG', 'JOIN', 'MUL', 'GROUP_BY', 'GROUP_SUM', 'GROUP_SUM',
    'LOOKUP', 'DIV', 'LOOKUP', 'GROUP_BY', 'GROUP_AVG', 'RANK', 'TOP_K',
    'PROJECT_SET', 'FILTER_IN', 'FILTER_NUM', 'PROJECT_SET', 'TOTAL', 'COUNT',
)

# For this fixed case, each definition is reviewed against the original steps.
DEFINITIONS = (
    ('资格条件', (16, 17), '参评供应商的主营采购品类须位列品类排名前两位，且该供应商的质量验收得分须达到85分。两项条件都满足才通过复核。'),
    ('品类质量与排名', (10, 11, 12, 13, 14, 15), '品类按供应商名录中的主营采购品类划分。品类质量得分为该品类内各参评供应商质量验收得分的算术平均值，每家权重相等。品类按此得分从高到低排名，同分按品类键的 Unicode 升序排列，前两位取两项，同分不额外扩充名额。'),
    ('供应商质量评分', (3, 4, 5, 6, 7, 8, 9), '供应商的参评订单是由该供应商承接、且有验收批次记录的采购订单。供应商质量验收得分是其各笔参评订单质量得分按订单含税金额加权的平均值。没有参评订单的供应商不参与评定。'),
    ('订单质量评分', (1, 2), '一笔采购订单的质量得分是归属于这笔订单的所有到货验收批次质量得分的算术平均值，每批权重相等。'),
    ('发布结果口径', (18, 19, 20), '通过名单按供应商编号去重，通过家数是名单中的不同供应商数。发布的参评订单含税金额合计只包括通过供应商的参评订单，每笔计一次。'),
)


def replace_policy(path, indices):
    document = Document(path)
    paragraphs = document.paragraphs
    lines = [label + TEXT_SEPARATOR + text for label, _, text in DEFINITIONS]
    for index, line in zip(indices, lines):
        paragraph = paragraphs[index]
        if len(paragraph.runs) != 1:
            raise ValueError('Unexpected frozen Word policy formatting')
        paragraph.runs[0].text = line
    for index in reversed(indices[len(lines):]):
        node = paragraphs[index]._p
        node.getparent().remove(node)
    document.save(path)


def revise_word(package, oracle):
    public, private = package / 'public', package / 'private'
    reference = private / 'reference'
    step_bindings = [b for b in oracle['bindings'] if b['node_id'].startswith('policy.step.')]
    indices = [b['location']['paragraph'] for b in step_bindings]
    if len(indices) != len(EXPECTED_OPERATORS) or indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError('Unexpected frozen policy paragraph locations')
    for directory in (public, reference):
        replace_policy(directory / WORD, indices)

    offset = len(indices) - len(DEFINITIONS)
    removed = {b['evidence_id'] for b in step_bindings}
    oracle['bindings'] = [b for b in oracle['bindings'] if b['evidence_id'] not in removed]
    oracle['evidence_checks'] = [c for c in oracle['evidence_checks'] if c['evidence_id'] not in removed]
    for binding in oracle['bindings']:
        if binding['file'] == WORD and binding['location']['paragraph'] > indices[-1]:
            binding['location']['paragraph'] -= offset
    for i, (label, _, text) in enumerate(DEFINITIONS):
        node = f'policy.definition.{i + 1}'
        key = f'evidence:word:{node}:paragraph'
        oracle['bindings'].append({'node_id': node, 'role': 'policy', 'label': label, 'file': WORD,
            'evidence_id': key, 'location': {'kind': 'paragraph', 'paragraph': indices[i]}, 'value_type': 'text'})
        oracle['evidence_checks'].append({'node_id': node, 'evidence_id': key, 'before': text, 'after': text,
                                           'group': 'protected'})

    def word_snapshot(path):
        return {key: value for key, value in snapshot(path, tuple(oracle['files'])).items()
                if key.startswith(WORD + ':')}

    before, after = (word_snapshot(path) for path in (public, reference))
    required_ids = {c['evidence_id'] for c in oracle['evidence_checks'] if c['group'] == 'required'}
    required_word = {key for b in oracle['bindings'] if b['file'] == WORD and b['evidence_id'] in required_ids
                     for key in structural_key(public, b)}
    actual_word = {key for key in before if before[key] != after.get(key)}
    if before.keys() != after.keys() or actual_word != required_word:
        raise ValueError('Word reference must change only the original business outputs')
    oracle['checks'] = [c for c in oracle['checks'] if not c['object'].startswith(WORD + ':')]
    oracle['checks'].extend({'object': key, 'group': 'required' if key in required_word else 'protected',
                             'expected': after[key]} for key in sorted(after))
    return oracle


def verify_pair(steps, definitions):
    same = ('public/instruction.md', 'public/review.xlsx', 'public/briefing.pptx',
            'private/reference/review.xlsx', 'private/reference/briefing.pptx',
            'private/graph.json', 'private/truth.json', 'private/visibility.json')
    for relative in same:
        if digest(steps / relative) != digest(definitions / relative):
            raise ValueError(f'Word-only pair differs at {relative}')
    for relative in ('public/policy.docx', 'private/reference/policy.docx'):
        if digest(steps / relative) == digest(definitions / relative):
            raise ValueError(f'Both variants have the same {relative}')
    for directory in ('public', 'private/reference'):
        observed = []
        for case in (steps, definitions):
            oracle = read(case / 'private/oracle.json')
            observed.append({b['evidence_id']: b['value'] for b in read_observations(case / directory, oracle['bindings'])
                             if not b['node_id'].startswith(('policy.step.', 'policy.definition.'))})
        if observed[0] != observed[1]:
            raise ValueError(f'A business input or result changed for {directory}')
    return {relative: digest(steps / relative) for relative in same}


def build(source, out):
    report = read(source / 'summary.json')
    original = source / 'case'
    graph = read(original / 'private/graph.json')
    if (report.get('builder') != 'unified' or not report['passed'] or report['level'] != 4
            or tuple(node['op'] for node in graph['productions']) != EXPECTED_OPERATORS
            or sorted(i for _, coverage, _ in DEFINITIONS for i in coverage) != list(range(1, 21))):
        raise ValueError('Expected the frozen unified procurement case and its reviewed policy coverage')
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError(out)
    with TemporaryDirectory(dir=out.parent, prefix='.policy-pair-') as temp:
        draft = Path(temp) / 'pair'
        steps, definitions = (draft / 'case' / name for name in VARIANTS)
        for case in (steps, definitions):
            copytree(original, case)
        private = definitions / 'private'
        oracle = revise_word(definitions, read(private / 'oracle.json'))
        write(private / 'oracle.json', oracle)
        write(private / 'evidence_map.json', oracle['bindings'])
        manifest = read(private / 'native_manifest.json')
        manifest['public_sha256'][WORD] = digest(definitions / 'public' / WORD)
        manifest['reference_sha256'][WORD] = digest(private / 'reference' / WORD)
        write(private / 'native_manifest.json', manifest)
        write(private / 'native_reference_evaluation.json', grade(private / 'reference', oracle))
        verified = verify_pair(steps, definitions)
        variants = {}
        for variant, case in zip(VARIANTS, (steps, definitions), strict=True):
            controls = native_controls(case, read(case / 'private/oracle.json'))
            variants[variant] = {key: report[key] for key in ('minimum_depth', 'minimum_files')}
            variants[variant].update(reference_grade=controls['reference']['groups'],
                                     controls={name: result['pass'] for name, result in controls.items()})
        write(draft / 'input.json', {'topic': read(source / 'input.json')['topic']})
        write(draft / 'summary.json', {'passed': True, 'builder': 'policy_presentation_pair',
              'variants': variants, 'shared_sha256': verified, 'source': source.name,
              'definition_review': {label: list(coverage) for label, coverage, _ in DEFINITIONS}})
        draft.rename(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.out)
