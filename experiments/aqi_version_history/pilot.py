"""Run paired, fresh-session edits of concentrated and distributed AQI rules."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from multifile_reconstruction.reasoning_graph_mvp.logic_first.hotel_agent import (
    event_summary, isolated_env, model_command, sandbox_arguments,
)
from research_archive import digest, write
from .replay import checkout_pair, score


TASK = '''# Correct PM2.5 AQI reporting

This workstation publishes air-quality reports for stations using PurpleAir and
AirGradient. Correct the US EPA PM2.5 AQI mapping, including every affected report
path. The Hazardous band starts at 225.5 µg/m³ and AQI 301. The concentration at
AQI 500 is 325.4 µg/m³. Use the same linear slope for concentrations above that
anchor. The already-supported lower bands, concentration truncation to one decimal
and integer display should otherwise continue to work. Keep the category colors,
the stored PM2.5 concentrations, and the existing report and data interfaces.

Make the edits in this workspace. Checks run on the updated source and render the
existing reports on different input readings. The existing WeeWX and Cheetah
packages are available by adding /mnt/weewx to the Python import path.
'''
REPLAY = Path(__file__).with_name('replay.py').resolve()


def snapshot(folder):
    return {path.relative_to(folder).as_posix(): digest(path) for path in folder.rglob('*')
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts}


def run_one(item, cache, run, dependencies, model):
    variant, repeat = item
    folder = run / f'{variant}-{repeat}'
    workspace, completion = folder / 'workspace', folder / 'completion'
    checkout_pair(cache, workspace, central=variant == 'concentrated')
    (workspace / 'instruction.md').write_text(TASK, encoding='utf-8')
    completion.mkdir()
    initial = snapshot(workspace)
    codex = Path(shutil.which('codex')).resolve()
    env = isolated_env()
    command = model_command(model, 'medium')
    prompt = 'Read instruction.md and complete the maintenance task in the files in this workspace.\n'
    started = datetime.now(timezone.utc).isoformat()
    start = monotonic()
    with TemporaryDirectory(prefix='icml_aqi_auth_') as auth:
        home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        shutil.copyfile(home / 'auth.json', Path(auth) / 'auth.json')
        mounts = sandbox_arguments(workspace, completion, Path(auth), codex)
        mounts += ['--ro-bind', str(dependencies), '/mnt/weewx']
        preflight = ['python', '-c', 'from pathlib import Path; import sys; '
                     'sys.path.insert(0,"/mnt/weewx"); import weewx, Cheetah; '
                     'assert not Path(sys.argv[1]).exists(); assert not Path(sys.argv[2]).exists(); '
                     'assert Path("instruction.md").is_file()', str(cache), str(REPLAY)]
        subprocess.run(mounts + preflight, env=env, check=True, timeout=30)
        events = folder / 'events.jsonl'
        print('START', folder.name, started, flush=True)
        with events.open('w', encoding='utf-8') as out, (folder / 'stderr.log').open('w', encoding='utf-8') as err:
            result = subprocess.run(mounts + command, env=env, input=prompt, text=True,
                                    stdout=out, stderr=err, timeout=900, check=False)

    elapsed = round(monotonic() - start, 3)
    trace = event_summary(events)
    final = snapshot(workspace)
    changed = sorted(name for name in initial.keys() | final.keys() if initial.get(name) != final.get(name))
    report = {'variant': variant, 'repeat': repeat, 'model': model, 'wall_seconds': elapsed,
              'started_utc': started, 'input_sha256': initial, 'output_sha256': final,
              'exit_code': result.returncode, 'changed': changed, 'usage': trace['usage'],
              'tool_calls': len(trace['tool_events']), 'thread_ids': trace['thread_ids']}
    if result.returncode == 0 and trace['usage']:
        with TemporaryDirectory(prefix='icml_aqi_verify_') as empty_auth:
            mounts = sandbox_arguments(workspace, completion, Path(empty_auth), codex)
            mounts += ['--ro-bind', str(dependencies), '/mnt/weewx', '--ro-bind', str(REPLAY), '/mnt/replay.py',
                       '--unshare-net', '--setenv', 'PYTHONPATH', '/mnt/weewx']
            observed = subprocess.run(mounts + ['python', '/mnt/replay.py', '/mnt/workspace'],
                                      env=env, capture_output=True, text=True, timeout=90, check=False)
        if observed.returncode == 0:
            values = json.loads(observed.stdout)
            report['checks'] = score(values)
            write(folder / 'observed.json', values)
        else:
            (folder / 'scoring_error.log').write_text(observed.stderr, encoding='utf-8')
    write(folder / 'result.json', report)
    print('FINISH', folder.name, elapsed, changed, flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_cache', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('--model', default='gpt-6.2-sol')
    args = parser.parse_args()
    cache, run = args.source_cache.resolve(), args.run.resolve()
    dependencies, = (cache / 'venv/lib').glob('python*/site-packages')
    run.mkdir(parents=True)
    trials = [('concentrated', 1), ('distributed', 1), ('distributed', 2), ('concentrated', 2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(run_one, item, cache, run, dependencies, args.model) for item in trials]
        outcomes = [job.result() for job in jobs]
    write(run / 'results.json', outcomes)


if __name__ == '__main__':
    main()
