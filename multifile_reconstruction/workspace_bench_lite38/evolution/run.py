"""Launch a frozen Lite 38 evolution milestone using the established isolated runner."""

import argparse
from pathlib import Path

from ..temporary_assignment import run as isolated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('m3', 'm4', 'm5', 'm6', 'm7'))
    parser.add_argument('branch', choices=('first_m1', 'second_m1'))
    parser.add_argument('freeze', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    freeze = args.freeze.resolve()
    suite = isolated.verify_suite(freeze)
    task = Path(__file__).with_name(f'{args.stage}.md').resolve()
    if suite['stage'] != args.stage or str(task) not in suite['frozen_files']:
        raise ValueError('Stage does not match the frozen task')
    isolated.TASK = task
    isolated.run(args.branch, freeze, args.output.resolve())


if __name__ == '__main__':
    main()
