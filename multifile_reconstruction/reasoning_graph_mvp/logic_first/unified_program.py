"""Compile a model-authored object schema and a mixed arithmetic/relational graph."""
from __future__ import annotations

from copy import deepcopy
from graphlib import CycleError, TopologicalSorter
import random
import re

from .program_graph import brief, evaluate
from .program_operators import ARITHMETIC, Column, compile_operator, require, table
from .scenario_graph import fields, identifier, label, units


def ordered(dependencies, message):
    try:
        return tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as exc:
        raise ValueError(message) from exc


def names(values):
    require(isinstance(values, list), 'Expected a list of fictional names')
    result = [brief(value) for value in values]
    require(len(result) == len(set(result)) and all(not x.startswith(('=', '+', '-', '@')) for x in result),
            'Value labels must be distinct, safe text')
    return result


def compile_objects(raw):
    fields(raw, 'title', 'period', 'decision_object', 'objects')
    require(isinstance(raw['period'], str) and re.fullmatch(r'20\d{2}-Q[1-4]', raw['period']), 'Invalid reporting period')
    require(isinstance(raw['objects'], list) and 1 <= len(raw['objects']) <= 4, 'Expected one to four object types')
    objects, schemas, links = {}, {}, {}
    for item in deepcopy(raw['objects']):
        fields(item, 'id', 'label', 'rows', 'key', 'fields')
        key = identifier(item['id'])
        require(key not in objects and key != 'policy_gate', 'Duplicate or reserved object ID')
        require(type(item['rows']) is int and 3 <= item['rows'] <= 14, 'Each object needs 3 to 14 rows')
        item['label'] = brief(item['label'])
        primary = item['key']
        fields(primary, 'id', 'label', 'examples')
        primary['id'], primary['label'], primary['examples'] = identifier(primary['id']), brief(primary['label']), names(primary['examples'])
        if primary['examples']:
            require(len(primary['examples']) == item['rows'], 'Key examples must cover the proposed rows')
        objects[key] = item
        links[key] = set()
    require(raw['decision_object'] in objects, 'Unknown decision object')
    require(sum(obj['rows'] for obj in objects.values()) <= 40, 'Total source rows exceed the pilot limit')
    require(len({obj['label'] for obj in objects.values()}) == len(objects), 'Object labels must be distinct')
    for key, item in objects.items():
        primary = item['key']['id']
        require(primary != 'rank', 'Reserved column name rank')
        cols = {primary: Column('key:' + key)}
        require(isinstance(item['fields'], list) and 1 <= len(item['fields']) <= 5, 'Each object needs one to five data fields')
        for spec in item['fields']:
            kind = spec['kind']
            col = identifier(spec['id'])
            require(col not in cols and col != 'rank', 'Duplicate or reserved column name')
            spec['label'] = brief(spec['label'])
            common = ('id', 'label', 'kind')
            if kind == 'number':
                fields(spec, *common, 'min', 'max', 'step', 'unit')
                lo, hi, step = (spec[name] for name in ('min', 'max', 'step'))
                require(all(type(value) is int for value in (lo, hi, step)) and -10**8 <= lo < hi <= 10**8
                        and 0 < step <= hi - lo, f'Invalid sampling interval for {col}')
                spec['unit'] = units(spec['unit'])
                cols[col] = Column('number', tuple(sorted(spec['unit'].items())))
            elif kind == 'reference':
                fields(spec, *common, 'target')
                target = spec['target']
                require(target in objects, f'Unknown reference target {target}')
                links[key].add(target)
                cols[col] = Column('key:' + target)
            elif kind == 'category':
                fields(spec, *common, 'values')
                spec['values'] = names(spec['values'])
                require(1 <= len(spec['values']) <= 8, f'Invalid category domain for {col}')
                cols[col] = Column(f'category:{key}.{col}')
            else:
                raise ValueError(f'Unknown column type {kind}')
        schemas[key] = table(cols, [primary])
    order = ordered(links, 'Circular foreign-key declarations')
    # Store the authored objects in foreign-key order for deterministic sampling.
    return {'title': brief(raw['title']), 'period': raw['period'], 'decision_object': raw['decision_object'],
            'objects': {key: objects[key] for key in order}}, schemas


