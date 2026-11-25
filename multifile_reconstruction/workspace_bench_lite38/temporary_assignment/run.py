"""Run one isolated Lite 38 milestone from a recorded input workbook."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from research_archive import digest, read, write
from ...reasoning_graph_mvp.logic_first.hotel_agent import (
    event_summary, isolated_check, isolated_env, model_command, prepare_workspace, sandbox_arguments,
)


TASK = Path(__file__).with_name('task.md')
MODEL = 'gpt-6.2-sol'
EFFORT = 'medium'
INPUT = 'employee_information_management.xlsx'
FILES = (INPUT, 'instruction.md')


def verify_suite(freeze_path):
    suite = read(freeze_path)
    for filename, expected in suite['frozen_files'].items():
        if digest(Path(filename)) != expected:
            raise ValueError(f'Frozen evidence changed: {filename}')
    return suite


def run(branch, freeze_path, run_dir):
    suite = verify_suite(freeze_path)
    origin = Path(suite['starts'][branch]['path'])
    run_dir.mkdir(parents=True)
    with TemporaryDirectory(prefix='lite38_m2_input_') as temp:
        staged = Path(temp)
        shutil.copyfile(origin, staged / INPUT)
        shutil.copyfile(TASK, staged / 'instruction.md')
        workspace = run_dir / 'workspace'
        prepare_workspace(staged, workspace, FILES)

    output = run_dir / 'completion'
    output.mkdir()
    prompt = ('Read instruction.md and upgrade the supplied Excel workbook. Save the final workbook as '
              'submission.xlsx in this directory. The workbook is the deliverable.\n')
    (run_dir / 'prompt.txt').write_text(prompt, encoding='utf-8')
    codex = shutil.which('codex')
    if codex is None:
        raise FileNotFoundError('codex')
    auth_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    env = isolated_env()
    with TemporaryDirectory(prefix='lite38_m2_auth_') as temp:
        auth = Path(temp)
        shutil.copy2(auth_home / 'auth.json', auth / 'auth.json')
        mounts = sandbox_arguments(workspace, output, auth, Path(codex).resolve())
        probe = isolated_check(mounts, env, origin, freeze_path, FILES)
        started = datetime.now(timezone.utc).isoformat()
        start = monotonic()
        print('MODEL_STARTED', branch, started, flush=True)
        events = run_dir / 'events.jsonl'
        with events.open('w', encoding='utf-8') as stdout, (run_dir / 'stderr.log').open('w', encoding='utf-8') as stderr:
            result = subprocess.run(mounts + model_command(MODEL, EFFORT), input=prompt, text=True,
                                    stdout=stdout, stderr=stderr, env=env)
        elapsed = monotonic() - start
    verify_suite(freeze_path)
    trace = event_summary(events)
    submission = workspace / 'submission.xlsx'
    delivered = submission.is_file() and not submission.is_symlink()
    report = {'branch': branch, 'started_utc': started, 'wall_seconds': round(elapsed, 3),
              'model': MODEL, 'effort': EFFORT, 'origin': suite['starts'][branch],
              'cli_exit_code': result.returncode, 'isolation_check': probe, 'trace': trace,
              'submission_sha256': digest(submission) if delivered else None,
              'status': 'completed' if result.returncode == 0 and trace['usage'] and delivered else 'incomplete',
              'freeze_sha256': digest(freeze_path)}
    write(run_dir / 'run.json', report)
    print('MODEL_FINISHED', branch, report['status'], round(elapsed), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('branch', choices=('first_m1', 'second_m1'))
    parser.add_argument('freeze', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.branch, args.freeze.resolve(), args.output.resolve())
