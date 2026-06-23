"""Run the C0 baseline against an offline fixture or official DeepSeek API."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from src.snapshots import SnapshotCorpus
from src.tasks.load import load_task
from src.trace.metrics import aggregate_run_metrics
from src.trace.models import EvaluationStatus, RunBudget

from .c1 import C1Runner
from .compaction import load_compaction_config
from .deepseek import DeepSeekProvider, load_pricing
from .fixture import FixtureProvider
from .runner import C0Runner

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TASK = REPO_ROOT / "data" / "fixtures" / "tasks" / "mem0-architecture.json"
FIXTURE_MANIFEST = (
    REPO_ROOT / "data" / "fixtures" / "source_snapshots" / "manifest.json"
)


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    task_default: Path | None = None,
    manifest_default: Path | None = None,
) -> None:
    parser.add_argument(
        "--task",
        type=Path,
        default=task_default,
        required=task_default is None,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=manifest_default,
        required=manifest_default is None,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-group-id", default="c0-development")
    parser.add_argument(
        "--configuration",
        choices=("C0", "C1"),
        default="C0",
    )
    parser.add_argument(
        "--compaction-config",
        type=Path,
        default=REPO_ROOT / "configs" / "compaction.yaml",
    )
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-model-calls", type=int, default=10)
    parser.add_argument("--max-tool-calls", type=int, default=20)
    parser.add_argument(
        "--max-active-context-tokens",
        type=int,
        default=100_000,
        help="maximum input tokens in any single model call",
    )
    parser.add_argument(
        "--max-uncached-input-tokens",
        type=int,
        default=100_000,
        help="maximum cumulative input tokens not served from provider cache",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--max-output-tokens", type=int, default=20_000)
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal("5"))
    parser.add_argument("--max-duration-ms", type=int, default=600_000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="provider", required=True)

    fixture = subparsers.add_parser(
        "fixture",
        help="run the committed synthetic fixture without network access",
    )
    _add_common_arguments(
        fixture,
        task_default=FIXTURE_TASK,
        manifest_default=FIXTURE_MANIFEST,
    )

    deepseek = subparsers.add_parser(
        "deepseek",
        help="run through the official DeepSeek API",
    )
    _add_common_arguments(deepseek)
    deepseek.add_argument("--pricing-file", type=Path, required=True)
    deepseek.add_argument("--model", default=None)
    deepseek.add_argument("--temperature", type=float, default=0)
    deepseek.add_argument("--model-max-tokens", type=int, default=4096)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = load_task(args.task)
    corpus = SnapshotCorpus(args.manifest)
    corpus.verify_all()
    budget = RunBudget(
        max_model_calls=args.max_model_calls,
        max_tool_calls=args.max_tool_calls,
        max_active_context_tokens=args.max_active_context_tokens,
        max_uncached_input_tokens=args.max_uncached_input_tokens,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_cost_usd=args.max_cost_usd,
        max_duration_ms=args.max_duration_ms,
    )
    if args.provider == "fixture":
        provider = FixtureProvider()
    else:
        kwargs = {
            "pricing": load_pricing(args.pricing_file),
            "temperature": args.temperature,
            "max_tokens": args.model_max_tokens,
        }
        if args.model:
            kwargs["model"] = args.model
        provider = DeepSeekProvider(**kwargs)

    runner_kwargs = {}
    runner_class = C0Runner
    if args.configuration == "C1":
        runner_class = C1Runner
        runner_kwargs["compaction_config"] = load_compaction_config(
            args.compaction_config
        )

    outcome = runner_class(
        task=task,
        corpus=corpus,
        provider=provider,
        budget=budget,
        max_iterations=args.max_iterations,
        output_dir=args.output,
        run_group_id=args.run_group_id,
        **runner_kwargs,
    ).run()
    aggregate = aggregate_run_metrics([outcome.metrics])
    print(
        json.dumps(
            {
                "run_id": str(outcome.run_id),
                "configuration": args.configuration,
                "status": outcome.status,
                "failure_label": outcome.failure_label,
                "fixture_only": task.fixture_only,
                "eligible_for_primary_egtsr": (
                    outcome.metrics.evaluation_scope.value == "primary"
                ),
                "trace": str(outcome.trace_path),
                "evidence": str(outcome.evidence_path),
                "report": str(outcome.report_path) if outcome.report_path else None,
                "metrics": outcome.metrics.model_dump(mode="json"),
                "aggregate": aggregate.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    if outcome.status is EvaluationStatus.INFRA_API_FAILED:
        return 2
    if outcome.status is EvaluationStatus.AGENT_FAILED:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
