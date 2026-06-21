"""Validate traces and reproduce metrics from the command line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .metrics import aggregate_run_metrics, compute_run_metrics
from .validate import validate_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate one trace")
    validate_parser.add_argument("trace", type=Path)
    validate_parser.add_argument("--evidence", type=Path)

    metrics_parser = subparsers.add_parser(
        "metrics",
        help="compute run metrics and aggregate EGTSR/cost",
    )
    metrics_parser.add_argument("traces", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        events = validate_trace(args.trace, args.evidence)
        print(
            json.dumps(
                {
                    "valid": True,
                    "run_id": str(events[0].run_id),
                    "events": len(events),
                },
                indent=2,
            )
        )
        return 0

    run_metrics = []
    for path in args.traces:
        evidence_path = path.with_name("evidence.jsonl")
        if not evidence_path.exists():
            raise SystemExit(f"missing evidence store beside trace: {evidence_path}")
        events = validate_trace(path, evidence_path)
        run_metrics.append(compute_run_metrics(events))
    aggregate = aggregate_run_metrics(run_metrics)
    print(
        json.dumps(
            {
                "runs": [metric.model_dump(mode="json") for metric in run_metrics],
                "aggregate": aggregate.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
