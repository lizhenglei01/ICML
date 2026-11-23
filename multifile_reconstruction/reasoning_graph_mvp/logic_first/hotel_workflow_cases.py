"""Hotel workflow input cases and an independent business-rule interpreter."""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction

LEDGER, APPS, POLICY, REFUNDS, REVIEW = 'Ledger', 'Applications', 'Policy', 'Refunds', 'Review'
HOTELS = ('Alder', 'Birch', 'Cedar', 'Dale')
JUNE, JULY, AUGUST = 202606, 202607, 202608
ROWS = range(2, 6)
EVENT_ROWS = range(2, 8)
COLS = (
    ('A', 'In period'), ('B', 'First record'), ('C', 'Sellable nights'), ('D', 'Nights valid'),
    ('E', 'Amount valid'), ('F', 'Recognized net'), ('G', 'Occupancy'), ('H', 'ADR'),
    ('I', 'Ready for review'), ('J', 'Ledger issue'),
    ('K', 'Request received'), ('L', 'New-sign eligible'), ('M', 'Effective withdrawal'),
    ('N', 'Candidate'), ('O', 'Priority rank'), ('P', 'Reserved'), ('Q', 'Waitlist rank'),
    ('R', 'Issued archive'), ('S', 'Published status'), ('T', 'Vacant seats'),
    ('U', 'Refund entries'), ('V', 'Effective refund'), ('W', 'Corrected net'), ('X', 'Corrected ADR'),
    ('Y', 'Renewal policy applies'), ('Z', 'Reassessed eligible'), ('AA', 'Seat released'),
    ('AB', 'Revised rank'), ('AC', 'Revised reservation'), ('AD', 'Revised published status'),
)
BASE = {
    LEDGER: [[1, JULY, 80, 105, 5, 11400, 700, 300, 1, 1],
             [2, JULY, 80, 100, 0, 10240, 0, 0, 1, 1],
             [3, JULY, 80, 100, 0, 10560, 0, 0, 1, 1],
             [4, JULY, 80, 100, 0, 8800, 0, 0, 1, 1]],
    APPS: [[1, 1, 10, 0, 0, 1, 1, 0, 0],
           [2, 1, 20, 0, 0, 0, 0, 0, 0],
           [3, 1, 30, 0, 1, 1, 1, 0, 0],
           [4, 1, 40, 0, 0, 0, 0, 0, 0]],
    POLICY: [[JULY], [.75], [125], [2], [120], [JULY]],
    REFUNDS: [[1, 1, JULY, 880, 1]] + [[0, 1, JULY, 0, 0] for _ in range(5)],
}


@dataclass(frozen=True)
class Case:
    name: str
    changes: tuple


def change(sheet, row, column, value):
    return (sheet, row, column, value)


def ledger(**values):
    fields = {'id': 0, 'month': 1, 'sold': 2, 'capacity': 3, 'closed': 4, 'gross': 5,
              'tax': 6, 'credit': 7, 'posted': 8, 'active': 9}
    return tuple(change(LEDGER, 0, fields[field], value) for field, value in values.items())


def app(**values):
    fields = {'request': 1, 'priority': 2, 'withdrawn': 3, 'issued': 4, 'archive': 5, 'renew': 6}
    return tuple(change(APPS, 0, fields[field], value) for field, value in values.items())


def refund(**values):
    fields = {'id': 0, 'hotel': 1, 'month': 2, 'amount': 3, 'posted': 4}
    return tuple(change(REFUNDS, 0, fields[field], value) for field, value in values.items())


def event(row, identity, hotel, month, amount, posted=1):
    return tuple(change(REFUNDS, row, column, value) for column, value in
                 enumerate((identity, hotel, month, amount, posted)))


def policy(field, value):
    fields = {'month': 0, 'occupancy': 1, 'new_adr': 2, 'quota': 3, 'renewal_adr': 4, 'renewal_start': 5}
    return (change(POLICY, fields[field], 0, value),)


