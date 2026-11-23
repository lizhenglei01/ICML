"""Order-sensitive hotel sequences and an independent in-memory Oracle."""
from copy import deepcopy
import random

from .hotel_stateful_bench import TEMPLATE as BASE

MONTH, PRIOR = 202607, 202606
STATES = ('reserved', 'waiting', 'issued', 'released', 'withdrawn')
SCHEMA = {**BASE, 'Prior ledger': [['Month', 'Hotel', 'Sold nights', 'Available nights', 'Net revenue cents']],
          'Prior policy': [['Month', 'Min ADR cents'], [PRIOR, 12000]],
          'Reversals': [['Event ID', 'Refund ID']],
          'Adjustments': [['Event ID', 'Hotel', 'Month', 'Delta cents']],
          'Publications': [['Publication ID', 'Month', 'Net revenue cents', 'Eligible hotels']]}


def fixture(seed, hotels, quota):
    rng = random.Random(seed)
    template = deepcopy(SCHEMA)
    names = ['Alder', 'Birch', 'Cedar', 'Dale', *(f'Hotel_{i}' for i in range(5, hotels + 1))][:hotels]
    rows = []
    for i, name in enumerate(names):
        sold = 80 if i < 2 else rng.choice((75, 80, 85, 90))
        adr = (13000, 12800, 13200, 11000)[i] if i < 4 else rng.randint(12600, 14200)
        rows.append([name, sold, 100, sold * adr])
    template['Ledger'] = [SCHEMA['Ledger'][0], *rows]
    template['Policy'][1][2] = quota
    template['Prior ledger'].extend([[PRIOR, name, sold, capacity, sold * (11900 if name == 'Birch' else 12100)]
                                    for name, sold, capacity, _ in rows])
    template['Finance notes'][0][1] = f'=SUM(Ledger!D2:D{hotels+1})'
    return template


