"""Standalone evaluator using a saved oracle, with no regeneration step."""
import argparse
import json
from pathlib import Path

from .oracle import evaluate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--submission", type=Path, required=True)
    p.add_argument("--oracle", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    result = evaluate(args.submission, args.oracle)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print("PASS" if result["pass"] else "FAIL", result.get("summary", {}))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
