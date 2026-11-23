"""Private staged reference for the hotel upgrade benchmark."""
from inspect import getsource
from pathlib import Path
from shutil import copy2

from docx import Document
from pptx import Presentation
from pptx.chart.data import CategoryChartData

from .hotel_stateful_reference import reference_source

REPORTS = ('current.docx', 'current.pptx')
STATES = ('reserved', 'waiting', 'issued', 'released', 'withdrawn')
LABELS = ('Period', 'Net revenue cents', 'Eligible hotels', *STATES)


def report_values(book, month):
    if month == 202607:
        rows = table(book, 'Ledger')
        entries = table(book, 'Applications')
        return [month, sum(row[3] for row in rows), sum(metrics(book, row[0])[1] for row in rows),
                *(sum(entry[1] == state for entry in entries) for state in STATES)]
    rows = [row for row in table(book, 'Prior ledger') if row[0] == month]
    _, (_, floor) = find(book, 'Prior policy', month)
    occupancy, _, _ = next(book['Policy'].iter_rows(min_row=2, values_only=True))
    return [month, sum(row[4] for row in rows),
            sum(0 < sold and sold * 100 >= occupancy * available and net // sold >= floor
                for _, _, sold, available, net in rows), *([0] * len(STATES))]


def update_reports(folder, values, destination=None):
    destination = destination or folder
    destination.mkdir(parents=True, exist_ok=True)
    doc = Document(folder / REPORTS[0])
    deck = Presentation(folder / REPORTS[1])
    table_shape = next(shape for shape in deck.slides[0].shapes if shape.has_table)
    chart = next(shape.chart for shape in deck.slides[0].shapes if shape.has_chart)
    for row, value in enumerate(values, 1):
        doc.tables[0].cell(row, 1).text = str(value)
        table_shape.table.cell(row, 1).text = str(value)
    chart_data = CategoryChartData()
    chart_data.categories = STATES
    chart_data.add_series('Applications', values[3:])
    chart.replace_data(chart_data)
    doc.save(destination / REPORTS[0])
    deck.save(destination / REPORTS[1])


def reconcile(book, event):
    hotel = event['hotel']
    row, check = find(book, 'Applications', hotel)
    if not check or check[1] != 'reserved':
        return
    adr, eligible = metrics(book, hotel)
    book['Applications'].cell(row, 3, adr)
    if not eligible:
        release(book, event)


def reverse_refund(book, event):
    identity, refund_id = event['id'], event['refund_id']
    _, previous = find(book, 'Reversals', identity)
    if previous:
        return 'duplicate' if previous[1] == refund_id else 'event_conflict'
    _, original = find(book, 'Refunds', refund_id)
    if not original:
        return 'unknown_refund'
    if any(row[1] == refund_id for row in table(book, 'Reversals')):
        return 'already_reversed'
    _, hotel, amount = original
    row, account = find(book, 'Ledger', hotel)
    book['Ledger'].cell(row, 4, account[3] + amount)
    book['Reversals'].append([identity, refund_id])
    application_row, check = find(book, 'Applications', hotel)
    reconcile(book, {'hotel': hotel})
    if check and check[1] == 'released':
        adr, eligible = metrics(book, hotel)
        if eligible:
            book['Applications'].cell(application_row, 2, 'waiting')
            book['Applications'].cell(application_row, 3, adr)
    return 'reversed'


def adjust_month(book, event):
    identity, hotel, month, delta = (event[key] for key in ('id', 'hotel', 'month', 'delta'))
    _, prior = find(book, 'Adjustments', identity)
    if prior:
        return 'duplicate' if prior == (identity, hotel, month, delta) else 'event_conflict'
    if month == 202607:
        page, column = 'Ledger', 4
        row, before = find(book, page, hotel)
    elif month == 202606:
        page, column = 'Prior ledger', 5
        row, before = next(((row, item) for row, item in enumerate(table(book, page), 2)
                            if tuple(item[:2]) == (month, hotel)), (None, None))
    else:
        return 'unknown_period'
    if not before:
        return 'unknown_hotel'
    if type(delta) is not int or delta == 0 or before[column - 1] + delta < 0:
        return 'invalid_amount'
    book[page].cell(row, column, before[column - 1] + delta)
    book['Adjustments'].append([identity, hotel, month, delta])
    if month == 202607:
        reconcile(book, event)
    return 'adjusted'


def withdraw(book, event):
    row, existing = find(book, 'Applications', event['hotel'])
    if not existing or existing[1] not in ('reserved', 'waiting', 'released'):
        return 'not_withdrawable'
    if existing[1] == 'reserved':
        release(book, event)
    book['Applications'].cell(row, 2, 'withdrawn')
    return 'withdrawn'


def amend_report(book, event):
    identity, month = event['id'], event['month']
    _, exists = find(book, 'Publications', identity)
    if exists:
        return 'duplicate' if exists[1] == month else 'event_conflict'
    if month not in (202606, 202607):
        return 'unknown_period'
    _, net, eligible, *_ = report_values(book, month)
    book['Publications'].append([identity, month, net, eligible])
    return 'published'


def upgrade_main():
    path = Path(sys.argv[1])
    event = json.loads(sys.argv[2])
    book = load_workbook(path)
    action = ACTIONS.get(event['action'])
    result = action(book, event) if action else 'unimplemented'
    if result in WRITES:
        folder = path.parent
        if LEVEL >= 2:
            update_reports(folder, report_values(book, 202607))
            if result == 'issued':
                snapshot = folder / 'signed' / event['hotel']
                snapshot.mkdir(parents=True, exist_ok=False)
                for name in REPORTS:
                    copy2(folder / name, snapshot / name)
            elif result == 'published':
                update_reports(folder, report_values(book, event['month']), folder / 'amendments' / event['id'])
        with TemporaryDirectory(dir=folder) as scratch:
            target = Path(scratch) / path.name
            book.save(target)
            target.replace(path)
    book.close()
    print(json.dumps({'result': result}))


def upgrade_source(level, arm):
    old = reference_source(level).rsplit("if __name__ == '__main__':", 1)[0]
    extra = ('from shutil import copy2\nfrom docx import Document\nfrom pptx import Presentation\n'
             'from pptx.chart.data import CategoryChartData\n')
    actions = (reverse_refund, adjust_month, withdraw, amend_report) if level == 3 and arm == 'expanded' else ()
    helpers = (report_values, update_reports) if level >= 2 else ()
    if actions:
        helpers += (reconcile,)
    code = '\n\n'.join(getsource(func).strip() for func in (*helpers, *actions, upgrade_main))
    return (old + extra + f'\nLEVEL = {level}\nREPORTS = {REPORTS!r}\nSTATES = {STATES!r}\n\n' +
            code + '\n\n' + f"ACTIONS.update({{{', '.join(f'{func.__name__!r}: {func.__name__}' for func in actions)}}})\n" +
            ("WRITES += ['reversed', 'adjusted', 'withdrawn', 'published']\n" if actions else '') +
            "\nif __name__ == '__main__':\n    upgrade_main()\n")
