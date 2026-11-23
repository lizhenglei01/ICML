"""Test native workbook formulas on the current data and altered ledger inputs."""
from math import isclose, isfinite
from numbers import Real
from pathlib import Path
from shutil import which
from subprocess import TimeoutExpired, run
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.properties import CalcProperties


METHOD = 'LibreOffice Calc in an isolated temporary workspace'
WORKBOOK_ERRORS = (BadZipFile, InvalidFileException, XMLSyntaxError, OSError, ValueError, KeyError, TypeError)


def _numeric(value):
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)


def _text_literal(value):
    return (isinstance(value, str) and 0 < len(value) <= 32767 and value == value.strip()
            and value.isprintable() and value[0] not in '=+-@')


def _json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    return float(value) if _numeric(value) else str(value)


def _matches(actual, target):
    # Calc can serialize an Excel numeric comparison such as --AND(...) as a
    # Boolean cache. The functional value is the same for 0/1 flag targets.
    if isinstance(actual, bool):
        return target in (0, 1) and int(actual) == target
    return _numeric(actual) and isclose(actual, target, rel_tol=1e-9, abs_tol=1e-9)


def _validate_scenarios(scenarios, allow_text_inputs, *, require_sensitivity=True):
    cases = [(row['name'], row.get('inputs', {}), row['expected']) for row in scenarios]
    if len({name for name, _, _ in cases}) != len(cases):
        raise ValueError('Formula scenarios need unique names')
    baselines = [expected for _, inputs, expected in cases if not inputs]
    if len(baselines) != 1 or not baselines[0]:
        raise ValueError('Provide exactly one nonempty current-data scenario')
    baseline = baselines[0]
    challenged = set()
    for _, inputs, expected in cases:
        if not expected or expected.keys() - baseline.keys() or inputs.keys() & expected.keys():
            raise ValueError('Each scenario must name independent expected formula cells and disjoint input cells')
        if any(not _numeric(value) for value in expected.values()):
            raise ValueError('Expected formula values must be finite numbers')
        if any(not _numeric(value) and not (allow_text_inputs and _text_literal(value)) for value in inputs.values()):
            raise ValueError('Formula inputs must be finite numbers or enabled safe text literals')
        for sheet, coordinate in (*inputs, *expected):
            if not sheet:
                raise ValueError('Formula scenario addresses need a worksheet name')
            row, column = coordinate_to_tuple(coordinate)
            if not 1 <= row <= 1048576 or not 1 <= column <= 16384:
                raise ValueError(f'Invalid Excel coordinate {coordinate!r}')
        if inputs:
            challenged.update(address for address, value in expected.items() if value != baseline[address])
    if require_sensitivity and challenged != baseline.keys():
        raise ValueError('Each formula target needs an input probe with an expected result different from the current one')
    return cases


def _recalculate(work, names, timeout):
    bwrap = which('bwrap')
    office = which('libreoffice')
    if not bwrap or not office:
        raise RuntimeError('Formula evaluation requires bubblewrap and LibreOffice Calc')
    command = [
        bwrap, '--die-with-parent', '--unshare-all', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--bind', str(work), '/work', '--chdir', '/work',
        '--clearenv', '--setenv', 'HOME', '/work', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LANG', 'C.UTF-8',
    ]
    for path in ('/etc/libreoffice', '/etc/fonts'):
        if Path(path).is_dir():
            command.extend(('--ro-bind', path, path))
    command.extend((
        office, '-env:UserInstallation=file:///work/profile', '--headless', '--norestore',
        '--nodefault', '--nofirststartwizard', '--convert-to', 'xlsx:Calc MS Excel 2007 XML',
        '--outdir', '/work/output', *(f'/work/input/{name}' for name in names),
    ))
    result = run(command, capture_output=True, text=True, timeout=timeout, check=False)
    absent = [name for name in names if not (work / 'output' / name).is_file()]
    if result.returncode or absent:
        detail = (result.stderr.strip() or result.stdout.strip())[:1500]
        raise RuntimeError(f'Calc returned {result.returncode}, missing {absent}. {detail}')


