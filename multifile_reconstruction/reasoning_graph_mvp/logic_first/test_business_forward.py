"""Exercise the boundary between the forward frontier and private definitions."""

import json

from . import business_forward


def proposal(identifier, concept, zh, en, unit, op, a, b):
    return {
        'id': identifier, 'concept': concept, 'kind': 'derived',
        'zh': zh, 'en': en, 'meaning': 'Same entity and quarter', 'unit': unit,
        'formula': {'op': op, 'args': [{'ref': a}, {'ref': b}]},
    }


def test_forward_prompt_starts_with_base_quantities_and_no_target():
    spec = business_forward.hotel_input()
    request = business_forward.forward_request(spec, [], [])
    payload = json.loads(request['messages'][1]['content'])
    assert [m['id'] for m in payload['available_quantities']] == [m['id'] for m in spec['base_metrics']]
    assert payload['already_verified_productions'] == []
    assert not any(name in repr(request).lower() for name in ('revpar', 'occupancy', 'average_daily_rate'))


def test_only_verified_quantities_progress_to_next_round(tmp_path, monkeypatch):
    spec = business_forward.hotel_input()
    price_unit = {'CNY': 1, 'room_night': -1}
    occ = proposal('occupancy', 'occupancy_rate', '入住率', 'Occupancy', {}, 'DIV', 'sold_room_nights', 'available_room_nights')
    adr = proposal('adr', 'average_daily_rate', '平均房价', 'Average daily rate', price_unit, 'DIV', 'room_revenue', 'sold_room_nights')
    direct = proposal('revenue_per_avail', 'revpar', '每间可售客房收入', 'RevPAR', price_unit, 'DIV', 'room_revenue', 'available_room_nights')
    compound = proposal('other_revpar_id', 'revenue_per_available_room', '每间可售客房收入', 'RevPAR', price_unit, 'MUL', 'adr', 'occupancy')
    invalid = proposal('made_up', 'arbitrary_index', '自造指标', 'Arbitrary index', {}, 'DIV', 'sold_room_nights', 'available_room_nights')
    wrong = proposal('reverse', 'occupancy_rate', '入住率', 'Occupancy', {}, 'DIV', 'available_room_nights', 'sold_room_nights')
    candidates = [[occ, adr, direct, compound, invalid, wrong], [compound]]
    calls = []

    def mock_call(request, folder, effort):
        payload = json.loads(request['messages'][1]['content'])
        calls.append(payload)
        return {'candidates': candidates[len(calls) - 1]}, {'reported_model': 'offline fixture'}

    monkeypatch.setattr(business_forward, 'call_business', mock_call)
    result = business_forward.explore(spec, tmp_path)
    assert result['passed']
    initial = result['rounds'][0]['candidates']
    assert initial[3]['verification']['error'] == 'INPUT_NOT_AVAILABLE_AT_START_OF_ROUND'
    assert initial[4]['verification']['status'] == 'DRAFT'
    assert initial[5]['verification']['error'] == 'BUSINESS_FORMULA_MISMATCH'
    assert {m['id'] for m in calls[1]['available_quantities']} == {
        'room_revenue', 'available_room_nights', 'sold_room_nights', 'occupancy', 'adr', 'revenue_per_avail',
    }
    routes = [p for p in result['productions'] if p['output'] == 'revenue_per_avail']
    assert [p['formula']['op'] for p in routes] == ['DIV', 'MUL']
    assert routes[-1]['round'] == 1
    assert result['verification']['trial_count'] == 36