class LedgerModel:
    """Compute state transitions without invoking the Excel or report writer."""

    def __init__(self, template):
        self.seed = template
        self.current = {row[0]: row[1:] for row in deepcopy(template['Ledger'][1:])}
        self.past = {row[1]: row[2:] for row in deepcopy(template['Prior ledger'][1:])}
        self.minimum, self.floor, self.quota = template['Policy'][1]
        self.app, self.issued, self.released = {}, {}, []
        self.refunds, self.reversed, self.adjusted, self.published = {}, {}, {}, {}
        self.versions = {}

    def eligible(self, hotel, month=MONTH):
        sold, available, net = (self.current if month == MONTH else self.past)[hotel]
        floor = self.floor if month == MONTH else self.seed['Prior policy'][1][1]
        return sold * 100 >= self.minimum * available and sold > 0 and net >= floor * sold

    def adr(self, hotel):
        sold, _, net = self.current[hotel]
        return net // sold

    def views(self, month=MONTH):
        source = self.current if month == MONTH else self.past
        statuses = [item[0] for item in self.app.values()] if month == MONTH else []
        return [month, sum(row[2] for row in source.values()), sum(self.eligible(hotel, month) for hotel in source),
                *(statuses.count(label) for label in STATES)]

    def snapshot(self):
        tables = {
            'Ledger': [[key, *row] for key, row in self.current.items()],
            'Prior ledger': [[PRIOR, key, *row] for key, row in self.past.items()],
            'Applications': [[key, *value] for key, value in self.app.items()],
            'Issued': [[key, value] for key, value in self.issued.items()],
            'Releases': deepcopy(self.released),
            'Refunds': [[key, *value] for key, value in self.refunds.items()],
            'Reversals': [list(pair) for pair in self.reversed.items()],
            'Adjustments': [[key, *value] for key, value in self.adjusted.items()],
            'Publications': [[key, *value] for key, value in self.published.items()],
        }
        return {key: [value[0], *tables[key]] if key in tables else value for key, value in self.seed.items()}

    def free(self):
        return sum(row[0] in ('reserved', 'issued') for row in self.app.values()) < self.quota

    def make_release(self, hotel):
        self.app[hotel][0] = 'released'
        self.released.append([len(self.released) + 1, hotel, None])

    def recalculate_reserved(self, hotel):
        if self.app.get(hotel, [None])[0] == 'reserved':
            self.app[hotel][1] = self.adr(hotel)
            if not self.eligible(hotel):
                self.make_release(hotel)

    def apply(self, event, level):
        action, hotel = event['action'], event.get('hotel')
        identity, month = event.get('id'), event.get('month')
        state = self.app.get(hotel, [None])[0]
        pending = next((row for row in self.released if row[2] is None), None)
        if action in ('request', 'waitlist'):
            if hotel not in self.current:
                return 'unknown_hotel'
            if state:
                return 'already_applied'
            if not self.eligible(hotel):
                return 'ineligible'
            if action == 'request' and (not self.free() or pending is not None):
                return 'no_capacity'
            status = 'reserved' if action == 'request' else 'waiting'
            self.app[hotel] = [status, self.adr(hotel)]
            return status
        if action in ('issue', 'release'):
            if state != 'reserved':
                return 'not_reserved'
            if action == 'release':
                self.make_release(hotel)
                return 'released'
            if not self.eligible(hotel):
                return 'ineligible'
            self.app[hotel][0] = 'issued'
            self.issued[hotel] = self.adr(hotel)
            if level >= 2:
                self.versions[f'signed/{hotel}'] = self.views()
            return 'issued'
        if action == 'promote':
            if pending is None:
                return 'no_release'
            if not self.free():
                return 'no_capacity'
            chosen = next((key for key, row in self.app.items() if row[0] == 'waiting' and self.eligible(key)), None)
            if chosen is None:
                return 'no_candidate'
            pending[2] = chosen
            self.app[chosen] = ['reserved', self.adr(chosen)]
            return 'promoted'
        if action == 'refund':
            amount = event['amount']
            if hotel not in self.current:
                return 'unknown_hotel'
            if identity in self.refunds:
                return 'duplicate' if self.refunds[identity] == [hotel, amount] else 'event_conflict'
            if type(amount) is not int or not 0 < amount <= self.current[hotel][2]:
                return 'invalid_amount'
            self.refunds[identity] = [hotel, amount]
            self.current[hotel][2] -= amount
            self.recalculate_reserved(hotel)
            return 'refunded'
        if action == 'reverse_refund':
            original = event['refund_id']
            if identity in self.reversed:
                return 'duplicate' if self.reversed[identity] == original else 'event_conflict'
            if original not in self.refunds:
                return 'unknown_refund'
            if original in self.reversed.values():
                return 'already_reversed'
            hotel, amount = self.refunds[original]
            self.reversed[identity] = original
            self.current[hotel][2] += amount
            self.recalculate_reserved(hotel)
            if self.app.get(hotel, [None])[0] == 'released' and self.eligible(hotel):
                self.app[hotel] = ['waiting', self.adr(hotel)]
            return 'reversed'
        if action == 'adjust_month':
            record = [hotel, month, event['delta']]
            if identity in self.adjusted:
                return 'duplicate' if self.adjusted[identity] == record else 'event_conflict'
            if month not in (MONTH, PRIOR):
                return 'unknown_period'
            source = self.current if month == MONTH else self.past
            if hotel not in source:
                return 'unknown_hotel'
            delta = event['delta']
            if type(delta) is not int or not delta or source[hotel][2] + delta < 0:
                return 'invalid_amount'
            self.adjusted[identity] = record
            source[hotel][2] += delta
            if month == MONTH:
                self.recalculate_reserved(hotel)
            return 'adjusted'
        if action == 'withdraw':
            if state not in ('reserved', 'waiting', 'released'):
                return 'not_withdrawable'
            if state == 'reserved':
                self.make_release(hotel)
            self.app[hotel][0] = 'withdrawn'
            return 'withdrawn'
        if action == 'amend_report':
            if identity in self.published:
                return 'duplicate' if self.published[identity][0] == month else 'event_conflict'
            if month not in (MONTH, PRIOR):
                return 'unknown_period'
            view = self.views(month)
            self.published[identity] = view[:3]
            self.versions[f'amendments/{identity}'] = view
            return 'published'
        raise ValueError(f'Unknown fixture action {action}')