def _prepare(source, destination, inputs, expected):
    workbook = load_workbook(source, keep_links=False)
    try:
        mutations = []
        for (sheet, address), value in inputs.items():
            cell = workbook[sheet][address] if sheet in workbook else None
            original = cell.value if cell is not None else None
            if isinstance(value, str):
                eligible = cell is not None and cell.data_type == 's' and _text_literal(original)
            else:
                eligible = _numeric(original) or (cell is not None and cell.data_type == 'f')
            changed = eligible and original != value
            mutations.append({
                'sheet': sheet, 'cell': address, 'before': _json_value(original),
                'after': _json_value(value), 'eligible': eligible, 'changed': changed,
            })
            if changed:
                cell.value = _json_value(value)
                if isinstance(value, str):
                    cell.data_type = 's'
        formulas = []
        for sheet, address in expected:
            cell = workbook[sheet][address] if sheet in workbook else None
            value = cell.value if cell is not None and cell.data_type == 'f' else None
            formulas.append(value if isinstance(value, str) else getattr(value, 'text', None))
        workbook.calculation = CalcProperties(calcMode='auto', fullCalcOnLoad=True, forceFullCalc=True)
        workbook.save(destination)
    finally:
        workbook.close()
    return mutations, formulas


def verify_formula_function(workbook_file, scenarios, *, timeout=30, allow_text_inputs=False, require_sensitivity=True):
    """Return formula presence and recalculated values for independent test scenarios.

    Each scenario gives a name, an `expected` mapping from (sheet, cell) to a
    numeric value, and optional `inputs` mapping of numeric changes to ledger
    values or chosen intermediate formula cells.
    Set `allow_text_inputs` to exercise native text input fields with safe
    literal hotel names and operation modes.
    Include the current data and one or more probes that change every tested
    formula's expected result across the suite. Only copies are changed.
    """
    cases = _validate_scenarios(scenarios, allow_text_inputs, require_sensitivity=require_sensitivity)
    report = {'pass': False, 'status': 'checked', 'method': METHOD, 'scenarios': []}
    workbook_file = Path(workbook_file)
    with TemporaryDirectory(prefix='milestone_calculation_') as temporary:
        work = Path(temporary)
        (work / 'input').mkdir()
        (work / 'output').mkdir()
        names = [f'scenario_{index:02d}.xlsx' for index in range(len(cases))]
        try:
            prepared = [_prepare(workbook_file, work / 'input' / key, inputs, expected)
                        for key, (_, inputs, expected) in zip(names, cases)]
        except WORKBOOK_ERRORS as exc:
            report.update(status='invalid_workbook', error=f'{type(exc).__name__}: {exc}')
            return report
        try:
            _recalculate(work, names, timeout)
        except (OSError, RuntimeError, TimeoutExpired) as exc:
            report.update(status='recalculation_failed', error=f'{type(exc).__name__}: {exc}')
            return report
        try:
            for key, (name, inputs, expected), (mutations, formulas) in zip(names, cases, prepared):
                workbook = load_workbook(work / 'output' / key, read_only=True, data_only=True)
                try:
                    for mutation, ((sheet, address), target) in zip(mutations, inputs.items()):
                        actual = workbook[sheet][address].value if sheet in workbook else None
                        mutation['observed'] = _json_value(actual)
                        landed = (isinstance(actual, str) and actual == target) if isinstance(target, str) else (
                            _numeric(actual) and _matches(actual, target))
                        mutation['pass'] = mutation['eligible'] and landed
                    checks = []
                    for ((sheet, address), target), formula in zip(expected.items(), formulas):
                        actual = workbook[sheet][address].value if sheet in workbook else None
                        checks.append({
                            'sheet': sheet, 'cell': address, 'formula': formula,
                            'expected': _json_value(target), 'observed': _json_value(actual),
                            'pass': bool(formula) and _matches(actual, target),
                        })
                finally:
                    workbook.close()
                mutation_pass = not inputs or (all(row['pass'] for row in mutations)
                                                and any(row['changed'] for row in mutations))
                report['scenarios'].append({'name': name, 'mutations': mutations, 'checks': checks,
                                            'pass': mutation_pass and all(row['pass'] for row in checks)})
        except WORKBOOK_ERRORS as exc:
            report.update(status='invalid_recalculated_workbook', error=f'{type(exc).__name__}: {exc}')
            return report
    report['pass'] = all(row['pass'] for row in report['scenarios'])
    return report
