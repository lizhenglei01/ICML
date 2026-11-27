"""Independent SQLite execution for relational programs. Numeric values stay exact."""
from contextlib import closing
from fractions import Fraction
import sqlite3


class RationalTotal:
    def __init__(self):
        self.total = Fraction()

    def step(self, value):
        self.total += Fraction(value)

    def finalize(self):
        return str(self.total)


def rational_order(left, right):
    left, right = Fraction(left), Fraction(right)
    return (left > right) - (left < right)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def load(db, name, rows):
    fields = list(rows[0]) if rows else []
    columns = ', '.join(f'{quote(key)} TEXT' for key in fields)
    db.execute(f'CREATE TEMP TABLE {name} ("@index" INTEGER{", " + columns if columns else ""})')
    db.executemany(f'INSERT INTO {name} VALUES ({", ".join("?" for _ in range(len(fields) + 1))})',
                   ((index, *(str(row[field]) for field in fields)) for index, row in enumerate(rows)))


def execute_step(op, params, inputs):
    with closing(sqlite3.connect(':memory:')) as db:
        db.create_collation('rational', rational_order)
        db.create_aggregate('rational_total', 1, RationalTotal)
        a = inputs[0]
        if op in {'AND', 'OR', 'COUNT'}:
            db.execute('CREATE TABLE members (value TEXT, source INTEGER)')
            db.executemany('INSERT INTO members VALUES (?, ?)', ((value, i) for i, members in enumerate(inputs) for value in members))
            if op == 'COUNT':
                return db.execute('SELECT count(DISTINCT value) FROM members').fetchone()[0]
            rows = db.execute('SELECT value FROM members GROUP BY value HAVING count(*) >= ?',
                              (len(inputs) if op == 'AND' else 1,))
            return frozenset(row[0] for row in rows)
        if op in {'GROUP_SUM', 'GROUP_AVG'}:
            value, result = params['value'], params['as']
            columns = params['keys']
            rows = tuple({**dict(zip(columns, key, strict=True)), value: row[value]} for key, group in a.items() for row in group)
            if not rows:
                return ()
            load(db, 'a', rows)
            keys = ', '.join(map(quote, columns))
            selected = db.execute(f'SELECT {keys}, rational_total({quote(value)}), count(*) FROM a GROUP BY {keys} ORDER BY {keys}')
            return tuple({**dict(zip(columns, entry[:-2], strict=True)),
                          result: Fraction(entry[-2]) / (entry[-1] if op == 'GROUP_AVG' else 1)} for entry in selected)
        if not a:
            return {} if op == 'GROUP_BY' else frozenset() if op == 'PROJECT_SET' else ()
        load(db, 'a', a)
        if op in {'JOIN', 'LOOKUP'}:
            b = inputs[1]
            load(db, 'b', b)
            key = quote(params['on'])
            fields = params['fields'] if op == 'JOIN' else [params['field']]
            pairs = db.execute(f'SELECT a."@index", b."@index" FROM a JOIN b ON a.{key}=b.{key} ORDER BY a."@index"').fetchall()
            if len(pairs) != len(a) or len({i for i, _ in pairs}) != len(a):
                raise ValueError('SQL reference found a missing or ambiguous join')
            return tuple({**a[i], **{field: b[j][field] for field in fields}} for i, j in pairs)
        if op == 'GROUP_BY':
            fields = ', '.join(map(quote, params['by']))
            out = {}
            for *key, i in db.execute(f'SELECT {fields}, "@index" FROM a ORDER BY "@index"'):
                out.setdefault(tuple(key), []).append(a[i])
            return out
        if op == 'PROJECT_SET':
            return frozenset(row[0] for row in db.execute(f'SELECT DISTINCT {quote(params["field"])} FROM a'))
        if op == 'FILTER_IN':
            db.execute('CREATE TABLE membership (value TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO membership VALUES (?)', ((v,) for v in inputs[1]))
            query = f'SELECT "@index" FROM a WHERE {quote(params["field"])} IN (SELECT value FROM membership) ORDER BY "@index"'
            return tuple(a[i] for i, in db.execute(query))
        if op == 'FILTER_NUM':
            comparison = {'GE': '>=', 'LE': '<=', 'GT': '>', 'LT': '<', 'EQ': '='}[params['comparison']]
            query = f'SELECT "@index" FROM a WHERE {quote(params["field"])} COLLATE rational {comparison} ? ORDER BY "@index"'
            return tuple(a[i] for i, in db.execute(query, (str(params['threshold']),)))
        if op in {'RANK', 'ARGMAX'}:
            direction = 'DESC' if op == 'ARGMAX' or params['order'] == 'DESC' else 'ASC'
            fields = ', '.join(map(quote, params['keys']))
            query = f'SELECT "@index" FROM a ORDER BY {quote(params["score"])} COLLATE rational {direction}, {fields}'
            if op == 'ARGMAX':
                query += ' LIMIT 1'
            return tuple({**a[i], 'rank': rank} for rank, (i,) in enumerate(db.execute(query), 1))
        if op == 'TOP_K':
            return tuple(a[i] for i, in db.execute('SELECT "@index" FROM a ORDER BY "@index" LIMIT ?', (params['k'],)))
        raise ValueError(f'No SQLite implementation for {op}')


def reference(graph, sources):
    state = dict(sources)
    for rule in graph['productions']:
        state[rule['output']] = execute_step(rule['op'], rule['program_parameters'], [state[key] for key in rule['inputs']])
    return state