# A case can probe more than one feature. The feature-to-case table explicitly
# records which assertion addresses each rule and which later tests replay it.
SCENARIOS = {name: Case(name, mutations) for name, mutations in {
    'current': (), 'other_month': ledger(month=JUNE), 'inactive': ledger(active=0),
    'report_moves': policy('month', AUGUST), 'zero_inventory': ledger(capacity=0, closed=0, sold=0),
    'no_sold_nights': ledger(sold=0), 'negative_sales': ledger(sold=-1),
    'over_capacity': ledger(sold=101), 'at_capacity': ledger(sold=100),
    'repairs_reduce_inventory': ledger(closed=25), 'unusable_inventory': ledger(closed=107),
    'negative_closures': ledger(closed=-2), 'negative_gross': ledger(gross=-1),
    'negative_tax': ledger(tax=-1), 'negative_credit': ledger(credit=-1),
    'charges_exceed_gross': ledger(tax=12000), 'charges_equal_gross': ledger(tax=11100),
    'alternative_deductions': ledger(tax=1200, credit=200), 'unposted_ledger': ledger(posted=0),
    'duplicate_later_ledger': (change(LEDGER, 1, 0, 1),),
    'same_id_other_period': (change(LEDGER, 1, 0, 1), change(LEDGER, 1, 1, JUNE)),
    'earlier_same_id': (change(LEDGER, 0, 0, 2),),
    'exact_new_threshold': ledger(sold=75, gross=10375),
    'below_occupancy': ledger(sold=74, gross=10620), 'below_new_adr': ledger(gross=10999),
    'no_request': app(request=0), 'withdraw_application': app(withdrawn=1),
    'withdraw_after_issuance': app(withdrawn=1, issued=1), 'issued_no_request': app(request=0, issued=1),
    'issued_first': app(issued=1), 'no_archive': app(archive=0), 'new_sign_request': app(renew=0),
    'later_priority': app(priority=25), 'tie_priority': app(priority=20),
    'no_vacancy': policy('quota', 1), 'quota_zero_with_archive': policy('quota', 0),
    'additional_vacancy': policy('quota', 3), 'all_have_seats': policy('quota', 5),
    'archive_removes_capacity': (change(APPS, 2, 4, 0),),
    'candidate_two_withdraws': (change(APPS, 1, 3, 1),),
    'candidates_two_three_withdraw': (change(APPS, 1, 3, 1), change(APPS, 3, 3, 1)),
    'no_refund': refund(posted=0), 'refund_not_in_period': refund(month=JUNE),
    'refund_other_hotel': refund(hotel=2), 'invalid_refund_id': refund(id=0),
    'refund_still_qualifies': refund(amount=400), 'refund_exact_renewal_floor': refund(amount=800),
    'duplicate_transaction': event(1, 1, 1, JULY, 210),
    'distinct_transaction': event(1, 2, 1, JULY, 210),
    'offsetting_reversal': event(1, 2, 1, JULY, -880),
    'excess_reversal': event(1, 2, 1, JULY, -1000),
    'refund_capped_at_net': refund(amount=14000), 'refund_later_policy': policy('renewal_start', AUGUST),
    'refund_posted_after_signing': app(issued=1), 'renewal_floor_reduced': policy('renewal_adr', 115),
    'refund_with_withdrawal': app(withdrawn=1), 'refund_without_capacity': policy('quota', 1),
    'refund_before_policy_effective': ledger(month=JUNE) + policy('month', JUNE) + refund(month=JUNE),
    'stale_handoff_adr': (change(APPS, 0, 7, 137),), 'stale_handoff_decision': (change(APPS, 0, 8, 1),),
}.items()}


