"""Project the same indirect evidence rule onto medical and sports Office assets."""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from .build import save
from .evidence_control import table
from .evidence_domain_cases import domain_cases
from .evidence_transform import DifferenceEvidence, MinimumCondition

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from worldloom.models import ArtifactIR, ArtifactSection, Chart, ChartKind
from worldloom.presentation import READER
from worldloom.render import docx, pptx, xlsx

DEFAULT = Path(__file__).resolve().parent / 'artifacts' / 'evidence_transfer'
FILES = ('exclusions.xlsx', 'review_policy.docx', 'source_summary.pptx')
MARKER = '达到复核门槛 / meets the review threshold'


def materialize(public, case, rule, condition, state, event):
    public.mkdir(parents=True, exist_ok=True)
    categories = state[rule.result]
    operands = rule.operands(state)
    excluded = operands['excel'][rule.excluded]
    total = operands['ppt'][rule.total]
    policy = ' '.join((
        rule.describe('按同批记录、同一组别核对', 'For the same batch and group'),
        condition.describe(rule.labels[rule.result]),
    ))
    metadata = {'worldloom_created': '2026-01-01T00:00:00+00:00'}

    def ir(title, identifier, sections):
        return ArtifactIR(id=identifier, intent_id='indirect-evidence-review',
                          title=title, metadata=metadata, sections=sections)

    exclusion_table = table('exclusions', rule.labels[rule.excluded],
                            [(key, excluded[key]) for key in categories])
    workbook = ir('排除项台账 / Exclusion ledger', 'exclusions', [
        ArtifactSection(heading=rule.labels[rule.excluded], table=exclusion_table)])
    (public / FILES[0]).write_bytes(xlsx.render(workbook))

    note = ir('复核口径 / Review policy', 'review-policy', [
        ArtifactSection(heading=case['title'], body=policy)])
    (public / FILES[1]).write_bytes(docx.render(note, presentation=READER))

    total_table = table('source-totals', rule.labels[rule.total],
                        [(key, total[key]) for key in categories], rule.labels[rule.total])
    chart = Chart(key='source-totals', title=rule.labels[rule.total], kind=ChartKind.BAR,
                  table='source-totals', series=['value'], value_axis=rule.labels[rule.total])
    deck = ir(case['title'], 'source-summary', [
        ArtifactSection(heading=rule.labels[rule.total], table=total_table, charts=[chart])])
    (public / FILES[2]).write_bytes(pptx.render(deck, profile=READER, artifact_type='source_summary'))
    event_notice = event or '这是初始发布批次。 This is the initial reporting batch.'
    (public / 'instruction.md').write_text(
        f'# {case["title"]}\n\n{event_notice}\n\n'
        '三份材料是当前批次。依据材料中的复核口径，报告每个组的计算结果和门槛标记。\n\n'
        'Use the three current-batch files to report the computed count and review flag for each group.\n',
        encoding='utf-8')
    return policy


def native_readback(public, groups, rule, policy):
    workbook = load_workbook(public / FILES[0], data_only=True)
    excluded, locations = {}, []
    for sheet in workbook:
        for label, cell in sheet.iter_rows(min_col=1, max_col=2):
            if label.value in groups:
                if label.value in excluded or type(cell.value) is not int:
                    raise ValueError(f'Invalid exclusion row {sheet.title}!{cell.coordinate}')
                excluded[label.value] = cell.value
                locations.append({'node_id': rule.excluded, 'entity': label.value,
                                  'file': FILES[0], 'sheet': sheet.title, 'cell': cell.coordinate})
    workbook.close()

    doc = Document(public / FILES[1])
    policy_indexes = [index for index, paragraph in enumerate(doc.paragraphs) if paragraph.text.strip() == policy]
    if len(policy_indexes) != 1:
        raise ValueError('The published scope or comparison rule did not round-trip')
    locations.append({'node_id': 'policy', 'file': FILES[1], 'paragraph': policy_indexes[0]})

    table_values = {}
    plot_values = {}
    deck = Presentation(public / FILES[2])
    for slide_index, slide in enumerate(deck.slides):
        for shape_index, shape in enumerate(slide.shapes):
            if shape.has_table:
                for row, cells in enumerate(shape.table.rows):
                    name = cells.cells[0].text
                    if name in groups:
                        if name in table_values:
                            raise ValueError(f'Duplicate chart-source table row for {name}')
                        table_values[name] = int(cells.cells[1].text)
                        locations.append({'node_id': rule.total, 'entity': name, 'file': FILES[2],
                                          'slide': slide_index, 'shape': shape_index,
                                          'row': row, 'column': 1, 'kind': 'table'})
            if shape.has_chart:
                native = shape.chart
                series, categories = native.series[0], native.plots[0].categories
                embedded = load_workbook(BytesIO(native.part.chart_workbook.xlsx_part.blob), data_only=True)
                for index, category in enumerate(categories):
                    name, number = category.label, series.values[index]
                    sheet = embedded.active
                    row = index + 2
                    if (name not in groups or name in plot_values or int(number) != number
                            or sheet[f'A{row}'].value != name or sheet[f'B{row}'].value != number):
                        raise ValueError(f'Chart cache and embedded workbook differ for {name}')
                    plot_values[name] = int(number)
                    locations.append({'node_id': rule.total, 'entity': name, 'file': FILES[2],
                                      'slide': slide_index, 'shape': shape_index,
                                      'series': 0, 'point': index, 'kind': 'chart_cache'})
                    locations.append({'node_id': rule.total, 'entity': name, 'file': FILES[2],
                                      'slide': slide_index, 'shape': shape_index,
                                      'embedded_part': str(native.part.chart_workbook.xlsx_part.partname),
                                      'sheet': sheet.title, 'cell': f'B{row}', 'kind': 'chart_workbook'})
                embedded.close()
    if excluded.keys() != groups.keys() or plot_values.keys() != groups.keys() or table_values != plot_values:
        raise ValueError('The actual Office evidence is incomplete or inconsistent')
    return {rule.total: plot_values, rule.excluded: excluded}, locations


