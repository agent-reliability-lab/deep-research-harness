"""Deterministic evaluator for synthetic integration fixtures."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from src.evidence import EvidenceRecord, EvidenceStore
from src.trace.models import (
    EvaluationEvent,
    EvaluationStatus,
    FinalReportEvent,
    ToolExecutionEvent,
    TraceEvent,
)

from .models import BenchmarkTask, EvaluationMode, RequiredClaim


def _contains_any(text: str, patterns: list[str]) -> bool:
    normalized = text.casefold()
    return any(pattern.casefold() in normalized for pattern in patterns)


def _record_supports_claim(record: EvidenceRecord, claim: RequiredClaim) -> bool:
    if record.source_id not in claim.acceptable_source_ids:
        return False
    searchable = f"{record.claim}\n{record.evidence_excerpt}"
    return _contains_any(searchable, claim.evidence_patterns)


def evaluate_fixture_run(
    *,
    task: BenchmarkTask,
    events: list[TraceEvent],
    evidence: EvidenceStore,
    sequence: int,
    timestamp: datetime,
    parent_event_id: UUID,
) -> EvaluationEvent:
    if task.evaluation_mode is not EvaluationMode.DETERMINISTIC_FIXTURE:
        raise ValueError("only deterministic_fixture tasks can use this evaluator")

    final_report = next(
        (event for event in reversed(events) if isinstance(event, FinalReportEvent)),
        None,
    )
    finalize_call = next(
        (
            event
            for event in reversed(events)
            if isinstance(event, ToolExecutionEvent)
            and event.tool_name == "finalize"
            and event.status.value == "success"
        ),
        None,
    )
    if final_report is None or finalize_call is None:
        raise ValueError("fixture evaluation requires a successful final report")

    summary = str(finalize_call.result["summary"])
    cited_records = [
        evidence.get(evidence_id) for evidence_id in final_report.cited_evidence_ids
    ]
    supported_claims = 0
    for claim in task.required_claims:
        answer_present = _contains_any(summary, claim.answer_patterns)
        evidence_present = any(
            _record_supports_claim(record, claim) for record in cited_records
        )
        supported_claims += answer_present and evidence_present

    supported_citations = sum(
        any(_record_supports_claim(record, claim) for claim in task.required_claims)
        for record in cited_records
    )
    citations_total = len(cited_records)
    unique_sources = len({record.source_id for record in cited_records})
    distractor_mentions = sum(
        distractor.casefold() in summary.casefold()
        for distractor in task.known_distractors
    )

    coverage = supported_claims / len(task.required_claims)
    precision = supported_citations / citations_total if citations_total else 0.0
    entailment = precision
    citation_shape_passed = (
        citations_total >= task.citation_expectations.minimum_citations
        and unique_sources >= task.citation_expectations.minimum_unique_sources
    )
    factual_correctness = (
        coverage >= task.rubric.minimum_required_claim_coverage
        and distractor_mentions <= task.rubric.maximum_distractor_mentions
    )
    task_success = (
        factual_correctness
        and precision >= task.rubric.minimum_citation_precision
        and entailment >= task.rubric.minimum_citation_entailment
        and citation_shape_passed
        and final_report.produced_within_budget
    )

    return EvaluationEvent(
        run_id=events[0].run_id,
        sequence=sequence,
        timestamp=timestamp,
        parent_event_id=parent_event_id,
        status=EvaluationStatus.EVAL_VALID,
        included_in_egtsr_denominator=True,
        task_success=task_success,
        required_claims_total=len(task.required_claims),
        supported_required_claims=supported_claims,
        citations_total=citations_total,
        supported_citations=supported_citations,
        entailed_citations=supported_citations,
        factual_correctness_passed=factual_correctness,
        critical_policy_violations=0,
        final_artifact_within_budget=final_report.produced_within_budget,
        unsupported_claim_count=distractor_mentions,
    )
