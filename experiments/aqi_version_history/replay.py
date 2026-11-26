"""Replay two pinned WeeWX versions through their published report interfaces."""

import argparse
from contextlib import closing
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from importlib import import_module
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


VERSIONS = {
    'airgradient': ('cc7f6a2654b96e6f21b7a2144d58609a0341336f', '9ad5cc7fc818b194c678adbe6bc19f37c697184b'),
    'purple': ('92b77bbd198360f92cf42219e56735722213f32b', '7636c4434f2f688c25a3b3bef257caccde1bf4f4'),
}
BREAKPOINTS = (
    ('0', '9.0', 0, 50, 'rgb(0,228,0)'),
    ('9.1', '35.4', 51, 100, 'rgb(255,255,0)'),
    ('35.5', '55.4', 101, 150, 'rgb(255,126,0)'),
    ('55.5', '125.4', 151, 200, 'rgb(255,0,0)'),
    ('125.5', '225.4', 201, 300, 'rgb(143,63,151)'),
    ('225.5', '325.4', 301, 500, 'rgb(126,0,35)'),
)
INPUTS = ('9.09', '9.1', '35.4', '35.5', '55.4', '55.5', '125.4', '125.5',
          '225.4', '225.5', '239.97', '250', '311.7', '325.4', '400', '425.4')
BASE_TIME = 1700000000


def git_file(checkout, revision, path):
    return subprocess.check_output(['git', '-C', str(checkout), 'show', f'{revision}:{path}'])


def checkout_pair(cache, destination, *, updated=(), central=False):
    """Copy the production plugin and display assets without release or test metadata."""
    destination.mkdir(parents=True)
    for name, revisions in VERSIONS.items():
        checkout = cache / f'weewx-{name}'
        rev = revisions[name in updated]
        for part in (f'bin/user/{name}.py', f'skins/{name}/index.html.tmpl', f'skins/{name}/skin.conf'):
            target = destination / part
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git_file(checkout, rev, part))
    if central:
        old_expression = 'round((x - 225.5) / 199.9 * 199.0 + 301.0)'
        shared = destination / 'bin/user/aqi_policy.py'
        shared.write_text(f'def hazardous_aqi(x):\n    return {old_expression}\n', encoding='utf-8')
        for name in VERSIONS:
            source = destination / f'bin/user/{name}.py'
            text = source.read_text(encoding='utf-8')
            if text.count(old_expression) != 1 or text.count('import math\n') != 1:
                raise ValueError(f'Unexpected upstream implementation in {name}')
            source.write_text(text.replace(old_expression, 'hazardous_aqi(x)').replace(
                'import math\n', 'import math\nfrom user.aqi_policy import hazardous_aqi\n'), encoding='utf-8')


def expected(concentration):
    pm = Decimal(concentration).quantize(Decimal('0.1'), rounding=ROUND_DOWN)
    for low, high, first, last, color in BREAKPOINTS:
        if pm <= Decimal(high) or last == 500:
            fraction = (pm - Decimal(low)) / (Decimal(high) - Decimal(low))
            aqi = int((first + fraction * (last - first)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            return {'scalar': aqi, 'historical_average': aqi, 'html_aqi': aqi, 'html_color': color}
    raise ValueError(concentration)


def observe(workspace, readings, html_dir=None):
    """Measure the actual XType, SQLite aggregation, and upstream Cheetah template."""
    import weewx
    from Cheetah.Template import Template
    from weeutil.weeutil import TimeSpan
    from configobj import ConfigObj

    sys.path.insert(0, str(workspace / 'bin'))
    output = {}
    for name in VERSIONS:
        aqi = import_module(f'user.{name}').AQI
        skin = workspace / 'skins' / name
        labels = ConfigObj(str(skin / 'skin.conf'))['Labels']['Generic']
        output[name] = {}
        for reading in readings:
            record = {'dateTime': BASE_TIME, 'usUnits': weewx.US, 'pm2_5': float(reading)}
            scalar = aqi.get_scalar('pm2_5_aqi', record).value
            color = aqi.get_scalar('pm2_5_aqi_color', record).value
            with closing(sqlite3.connect(':memory:')) as db:
                db.execute('CREATE TABLE archive (dateTime INTEGER PRIMARY KEY, usUnits INTEGER, pm2_5 REAL)')
                db.execute('INSERT INTO archive VALUES (?, ?, ?)', tuple(record.values()))
                manager = SimpleNamespace(table_name='archive', std_unit_system=weewx.US,
                                          getSql=lambda query: db.execute(query).fetchone())
                average = aqi.get_aggregate('pm2_5_aqi', TimeSpan(BASE_TIME - 60, BASE_TIME + 60),
                                            'avg', manager).value
                assert db.execute('SELECT pm2_5 FROM archive').fetchone() == (float(reading),)

            class ReportValue(int):
                has_data = True

            current = {'pm2_5_aqi': ReportValue(scalar), 'pm2_5_aqi_color': {'raw': color}}
            page = str(Template(file=str(skin / 'index.html.tmpl'),
                                searchList=[{'current': current, 'obs': {'label': labels}}]))
            match = re.search(r"<span style='color:(rgb\(\d+,\d+,\d+\));'>(\d+)</span>", page)
            if html_dir is not None:
                html_dir.mkdir(parents=True, exist_ok=True)
                (html_dir / f'{name}-{reading}.html').write_text(page, encoding='utf-8')
            output[name][reading] = {'scalar': scalar, 'historical_average': average,
                                     'html_aqi': int(match[2]) if match else None,
                                     'html_color': match[1] if match else None}
    return output


def score(observed):
    result = {}
    for name, rows in observed.items():
        result[name] = {}
        for key, actual in rows.items():
            oracle = expected(key)
            result[name][key] = {field: actual[field] == value for field, value in oracle.items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--html-dir', type=Path)
    parser.add_argument('--readings', nargs='+', default=INPUTS)
    options = parser.parse_args()
    print(json.dumps(observe(options.workspace.resolve(), options.readings, options.html_dir), indent=2))


if __name__ == '__main__':
    main()
