"""Reference actions. The builder exposes only completed predecessor actions."""
import json
from inspect import getsource
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from openpyxl import load_workbook


def table(book, name):
    return list(book[name].iter_rows(min_row=2, values_only=True)) if book[name].max_row > 1 else []


def find(book, name, key):
    return next(((row, values) for row, values in enumerate(table(book, name), 2)
                 if values[0] == key), (None, None))


def metrics(book, hotel):
    _, (_, sold, capacity, net) = find(book, 'Ledger', hotel)
    minimum_occupancy, minimum_adr, _ = next(book['Policy'].iter_rows(min_row=2, values_only=True))
    adr = net // sold if sold else 0
    return adr, sold > 0 and 100 * sold >= minimum_occupancy * capacity and adr >= minimum_adr


def free_seat(book):
    _, _, quota = next(book['Policy'].iter_rows(min_row=2, values_only=True))
    return sum(state in ('reserved', 'issued') for _, state, _ in table(book, 'Applications')) < quota


def pending_release(book):
    return next(((row, token) for row, (token, _, consumed) in enumerate(table(book, 'Releases'), 2)
                 if consumed is None), (None, None))


def request(book, event):
    hotel = event['hotel']
    if not find(book, 'Ledger', hotel)[1]:
        return 'unknown_hotel'
    _, previous = find(book, 'Applications', hotel)
    if previous:
        return 'already_applied'
    adr, eligible = metrics(book, hotel)
    if not eligible:
        return 'ineligible'
    if not free_seat(book):
        return 'no_capacity'
    book['Applications'].append([hotel, 'reserved', adr])
    return 'reserved'


def issue(book, event):
    hotel = event['hotel']
    row, application = find(book, 'Applications', hotel)
    if not application or application[1] != 'reserved':
        return 'not_reserved'
    adr, eligible = metrics(book, hotel)
    if not eligible:
        return 'ineligible'
    book['Applications'].cell(row, 2, 'issued')
    book['Issued'].append([hotel, adr])
    return 'issued'


def waitlist(book, event):
    hotel = event['hotel']
    if not find(book, 'Ledger', hotel)[1]:
        return 'unknown_hotel'
    if find(book, 'Applications', hotel)[1]:
        return 'already_applied'
    adr, eligible = metrics(book, hotel)
    if not eligible:
        return 'ineligible'
    book['Applications'].append([hotel, 'waiting', adr])
    return 'waiting'


def release(book, event):
    hotel = event['hotel']
    row, application = find(book, 'Applications', hotel)
    if not application or application[1] != 'reserved':
        return 'not_reserved'
    book['Applications'].cell(row, 2, 'released')
    token = len(table(book, 'Releases')) + 1
    book['Releases'].append([token, hotel])
    return 'released'


def promote(book, event):
    grant_row, _ = pending_release(book)
    if grant_row is None:
        return 'no_release'
    if not free_seat(book):
        return 'no_capacity'
    for row, (hotel, state, _) in enumerate(table(book, 'Applications'), 2):
        if state != 'waiting':
            continue
        adr, eligible = metrics(book, hotel)
        if eligible:
            book['Applications'].cell(row, 2, 'reserved')
            book['Applications'].cell(row, 3, adr)
            book['Releases'].cell(grant_row, 3, hotel)
            return 'promoted'
    return 'no_candidate'


def refund(book, event):
    hotel, identity, amount = event['hotel'], event['id'], event['amount']
    ledger_row, ledger = find(book, 'Ledger', hotel)
    if not ledger:
        return 'unknown_hotel'
    _, prior = find(book, 'Refunds', identity)
    if prior:
        return 'duplicate' if prior == (identity, hotel, amount) else 'event_conflict'
    if type(amount) is not int or not 0 < amount <= ledger[3]:
        return 'invalid_amount'
    book['Ledger'].cell(ledger_row, 4, ledger[3] - amount)
    book['Refunds'].append([identity, hotel, amount])
    application_row, application = find(book, 'Applications', hotel)
    if application and application[1] == 'reserved':
        adr, eligible = metrics(book, hotel)
        book['Applications'].cell(application_row, 3, adr)
        if not eligible:
            release(book, event)
    return 'refunded'


def main():
    destination = Path(sys.argv[1])
    event = json.loads(sys.argv[2])
    book = load_workbook(destination)
    operation = ACTIONS.get(event['action'])
    status = operation(book, event) if operation else 'unimplemented'
    if status in WRITES:
        # Save on the same filesystem and publish a complete workbook at once.
        with TemporaryDirectory(dir=destination.parent) as scratch:
            pending = Path(scratch) / destination.name
            book.save(pending)
            pending.replace(destination)
    book.close()
    print(json.dumps({'result': status}))


def reference_source(stage):
    stages = {1: (request, issue), 2: (waitlist, release, promote), 3: (refund,)}
    selected = [action for level, actions in stages.items() if level <= stage for action in actions]
    helpers = (table, find, metrics, free_seat) if stage >= 1 else ()
    if stage >= 2:
        helpers += (pending_release,)
    header = ('import json\nimport sys\nfrom pathlib import Path\nfrom tempfile import TemporaryDirectory\n'
              'from openpyxl import load_workbook\n\n')
    core = '\n\n'.join(getsource(function).strip() for function in (*helpers, *selected, main))
    if stage >= 2:
        anchor = "    if not free_seat(book):\n        return 'no_capacity'\n    book['Applications'].append"
        if core.count(anchor) != 1:
            raise ValueError('The request capacity check must have exactly one stage-upgrade anchor')
        core = core.replace(anchor, anchor.replace('not free_seat(book)', 'not free_seat(book) or pending_release(book)[0]'))
    registry = ', '.join(f'{f.__name__!r}: {f.__name__}' for f in selected)
    statuses = ['reserved', 'issued', 'waiting', 'released', 'promoted', 'refunded']
    write_set = statuses[:(0, 2, 5, 6)[stage]]
    return (header + core + f'\n\nACTIONS = {{{registry}}}\n'
            f'WRITES = {write_set!r}\n'
            "\nif __name__ == '__main__':\n    main()\n")
