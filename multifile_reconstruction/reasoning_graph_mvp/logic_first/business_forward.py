"""Explore forward from supplied business quantities and verify each proposal."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .business_author import call_business
from .business_goal import known_revpar
from .business_verify import verify
from .topic_author import parse_json


OPERATORS = ['ADD', 'SUB', 'MUL', 'DIV']
DEFAULT_OUTPUT = Path(__file__).resolve().parent / 'business_artifacts' / 'forward_hotel'
BASE_FIELDS = {'id', 'concept', 'kind', 'zh', 'en', 'meaning', 'unit'}


def hotel_input():
    return {
        'topic_zh': '酒店客房经营分析',
        'topic_en': 'Hotel room operating performance',
        'entity_type_zh': '酒店',
        'entity_type_en': 'hotel',
        'period': '2025-Q4',
        'base_metrics': known_revpar(),
        'operators': OPERATORS,
    }


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def forward_request(spec, validated, productions):
    """The first round exposes only user-supplied quantities and operator semantics."""
    quantities = spec['base_metrics'] + validated
    guidance = [
        'Choose any pair from the available quantities and try one allowed arithmetic operation. Operands must be references, not numeric literals or nested operations.',
        'Propose the output as a recognizable business metric with its precise meaning and dimensional unit. Avoid arbitrary scores, reformatted versions of an input, and algebraic padding.',
        'Use the same entity and reporting period. Write a ratio as a decimal fraction with a dimensionless unit. A unit is a dimension-to-integer-exponent mapping.',
        'Propose up to six candidates. Return valid JSON only. Skip unsupported combinations rather than guessing.',
        'Do not return a planned target, market data, thresholds, or sample answers.',
    ]
    if validated:
        guidance += [
            'Explore combinations that use at least one of your previously verified derived quantities as an operand.',
            'A distinct derivation of an already verified metric is useful if it uses an accepted intermediate. Use its existing identifier and concept.',
        ]
    payload = {
        'topic': spec['topic_zh'], 'period': spec['period'],
        'available_quantities': [{k: v for k, v in metric.items() if k != 'formula'} for metric in quantities],
        'operators': spec['operators'],
        'already_verified_productions': productions,
        'constraints': guidance,
        'output_schema': {'candidates': [{
            'id': 'lowercase metric identifier', 'concept': 'standard business concept',
            'kind': 'derived', 'zh': 'Chinese name', 'en': 'English name',
            'meaning': 'same-period definition', 'unit': {'dimension_name': 1},
            'formula': {'op': 'one allowed operator', 'args': [{'ref': 'first_id'}, {'ref': 'second_id'}]},
        }]},
    }
    return {'messages': [
        {'role': 'system', 'content': 'Explore meaningful metric relationships forward from available business quantities. Return JSON only without tools or file access.'},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]}


def base_profile(spec):
    profile = {k: v for k, v in spec.items() if k not in {'base_metrics', 'operators'}}
    profile['entities'] = [f'Verification entity {i + 1}' for i in range(6)]
    profile['metrics'] = deepcopy(spec['base_metrics'])
    return profile


def inspect_candidate(candidate, profile, available, operators):
    """Check the candidate's interface, then run the existing independent verifier."""
    from .business_definitions import canonical

    required = {'id', 'concept', 'kind', 'zh', 'en', 'meaning', 'unit', 'formula'}
    if not isinstance(candidate, dict) or set(candidate) != required or candidate['kind'] != 'derived':
        return None, {'status': 'REJECTED', 'error': 'CANDIDATE_SCHEMA'}
    if not all(isinstance(candidate[key], str) for key in ('id', 'concept')):
        return None, {'status': 'REJECTED', 'error': 'CANDIDATE_ID'}
    formula = candidate['formula']
    if (not isinstance(formula, dict) or set(formula) != {'op', 'args'}
            or formula['op'] not in operators or not isinstance(formula['args'], list)
            or len(formula['args']) != 2 or any(not isinstance(a, dict) or set(a) != {'ref'}
                                               or not isinstance(a['ref'], str) for a in formula['args'])):
        return None, {'status': 'REJECTED', 'error': 'ONE_OPERATOR_WITH_REFERENCES_REQUIRED'}
    inputs = [a['ref'] for a in formula['args']]
    if not set(inputs) <= available:
        return None, {'status': 'REJECTED', 'error': 'INPUT_NOT_AVAILABLE_AT_START_OF_ROUND'}

    role = canonical(candidate['concept'])
    existing = next((m for m in profile['metrics'] if role and canonical(m['concept']) == role), None)
    if existing and existing['kind'] == 'base':
        return None, {'status': 'REJECTED', 'error': 'REDEFINES_BASE_CONCEPT'}
    normalized = deepcopy(candidate)
    if existing:
        normalized['id'] = existing['id']
    if normalized['id'] in inputs:
        return None, {'status': 'REJECTED', 'error': 'SELF_DEPENDENCY'}
    if any(m['id'] == normalized['id'] and m is not existing for m in profile['metrics']):
        return None, {'status': 'REJECTED', 'error': 'REDEFINES_EXISTING_ID'}

    trial = dict(profile, metrics=[m for m in profile['metrics'] if m is not existing] + [normalized])
    report = verify(trial)
    entry = next((m for m in report['metrics'] if m['id'] == normalized['id']), None)
    if entry is None:
        return None, {'status': 'REJECTED', 'error': 'PROFILE_VALIDATION', 'details': report['errors']}
    # Alternative formulas may affect downstream dependencies, so also check that
    # replacing the production retains the already accepted graph's validity.
    if entry['accepted'] and not all(m['accepted'] for m in report['metrics']):
        return None, {'status': 'REJECTED', 'error': 'INVALID_DEPENDENCY_GRAPH', 'verification': entry}
    return (normalized if entry['accepted'] else None), entry