def compile_nodes(base, input_schemas, candidates):
    require(isinstance(candidates, list) and 1 <= len(candidates) <= 36, 'Expected one to 36 graph operations')
    nodes, specs = {}, dict(input_schemas)
    titles = {key: obj['label'] for key, obj in base['objects'].items()}
    for item in candidates:
        fields(item, 'id', 'label', 'op', 'inputs', 'params')
        key = identifier(item['id'])
        require(key not in nodes and key not in specs and key != 'policy_gate', 'Duplicate or reserved operation identifier')
        inputs = item['inputs']
        require(isinstance(inputs, list) and all(isinstance(name, str) for name in inputs)
                and len(set(inputs)) == len(inputs), 'Inputs must be distinct, known node names')
        titles[key] = label(item['label'])
        nodes[key] = item
    require(len(set(titles.values())) == len(titles), 'Business labels must be distinct across sources and operations')
    require(all(set(item['inputs']) <= specs.keys() | nodes.keys() for item in nodes.values()), 'Unknown referenced node')
    order = ordered({key: item['inputs'] for key, item in nodes.items()}, 'Circular program dependency')
    productions, parents = [], {key: set() for key in specs}
    for key in order:
        if key not in nodes:
            continue
        item = nodes[key]
        inputs, op = item['inputs'], item['op']
        if op in {'JOIN', 'LOOKUP'}:
            require(isinstance(item['params'].get('on'), list), 'Declare [left field, right field] for a link')
        specs[key], params = compile_operator(op, [specs[name] for name in inputs], item['params'])
        parents[key] = set(inputs).union(*(parents[name] for name in inputs))
        productions.append({'id': key, 'output': key, 'op': op, 'inputs': inputs, 'requires': ['policy_gate'],
                            'program_parameters': params, 'label': titles[key]})
    return specs, productions, parents


def partial(base, schemas, proposal):
    specs, rules, parents = compile_nodes(base, schemas, proposal['nodes'])
    target = proposal['target']
    require(target in specs and target not in schemas and specs[target].kind == 'table'
            and any(c.kind == 'number' for c in specs[target].columns.values()), 'Expected a derived numeric table target')
    require({rule['output'] for rule in rules} <= parents[target] | {target}, 'Forward or backward operations must contribute to the target')
    return specs[target], {'productions': rules, 'entities': []}


def schema_description(schema):
    return {'keys': list(schema.keys), 'columns': {key: {'type': field.kind, 'unit': dict(field.unit) if field.unit else {}}
                                                    for key, field in schema.columns.items()}}


def signature(graph, target):
    rules = {rule['output']: rule for rule in graph['productions']}

    def visit(key):
        if key not in rules:
            return ('source', key)
        rule = rules[key]
        return (rule['op'], rule['program_parameters'], [visit(parent) for parent in rule['inputs']])

    return visit(target)


def verify_backward(base, schemas, forward, backward, seed):
    expected, a = partial(base, schemas, forward)
    actual, b = partial(base, schemas, backward)
    require(actual == expected, 'Backward output columns, units or keys differ from forward target')
    exact = signature(a, forward['target']) == signature(b, backward['target'])
    rng = random.Random(seed)
    count = 0
    for i in range(20):
        inputs = sample(base, rng, clustered=i % 2 == 0)
        try:
            x = evaluate(a, inputs)[forward['target']]
            y = evaluate(b, inputs)[backward['target']]
        except (ValueError, ZeroDivisionError):
            continue
        def by_key(rows):
            return sorted(rows, key=lambda row: tuple(row[k] for k in expected.keys))
        require(by_key(x) == by_key(y), 'Backward path disagrees with forward result on sampled evidence')
        count += 1
    require(count >= 8, 'Too few valid worlds for backward-path validation')
    return {'match': 'same_operator_structure' if exact else 'sampled_agreement_only',
            'worlds_checked': count, 'used_for_execution': False}


def finalize(base, schemas, forward, policy, level):
    fields(policy, 'nodes', 'roster', 'reports')
    specs, rules, ancestors = compile_nodes(base, schemas, forward['nodes'] + policy['nodes'])
    roster, reports = policy['roster'], policy['reports']
    decision = base['decision_object']
    require(roster in specs and specs[roster].kind == 'set' and specs[roster].field == Column('key:' + decision),
            'Review roster must contain decision object identifiers')
    require(isinstance(reports, list) and 1 <= len(reports) <= 2 and len(set(reports)) == len(reports)
            and all(key in specs and specs[key].kind == 'number' for key in reports), 'Expected one or two scalar reports')
    targets = [roster, *reports]
    used = set(targets).union(*(ancestors[key] for key in targets))
    require(used == specs.keys() and forward['target'] in ancestors[roster], 'Each source and operation must contribute to the actual review')
    ops = {rule['output']: rule['op'] for rule in rules}
    roster_path = ancestors[roster] | {roster}
    linked = {key for key in roster_path if ops.get(key) in {'JOIN', 'LOOKUP'} and len(ancestors[key] & schemas.keys()) >= 2}
    grouped = {key for key in roster_path if ops.get(key) in {'GROUP_SUM', 'GROUP_AVG'} and linked & ancestors[key]}
    cuts = {key: list(specs[key].keys) for key in roster_path
            if ops.get(key) in {'TOP_K', 'ARGMAX'} and grouped & ancestors[key]}
    if level == 1:
        require(any(ops.get(key) in ARITHMETIC for key in roster_path), 'Level 1 needs a meaningful derived arithmetic measure')
    if level == 2:
        require(any(ops.get(key) in {'AND', 'OR'} for key in roster_path), 'Level 2 needs multiple decision branches')
    if level >= 3:
        require(grouped, 'Review must use linked sources and their grouping on a path to the roster')
    if level == 4:
        require(cuts, 'Review must use a grouped ranking cutoff on a path to the roster')
    labels = {key: {'kind': 'base', 'label': item['label'], 'unit': {}} for key, item in base['objects'].items()}
    for rule in rules:
        key = rule['output']
        unit = specs[key].field.unit if specs[key].kind == 'number' else ()
        labels[key] = {'kind': 'report' if key in targets else 'derived',
                       'label': rule['label'], 'unit': dict(unit) if unit else {}}
    labels['policy_gate'] = {'kind': 'rule', 'label': '合成复核政策', 'unit': {}}
    return {**base, 'level': level, 'entities': key_values(base, decision), 'targets': targets, 'productions': rules,
            'nodes': labels, 'rank_cuts': cuts, 'policy_origin': 'model-authored synthetic policy'}