FEATURE_CASES = {
    'A': 'current other_month inactive report_moves no_sold_nights refund_before_policy_effective',
    'B': 'current duplicate_later_ledger same_id_other_period earlier_same_id other_month inactive',
    'C': 'current zero_inventory repairs_reduce_inventory unusable_inventory negative_closures at_capacity',
    'D': 'current negative_sales over_capacity at_capacity no_sold_nights zero_inventory unusable_inventory',
    'E': 'current negative_gross negative_tax negative_credit charges_exceed_gross charges_equal_gross',
    'F': 'current alternative_deductions unposted_ledger negative_tax charges_equal_gross negative_gross',
    'G': 'current zero_inventory no_sold_nights exact_new_threshold below_occupancy over_capacity',
    'H': 'current no_sold_nights alternative_deductions charges_equal_gross below_new_adr unposted_ledger',
    'I': 'current inactive duplicate_later_ledger unposted_ledger charges_exceed_gross over_capacity',
    'J': 'current other_month duplicate_later_ledger negative_sales negative_gross unposted_ledger',
    'K': 'current no_request unposted_ledger other_month negative_sales report_moves',
    'L': 'current exact_new_threshold below_occupancy below_new_adr no_request unposted_ledger',
    'M': 'current withdraw_application withdraw_after_issuance issued_no_request no_request issued_first',
    'N': 'current withdraw_application issued_first no_request below_new_adr exact_new_threshold',
    'O': 'current later_priority tie_priority withdraw_application candidate_two_withdraws earlier_same_id',
    'P': 'current later_priority no_vacancy additional_vacancy archive_removes_capacity withdraw_application',
    'Q': 'current no_vacancy later_priority additional_vacancy candidate_two_withdraws withdraw_application',
    'R': 'current issued_first withdraw_after_issuance issued_no_request archive_removes_capacity no_request',
    'S': 'current issued_first later_priority no_vacancy withdraw_application no_request',
    'T': 'current all_have_seats quota_zero_with_archive additional_vacancy candidates_two_three_withdraw no_request',
    'U': 'current no_refund refund_not_in_period refund_other_hotel duplicate_transaction distinct_transaction invalid_refund_id',
    'V': 'current offsetting_reversal excess_reversal refund_capped_at_net duplicate_transaction refund_other_hotel',
    'W': 'current refund_still_qualifies distinct_transaction refund_capped_at_net excess_reversal unposted_ledger',
    'X': 'current refund_exact_renewal_floor refund_capped_at_net offsetting_reversal no_sold_nights duplicate_transaction',
    'Y': 'current no_archive new_sign_request refund_later_policy refund_before_policy_effective no_request',
    'Z': 'current refund_exact_renewal_floor below_occupancy renewal_floor_reduced refund_still_qualifies stale_handoff_adr',
    'AA': 'current refund_still_qualifies no_vacancy refund_posted_after_signing withdraw_application refund_other_hotel stale_handoff_decision',
    'AB': 'current refund_exact_renewal_floor candidate_two_withdraws later_priority tie_priority withdraw_application stale_handoff_decision',
    'AC': 'current refund_exact_renewal_floor refund_without_capacity refund_posted_after_signing additional_vacancy candidate_two_withdraws stale_handoff_decision',
    'AD': 'current refund_exact_renewal_floor refund_without_capacity refund_posted_after_signing additional_vacancy candidate_two_withdraws stale_handoff_decision',
}


def world(case):
    raw = deepcopy(BASE)
    for sheet, row, column, value in case.changes:
        raw[sheet][row][column] = value
    return raw


def rank(candidates, applications):
    order = sorted((i for i, candidate in enumerate(candidates) if candidate),
                   key=lambda index: (applications[index][2], index))
    return [order.index(i) + 1 if i in order else 0 for i in range(len(applications))]


