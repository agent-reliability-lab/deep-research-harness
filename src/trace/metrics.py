"""Metric extraction from validated traces."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field
from pydantic.main import BaseModel

from .models import (
    CompactionEvent,
    EvaluationEvent,
    ModelCallEvent,
    PermissionDecisionEvent,
    PermissionVerdict,
    RecoveryEvent,
    SubagentHandoffEvent,
    ToolExecutionEvent,
    TraceEvent,
)
from .validate import validate_trace


class MetricsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunMetrics(MetricsModel):
    run_id: UUID
    included_in_egtsr_denominator: bool
    task_success: bool | None
    model_cost_usd: Decimal = Field(ge=0)
    tool_cost_usd: Decimal = Field(ge=0)
    total_cost_usd: Decimal = Field(ge=0)
    cost_per_success_usd: Decimal | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(ge=0)
    peak_active_context_tokens: int = Field(ge=0)
    citation_precision: float | None
    citation_entailment_rate: float | None
    required_claim_coverage: float | None
    recovery_success: bool | None
    compaction_ratio: float | None
    critical_fact_retention: float | None
    approval_requests: int = Field(ge=0)
    permission_over_blocks: int = Field(ge=0)
    permission_under_blocks: int = Field(ge=0)
    subagent_handoff_bytes: int = Field(ge=0)
    duplicated_source_ids: int = Field(ge=0)
    tool_call_errors: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)


class AggregateMetrics(MetricsModel):
    runs: int = Field(ge=0)
    eval_valid_runs: int = Field(ge=0)
    successful_runs: int = Field(ge=0)
    egtsr: float | None
    total_eval_valid_cost_usd: Decimal = Field(ge=0)
    cost_per_success_usd: Decimal | None
    peak_active_context_tokens: int = Field(ge=0)
    recovery_success_rate: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def compute_run_metrics(events: list[TraceEvent]) -> RunMetrics:
    events = validate_trace(events)
    run_id = events[0].run_id
    model_calls = [event for event in events if isinstance(event, ModelCallEvent)]
    tool_calls = [event for event in events if isinstance(event, ToolExecutionEvent)]
    evaluation = next(
        (event for event in events if isinstance(event, EvaluationEvent)),
        None,
    )
    if evaluation is None:
        raise ValueError("trace has no evaluation event")

    model_cost = sum(
        (event.cost.total_usd for event in model_calls),
        start=Decimal("0"),
    )
    tool_cost = sum(
        (event.cost_usd for event in tool_calls),
        start=Decimal("0"),
    )
    total_cost = model_cost + tool_cost
    usages = [event.usage for event in model_calls if event.usage is not None]
    source_ids = [source_id for event in tool_calls for source_id in event.source_ids]
    recoveries = [event for event in events if isinstance(event, RecoveryEvent)]
    compactions = [event for event in events if isinstance(event, CompactionEvent)]
    permissions = [event for event in events if isinstance(event, PermissionDecisionEvent)]
    handoffs = [event for event in events if isinstance(event, SubagentHandoffEvent)]

    latest_compaction = compactions[-1] if compactions else None
    retention = None
    compaction_ratio = None
    if latest_compaction:
        compaction_ratio = _ratio(
            latest_compaction.output_tokens,
            latest_compaction.input_tokens,
        )
        retention = _ratio(
            len(
                set(latest_compaction.preserved_fact_ids) & set(latest_compaction.required_fact_ids)
            ),
            len(set(latest_compaction.required_fact_ids)),
        )

    recovery_success = None
    if recoveries:
        recovery_success = all(
            event.restored
            and event.repeated_gated_actions == 0
            and event.completed_within_remaining_budget
            for event in recoveries
        )

    return RunMetrics(
        run_id=run_id,
        included_in_egtsr_denominator=evaluation.included_in_egtsr_denominator,
        task_success=evaluation.task_success,
        model_cost_usd=model_cost,
        tool_cost_usd=tool_cost,
        total_cost_usd=total_cost,
        cost_per_success_usd=(
            total_cost
            if evaluation.included_in_egtsr_denominator and evaluation.task_success
            else None
        ),
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        cache_hit_tokens=sum(usage.cache_hit_tokens for usage in usages),
        peak_active_context_tokens=max(
            (usage.input_tokens for usage in usages),
            default=0,
        ),
        citation_precision=_ratio(
            evaluation.supported_citations,
            evaluation.citations_total,
        ),
        citation_entailment_rate=_ratio(
            evaluation.entailed_citations,
            evaluation.citations_total,
        ),
        required_claim_coverage=_ratio(
            evaluation.supported_required_claims,
            evaluation.required_claims_total,
        ),
        recovery_success=recovery_success,
        compaction_ratio=compaction_ratio,
        critical_fact_retention=retention,
        approval_requests=sum(event.policy_action.value == "ask" for event in permissions),
        permission_over_blocks=sum(
            event.verdict is PermissionVerdict.OVER_BLOCK for event in permissions
        ),
        permission_under_blocks=sum(
            event.verdict is PermissionVerdict.UNDER_BLOCK for event in permissions
        ),
        subagent_handoff_bytes=sum(event.serialized_bytes for event in handoffs),
        duplicated_source_ids=len(source_ids) - len(set(source_ids)),
        tool_call_errors=sum(event.status.value == "error" for event in tool_calls),
        unsupported_claim_count=evaluation.unsupported_claim_count,
    )


def aggregate_run_metrics(metrics: Iterable[RunMetrics]) -> AggregateMetrics:
    metrics = list(metrics)
    denominator = [item for item in metrics if item.included_in_egtsr_denominator]
    successes = [item for item in denominator if item.task_success]
    total_cost = sum(
        (item.total_cost_usd for item in denominator),
        start=Decimal("0"),
    )
    recovery_results = [
        item.recovery_success for item in denominator if item.recovery_success is not None
    ]
    return AggregateMetrics(
        runs=len(metrics),
        eval_valid_runs=len(denominator),
        successful_runs=len(successes),
        egtsr=_ratio(len(successes), len(denominator)),
        total_eval_valid_cost_usd=total_cost,
        cost_per_success_usd=(total_cost / len(successes) if successes else None),
        peak_active_context_tokens=max(
            (item.peak_active_context_tokens for item in metrics),
            default=0,
        ),
        recovery_success_rate=_ratio(
            sum(bool(item) for item in recovery_results),
            len(recovery_results),
        ),
    )
