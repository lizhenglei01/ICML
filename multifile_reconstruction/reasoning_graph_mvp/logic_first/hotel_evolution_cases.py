"""Independent formula behavior cases for the shared hotel approval interface."""
from fractions import Fraction

from .hotel_revenue_milestones import ANALYSIS, WILLOW, formula_spec as ledger_formula_spec


APPROVAL = '审批办理'
NEW_SIGNING = '新签'
RENEWAL = '续约'
RENEWAL_RATE_FLOOR = 120


def _point(location, value, *, milestone=None):
    sheet, cell = location
    point = {'sheet': sheet, 'cell': cell, 'value': value}
    if isinstance(value, str):
        point['value_type'] = 'text'
    if milestone is not None:
        point['milestone'] = milestone
    return point


def formula_spec(graph, state, sources, stage, *, approved=()):
    """Return native Excel probe manifests and each monitored formula's stage.

    The interface uses B3 and B6 as caller inputs, B4/B5 as metric lookups, and
    B8 as the decision. The fixed archive pairs D3:D8 with E3:E8, keyed by
    hotel name. Expected numbers come from independent exact fractions.
    """
    scenarios, metric_origins = ledger_formula_spec(graph, state, sources, 1)
    for scenario in scenarios:
        scenario['milestone'] = 1
    origins = {f'{ANALYSIS}!{cell}': origin for cell, origin in metric_origins.items()}
    if stage == 1:
        return scenarios, origins
    entities = graph['entities']
    approved = frozenset(approved)
    sold, capacity, receipts, refunds = (state[key] for key in (
        'sold_room_nights', 'available_room_nights', 'booked_room_receipts', 'room_refunds',
    ))
    metrics = {hotel: (Fraction(sold[hotel], capacity[hotel]),
                       Fraction(receipts[hotel] - refunds[hotel], sold[hotel]))
               for hotel in entities}
    occupancy_floor = Fraction(state['occupancy_floor'])
    peers = [adr for occ, adr in metrics.values() if occ < occupancy_floor]
    rate_hurdle = sum(peers) / len(peers) + state['adr_premium']
    if (WILLOW not in metrics or not approved <= metrics.keys()
            or rate_hurdle != state['review_adr_floor']):
        raise ValueError('Approval case fixtures disagree with the independent current hotel data')

    hotel_cell, mode_cell = (APPROVAL, 'B3'), (APPROVAL, 'B6')
    occupancy_cell, adr_cell, decision_cell = ((APPROVAL, col) for col in ('B4', 'B5', 'B8'))
    origins.update({f'{APPROVAL}!B4': 2, f'{APPROVAL}!B5': 2, f'{APPROVAL}!B8': stage})

    def eligible(occ, adr, floor):
        return occ >= occupancy_floor and adr >= floor

    def add(name, changes, points, milestone=2):
        scenarios.append({'name': name, 'milestone': milestone,
                          'inputs': [_point(where, value) for where, value in changes],
                          'expected': [_point(where, value, milestone=2 if where != decision_cell else milestone)
                                       for where, value in points]})

    def outcomes(hotel, mode=NEW_SIGNING):
        occ, adr = metrics[hotel]
        allowed = (eligible(occ, adr, rate_hurdle) if mode == NEW_SIGNING
                   else eligible(occ, adr, RENEWAL_RATE_FLOOR) and hotel in approved)
        return [(occupancy_cell, occ), (adr_cell, adr), (decision_cell, int(allowed))]

    scenarios[0]['expected'].extend(_point(where, value, milestone=2) for where, value in outcomes(WILLOW))
    for index, hotel in enumerate(entities, 2):
        if hotel != WILLOW:
            add(f'new_signing_hotel_{index}', [(hotel_cell, hotel)], outcomes(hotel))

    row = 2 + entities.index(WILLOW)
    good_occ = (occupancy_floor + 1) / 2
    good_adr = rate_hurdle + 20
    add('lookup_consumes_published_metrics',
        [((ANALYSIS, f'C{row}'), good_occ), ((ANALYSIS, f'D{row}'), good_adr)],
        [(occupancy_cell, good_occ), (adr_cell, good_adr), (decision_cell, 1)])
    passing = next(hotel for hotel in entities if eligible(*metrics[hotel], rate_hurdle))
    passing_row = 2 + entities.index(passing)
    passing_occupancy, passing_adr = (ANALYSIS, f'C{passing_row}'), (ANALYSIS, f'D{passing_row}')
    for name, occ, adr in (
        ('caller_occupancy_fails', 0, good_adr),
        ('caller_adr_fails', good_occ, 0),
    ):
        add(name, [(hotel_cell, passing), (passing_occupancy, occ), (passing_adr, adr)], [(decision_cell, 0)])
    for name, location, blocked in (
        ('caller_occupancy_floor_changes', (APPROVAL, 'H2'), 1),
        ('caller_rate_hurdle_changes', (APPROVAL, 'H3'), good_adr + 10),
    ):
        add(name, [(hotel_cell, passing), (passing_occupancy, good_occ), (passing_adr, good_adr), (location, blocked)],
            [(decision_cell, 0)])

    if stage >= 3:
        renewal_candidates = {hotel for hotel in entities if eligible(*metrics[hotel], RENEWAL_RATE_FLOOR)}
        unapproved = next(hotel for hotel in entities if hotel in renewal_candidates - approved)
        variant = next(hotel for hotel in entities if hotel not in renewal_candidates)
        renewals = approved | {unapproved, variant}
        if WILLOW not in approved & renewal_candidates or metrics[WILLOW][1] != RENEWAL_RATE_FLOOR:
            raise ValueError('The renewal test needs a signed Willowbank at the inclusive renewal boundary')
        for index, hotel in enumerate(entities, 2):
            if hotel in renewals:
                add(f'renewal_hotel_{index}', [(hotel_cell, hotel), (mode_cell, RENEWAL)],
                    outcomes(hotel, RENEWAL), milestone=3)
        switch = [(mode_cell, RENEWAL)]
        add('renewal_rate_hurdle_changes', [*switch, ((APPROVAL, 'H4'), RENEWAL_RATE_FLOOR + 1)],
            [(decision_cell, 0)], milestone=3)
        # Override caller data, never the computed display cells B4/B5.
        higher_occ_floor = (metrics[WILLOW][0] + 1) / 2
        add('renewal_shared_occupancy_changes',
            [*switch, ((APPROVAL, 'H2'), higher_occ_floor)],
            [(decision_cell, 0)], milestone=3)
        willow_archive = 3 + entities.index(WILLOW)
        alternate_archive = 3 + entities.index(unapproved)
        add('renewal_reads_frozen_signing_flag', [*switch, ((APPROVAL, f'E{willow_archive}'), 0)],
            [(decision_cell, 0)], milestone=3)
        add('renewal_looks_up_frozen_hotel_name',
            [*switch, ((APPROVAL, f'D{willow_archive}'), unapproved), ((APPROVAL, f'D{alternate_archive}'), WILLOW)],
            [(decision_cell, 0)], milestone=3)
    return scenarios, origins