def compute(raw, mode):
    """Interpret the business rules in Python without using the XLSX formulas."""
    month, min_occ, min_adr, quota, renew_floor, effective_month = [item[0] for item in raw[POLICY]]
    apps = raw[APPS]
    rows, seen_keys = [], set()
    for identity, period, sold, stock, closed, gross, tax, credit, posted, active in raw[LEDGER]:
        in_period = active == 1 and period == month
        first = (identity, period) not in seen_keys
        seen_keys.add((identity, period))
        sellable = max(0, stock - closed)
        nights = 0 <= closed <= stock and 0 <= sold <= sellable and sellable > 0
        amount = min(gross, tax, credit) >= 0 and gross >= tax + credit
        net = gross - tax - credit if in_period and first and amount and posted else 0
        occ = Fraction(sold, sellable) if in_period and first and nights else 0
        adr = Fraction(net, sold) if in_period and first and nights and sold else 0
        ready = in_period and first and nights and amount and posted == 1
        issue = next((index for index, check in enumerate((in_period, first, nights, amount, posted == 1), 1)
                      if not check), 0)
        rows.append([int(in_period), int(first), sellable, int(nights), int(amount), net, occ, adr, int(ready), issue])
    issued = [entry[4] == 1 for entry in apps]
    vacancy = max(0, quota - sum(issued))
    received = [bool(before[8] and entry[1]) for before, entry in zip(rows, apps)]
    eligible = [received[i] and before[6] >= Fraction(str(min_occ)) and before[7] >= min_adr
                for i, before in enumerate(rows)]
    withdrawn = [bool(entry[1] and entry[3] and not issued[i]) for i, entry in enumerate(apps)]
    candidates = [eligible[i] and not (withdrawn[i] or issued[i]) for i in range(len(rows))]
    ranks = rank(candidates, apps)
    reserved = [0 < position <= vacancy for position in ranks]
    vacant_seats = vacancy - sum(reserved)
    for i, row in enumerate(rows):
        wait = max(0, ranks[i] - vacancy)
        status = 3 if issued[i] else 2 if reserved[i] else 1 if wait else 0
        row.extend([int(received[i]), int(eligible[i]), int(withdrawn[i]), int(candidates[i]),
                    ranks[i], int(reserved[i]), wait, int(issued[i]), status, vacant_seats])

    valid_events, seen_ids = [], set()
    for event_id, hotel, period, amount, posted in raw[REFUNDS]:
        if event_id <= 0 or posted != 1 or not amount or hotel not in range(1, 5) or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        valid_events.append((hotel, period, amount))
    revised_candidates = []
    for i, row in enumerate(rows):
        identity, period, sold, *_ = raw[LEDGER][i]
        events = [value for hotel, date, value in valid_events if (identity, month) == (hotel, date) and row[0] and row[1]]
        applied = min(row[5], max(0, sum(events)))
        corrected_net = row[5] - applied
        corrected_adr = Fraction(corrected_net, sold) if row[8] and sold else 0
        renewal = bool(apps[i][1] and apps[i][5] and apps[i][6] and period == month and month >= effective_month)
        approval_adr = apps[i][7] if mode == 'manual' else corrected_adr
        reassessed = received[i] and row[6] >= Fraction(str(min_occ)) and approval_adr >= (
            renew_floor if renewal else min_adr)
        selected = bool(apps[i][8]) if mode == 'manual' else reassessed
        candidate = received[i] and selected and not (withdrawn[i] or issued[i])
        revised_candidates.append(candidate)
        row.extend([len(events), applied, corrected_net, corrected_adr, int(renewal), int(reassessed),
                    int(reserved[i] and not candidate)])
    for i, (row, position) in enumerate(zip(rows, rank(revised_candidates, apps))):
        allocated = 0 < position <= vacancy
        status = 3 if issued[i] else 2 if allocated else 1 if position else 0
        row.extend([position, int(allocated), status])
    return rows


def case_inputs(case, stage, mode):
    raw = world(case)
    if stage < 3:
        raw[REFUNDS] = [[0, 1, JULY, 0, 0] for _ in EVENT_ROWS]
        return raw
    if mode == 'manual':
        # Simulate a separately confirmed operator handoff. Normal handoffs
        # reflect the event; deliberately divergent confirmations are probed too.
        calculated = compute(raw, 'linked')
        for entry, row in zip(raw[APPS], calculated):
            entry[7], entry[8] = float(row[23]), row[25]
        for sheet, row, column, value in case.changes:
            if sheet == APPS and column in (7, 8):
                raw[sheet][row][column] = value
    return raw


def stage_features(stage):
    return COLS[(stage - 1) * 10:stage * 10]
