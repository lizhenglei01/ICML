"""Command-line entry points for the initial HR operations system."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .hr_workflow import approve_departure, approve_hiring, issue_hiring, onboard, publish_workspace, submit_departure
from .workspace import build_workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "approve-request", "decide", "issue", "onboard", "submit-departure", "approve-departure", "publish"])
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--id")
    parser.add_argument("--decision", choices=["Approved", "Rejected"])
    parser.add_argument("--employee-id")
    parser.add_argument("--name")
    parser.add_argument("--date")
    args = parser.parse_args()
    if args.command == "build":
        build_workspace(args.workspace)
    elif args.command == "approve-request":
        approve_hiring(args.workspace / "HR_Operations.xlsx", args.id)
    elif args.command == "decide":
        from .hr_workflow import set_hiring_decision
        set_hiring_decision(args.workspace / "HR_Operations.xlsx", args.id, args.decision)
    elif args.command == "issue":
        issue_hiring(args.workspace / "HR_Operations.xlsx", args.id, date.fromisoformat(args.date))
    elif args.command == "onboard":
        onboard(args.workspace / "HR_Operations.xlsx", args.id, args.employee_id, args.name, date.fromisoformat(args.date))
    elif args.command == "submit-departure":
        submit_departure(args.workspace / "HR_Operations.xlsx", args.id)
    elif args.command == "approve-departure":
        approve_departure(args.workspace / "HR_Operations.xlsx", args.id)
    else:
        snapshot = publish_workspace(args.workspace, date.fromisoformat(args.date))
        print(snapshot)


if __name__ == "__main__":
    main()