def event(action, hotel=None, **inputs):
    return {'action': action, **({'hotel': hotel} if hotel is not None else {}), **inputs}


def generated_worlds(stage, arm, seeds=(101, 202, 303)):
    """Create matched worlds with 4/6/8 hotels and 1/2/3 available seats."""
    cases = []
    for seed, count, quota in zip(seeds, (4, 6, 8), (1, 2, 3), strict=True):
        template = fixture(seed, count, quota)
        request = event('request', 'Alder')
        issue = event('issue', 'Alder')
        wait = event('waitlist', 'Birch')
        refund = event('refund', 'Alder', id='R1', amount=88000)
        occupied = [event('request', name) for name in ('Hotel_5', 'Hotel_6')[:quota-1]]
        promote = event('promote')
        reverse = event('reverse_refund', id='V1', refund_id='R1')
        adjust_prior = event('adjust_month', 'Birch', id='J1', month=PRIOR, delta=16000)
        past_publish = event('amend_report', id='P1', month=PRIOR)
        m1 = {
            'issue_and_duplicate_guard': [*occupied, request, issue, issue, event('request', 'Birch')],
            'preconditions': [event('request', 'Dale'), issue, event('request', 'Missing')],
        }
        m2 = {'old_approval_and_candidate_release': [*occupied, request, wait, promote, event('release', 'Alder'),
                                                    event('request', 'Cedar'), promote, promote, event('issue', 'Birch')],
              'release_record_gate': [*occupied, request, wait, event('release', 'Alder'),
                                      {'intervention': 'remove_release'}, promote]}
        m3 = {'refund_before_signing': [*occupied, request, wait, refund, issue, promote, refund],
              'signing_before_refund': [*occupied, request, wait, issue, refund, promote],
              'refund_release_gate': [*occupied, request, wait, refund, {'intervention': 'remove_release'}, promote]}
        expanded = {
            'reversal_after_slot_taken': [*occupied, request, wait, refund, promote, reverse, promote, issue,
                                          event('issue', 'Birch'), reverse],
            'reversal_before_promotion': [*occupied, request, wait, refund, reverse, promote, issue],
            'withdraw_and_reassign': [*occupied, request, wait, event('waitlist', 'Cedar'), event('withdraw', 'Birch'),
                                      event('withdraw', 'Alder'), promote, event('issue', 'Cedar'),
                                      event('withdraw', 'Cedar')],
            'cross_month_publish_versions': [*occupied, request, issue, past_publish, adjust_prior, past_publish,
                                             event('amend_report', id='P2', month=PRIOR), refund,
                                             event('amend_report', id='P3', month=MONTH)],
            'current_adjustment_reuses_release': [*occupied, request, wait,
                event('adjust_month', 'Alder', id='J2', month=MONTH, delta=-88000), issue, promote],
        }
        for level, group in enumerate((m1, m2, m3), 1):
            if level > stage:
                break
            if level == 3 and arm == 'expanded':
                group |= expanded
            for name, actions in group.items():
                model = LedgerModel(template)
                reports = model.views()
                steps = []
                for action in actions:
                    if 'intervention' in action:
                        model.released.clear()
                        status = None
                    else:
                        status = model.apply(action, stage)
                        if stage >= 2:
                            reports = model.views()
                    steps.append({'event': action, 'reply': {'result': status} if status is not None else None,
                                  'workbook': deepcopy(model.snapshot()), 'current': reports,
                                  'versions': deepcopy(model.versions)})
                cases.append({'name': f'{seed}:{name}', 'scenario': name, 'seed': seed,
                              'hotels': count, 'capacity': quota, 'introduced': level,
                              'initial': template, 'initial_report': LedgerModel(template).views(), 'steps': steps})
    return cases