def key_values(base, key):
    obj = base['objects'][key]
    return obj['key']['examples'] or [f'{key}-{i + 1:02d}' for i in range(obj['rows'])]


def sample(base, rng, *, clustered=False):
    sources = {}
    for key, obj in base['objects'].items():
        size = obj['rows']
        columns, categories = {obj['key']['id']: key_values(base, key)}, []
        for spec in obj['fields']:
            if spec['kind'] == 'number':
                continue
            values = key_values(base, spec['target']) if spec['kind'] == 'reference' else spec['values']
            sequence = (rng.sample(values, len(values)) * (1 + size // len(values)))[:size]
            rng.shuffle(sequence)
            columns[spec['id']] = sequence
            categories.append(sequence)
        for spec in obj['fields']:
            if spec['kind'] != 'number':
                continue
            domain = range(spec['min'], spec['max'] + 1, spec['step'])
            if clustered and categories:
                group = categories[0]
                centers = {value: rng.randrange(len(domain)) for value in sorted(set(group))}
                radius = max(1, len(domain) // 3)
                column = [domain[rng.randrange(max(0, centers[value] - radius), min(len(domain), centers[value] + radius + 1))]
                          for value in group]
            else:
                column = rng.choices(domain, k=size)
            columns[spec['id']] = column
        sources[key] = tuple(dict(zip(columns, values, strict=True)) for values in zip(*columns.values(), strict=True))
    return sources


def ownership_paths(base, source):
    if source == base['decision_object']:
        return [()]
    paths = []
    for field in base['objects'][source]['fields']:
        if field['kind'] == 'reference':
            for rest in ownership_paths(base, field['target']):
                paths.append(((field['id'], field['target']), *rest))
    return paths


def revisions(graph, seed, *, worlds=100):
    rng = random.Random(seed)
    roster = graph['targets'][0]
    paths = {key: ownership_paths(graph, key) for key in graph['objects']}
    candidates = {key: spec[0] for key, spec in paths.items() if len(spec) == 1
                  and any(f['kind'] == 'number' for f in graph['objects'][key]['fields'])}
    require(candidates, 'No numeric source has an unambiguous path to the decision object')
    trials = 0
    for world in range(1, worlds + 1):
        sources = sample(graph, rng, clustered=world % 2 == 0)
        try:
            old = evaluate(graph, sources)
        except (ValueError, ZeroDivisionError):
            continue
        if not 0 < len(old[roster]) < len(graph['entities']):
            continue
        indexes = {key: {row[spec['key']['id']]: row for row in sources[key]} for key, spec in graph['objects'].items()}
        for source, path in candidates.items():
            for index, row in enumerate(sources[source]):
                owner_row = row
                for field, target in path:
                    owner_row = indexes[target][owner_row[field]]
                owner = owner_row[graph['objects'][graph['decision_object']]['key']['id']]
                for field in graph['objects'][source]['fields']:
                    if field['kind'] != 'number':
                        continue
                    key = field['id']
                    domain = range(field['min'], field['max'] + 1, field['step'])
                    values = {domain[0], domain[-1], *rng.sample(domain, min(10, len(domain)))} - {row[key]}
                    for value in sorted(values, key=lambda x: (abs(x - row[key]), x)):
                        trials += 1
                        amended = list(sources[source])
                        amended[index] = {**row, key: value}
                        try:
                            new = evaluate(graph, {**sources, source: tuple(amended)})
                        except (ValueError, ZeroDivisionError):
                            continue
                        gained, lost = new[roster] - old[roster], old[roster] - new[roster]
                        delta = gained | lost
                        external = delta - {owner}
                        if not delta or not 0 < len(new[roster]) < len(graph['entities']):
                            continue
                        if graph['level'] >= 3 and not external:
                            continue
                        cuts = [node for node, keys in graph['rank_cuts'].items()
                                if {tuple(r[k] for k in keys) for r in old[node]} !=
                                {tuple(r[k] for k in keys) for r in new[node]}]
                        if graph['level'] == 4 and (not cuts or not gained or not lost):
                            continue
                        event = {'source': source, 'record_index': index, 'record': row[graph['objects'][source]['key']['id']],
                                 'entity': owner, 'field': key, 'before': row[key], 'after': value}
                        yield old, new, event, {'worlds': world, 'event_trials': trials, 'gains': sorted(gained),
                                               'losses': sorted(lost), 'affected_other_entities': sorted(external),
                                               'changed_rank_cut_nodes': cuts}
    raise ValueError(f'No single-record correction satisfies this review after {trials} trials')