def validate_necessity(rule, condition, operands, net):
    # These alternatives are legal under the public partition/count contract.
    total, excluded = operands[rule.total], operands[rule.excluded]
    entity = next(key for key in total if total[key] > 0)
    different_excluded = {**excluded, entity: excluded[entity] - 1 if excluded[entity] else 1}
    different_total = {**total, entity: total[entity] + 1}
    alternate_count = {
        FILES[0]: rule.recover({**operands, rule.excluded: different_excluded})[entity],
        FILES[2]: rule.recover({**operands, rule.total: different_total})[entity],
    }
    if any(value == net[entity] for value in alternate_count.values()):
        raise ValueError('Removing a source did not make the requested count underdetermined')
    alternative_condition = MinimumCondition(condition.label, max(net.values()) + 1)
    if condition.flags(net) == alternative_condition.flags(net):
        raise ValueError('Removing the policy did not remove information about the review flags')
    return {FILES[0]: {'same_total_for': entity, 'observed_net': net[entity],
                       'alternative_net': alternate_count[FILES[0]]},
            FILES[2]: {'same_exclusion_for': entity, 'observed_net': net[entity],
                       'alternative_net': alternate_count[FILES[2]]},
            FILES[1]: {'same_counts': True, 'alternative_minimum': alternative_condition.minimum}}


def build(root=DEFAULT):
    root = Path(root)
    summaries = {}
    condition = MinimumCondition(MARKER, 4)
    for domain, case in domain_cases().items():
        rule = DifferenceEvidence('gross', 'excluded', 'net', case['labels'])
        stages = []
        for index, state in enumerate(case['states']):
            package = root / domain / f'state_{index}'
            public = package / 'public'
            event = case['events'][index - 1] if index else None
            policy = materialize(public, case, rule, condition, state, event)
            raw, locations = native_readback(public, state['net'], rule, policy)
            net = rule.recover(raw)
            if net != state['net'] or any(raw[key] != state[key] for key in raw):
                raise ValueError(f'{domain} state {index} failed independent source-table reconciliation')
            flags = condition.flags(net)
            prior = case['states'][index - 1]['net'] if index else net
            changed = [key for key in net if prior[key] != net[key]]
            necessity = validate_necessity(rule, condition, raw, net)
            save(package / 'private/evidence_map.json', locations)
            save(package / 'private/truth.json', {'net': net, 'flags': flags, 'changed_groups': changed,
                                                  'preserved_groups': [key for key in net if key not in changed]})
            stage = {'state': index, 'event': event, 'native_readback_passed': True,
                     'groups': len(net), 'declared_visible_metrics': sorted(raw),
                     'changed_groups': changed, 'full_task_needs_files': list(necessity),
                     'missing_file_counterexamples': necessity}
            save(package / 'private/verification.json', stage)
            stages.append(stage)
        save(root / domain / 'private/provenance.json', case['provenance'])
        summaries[domain] = stages
    report = {'passed': True, 'states': summaries,
              'condition_minimum': condition.minimum, 'agent_calls': 0,
              'scope': 'Exact subtraction and threshold comparison on public counts. Source tables and truths are private.'}
    save(root / 'summary.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = build(args.output)
    print(f'Validated Office readback for {sum(map(len, result["states"].values()))} states at {args.output}')


if __name__ == '__main__':
    main()