def explore(spec, directory, *, rounds=2, reuse=False, effort='medium'):
    directory = Path(directory)
    if not 1 <= rounds <= 4:
        raise ValueError('rounds must be in [1, 4]')
    if not spec['operators'] or any(op not in OPERATORS for op in spec['operators']):
        raise ValueError('unsupported operator')
    if not spec['base_metrics'] or any(set(m) != BASE_FIELDS or m['kind'] != 'base' for m in spec['base_metrics']):
        raise ValueError('input must contain base metrics only')
    profile = base_profile(spec)
    base_report = verify(profile)
    if base_report['errors'] or not all(m['accepted'] for m in base_report['metrics']):
        raise ValueError('unverified base metrics')

    save(directory / 'input.json', spec)
    trace, productions = [], []
    for index in range(rounds):
        derived = [m for m in profile['metrics'] if m['kind'] == 'derived']
        available = {m['id'] for m in profile['metrics']}
        request = forward_request(spec, derived, [{k: p[k] for k in ('output', 'formula')} for p in productions])
        folder = directory / f'round_{index:02d}'
        prior = folder / 'request.json'
        if (reuse and prior.exists() and (folder / 'response.txt').exists() and (folder / 'call.json').exists()
                and json.loads(prior.read_text(encoding='utf-8')) == request):
            response = parse_json((folder / 'response.txt').read_text(encoding='utf-8'))
            info = json.loads((folder / 'call.json').read_text(encoding='utf-8'))
        else:
            response, info = call_business(request, folder, effort=effort)
        if set(response) != {'candidates'} or not isinstance(response['candidates'], list) or len(response['candidates']) > 12:
            raise ValueError('INVALID_FORWARD_RESPONSE')

        evaluations = []
        for candidate in response['candidates']:
            metric, record = inspect_candidate(candidate, profile, available, spec['operators'])
            outcome = {'candidate': candidate, 'verification': record}
            if metric:
                formula = metric['formula']
                arguments = [operand['ref'] for operand in formula['args']]
                if formula['op'] in {'ADD', 'MUL'}:
                    arguments.sort()
                key = json.dumps([metric['id'], formula['op'], arguments])
                rule_id = 'forward_' + hashlib.sha256(key.encode()).hexdigest()[:16]
                production = {'id': rule_id, 'output': metric['id'], 'formula': metric['formula'],
                              'origin': 'LLM forward exploration', 'round': index,
                              'source': record['source'], 'canonical_concept': record['canonical_concept']}
                if not any(p['id'] == rule_id for p in productions):
                    productions.append(production)
                    if not any(m['id'] == metric['id'] for m in profile['metrics']):
                        profile['metrics'].append(metric)
                outcome['production_id'] = rule_id
            evaluations.append(outcome)
        step = {'round': index, 'available_at_start': sorted(available), 'call': info, 'candidates': evaluations}
        trace.append(step)
        save(folder / 'verification.json', step)

    final = verify(profile)
    result = {'passed': final['passed'], 'profile': profile,
              'quantities': [{k: v for k, v in metric.items() if k != 'formula'} for metric in profile['metrics']],
              'productions': productions, 'rounds': trace, 'verification': final,
              'mode': 'forward_from_known_quantities',
              'provenance': {'human': 'Input concepts, units, period, and independent reference definitions',
                             'llm': 'Proposed outputs, business concepts and arithmetic productions',
                             'program': 'Forward frontier validation and independent business verification'}}
    save(directory / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--input', type=Path, help='JSON input with base metrics and allowed operators')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--effort', default='medium')
    parser.add_argument('--reuse', action='store_true')
    args = parser.parse_args()
    spec = json.loads(args.input.read_text(encoding='utf-8')) if args.input else hotel_input()
    result = explore(spec, args.out, rounds=args.rounds, reuse=args.reuse, effort=args.effort)
    print(json.dumps({'passed': result['passed'], 'productions': result['productions']}, ensure_ascii=False), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
