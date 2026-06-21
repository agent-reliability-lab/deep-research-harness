"""Trace/evidence storage, invariants, and metric coverage."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

from src.evidence import EvidenceRecord, EvidenceStore
from src.evidence.store import EvidenceStoreError
from src.trace.cli import main as trace_cli
from src.trace.metrics import aggregate_run_metrics, compute_run_metrics
from src.trace.models import (
    CallCost,
    CallStatus,
    ChatMessage,
    CheckpointEvent,
    CheckpointState,
    Configuration,
    EvaluationEvent,
    EvaluationScope,
    EvaluationStatus,
    EvidenceRecordedEvent,
    FinalReportEvent,
    ModelCallEvent,
    ModelUsage,
    RecoveryEvent,
    RunBudget,
    RunEndedEvent,
    RunStartedEvent,
    ToolCallRequest,
    ToolExecutionEvent,
)
from src.trace.store import TraceStoreError, TraceWriter
from src.trace.validate import TraceValidationError, validate_trace

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def run_started(run_id: UUID, sequence: int = 0) -> RunStartedEvent:
    return RunStartedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=NOW,
        run_group_id="primary-2026-06",
        task_id="architecture-01",
        configuration=Configuration.C0,
        provider="deepseek",
        endpoint_class="openai-compatible",
        requested_model="deepseek-v4-flash",
        model_parameters={"temperature": 0},
        source_snapshot_id="memory-systems-2026-06-21",
        pricing_version="deepseek-2026-06-21",
        budget=RunBudget(
            max_model_calls=20,
            max_tool_calls=40,
            max_input_tokens=100_000,
            max_output_tokens=20_000,
            max_cost_usd=Decimal("5"),
            max_duration_ms=600_000,
        ),
    )


def successful_trace(run_id: UUID, evidence_id: UUID) -> list:
    start = run_started(run_id)
    model_call = ModelCallEvent(
        run_id=run_id,
        sequence=1,
        timestamp=NOW,
        parent_event_id=start.event_id,
        call_id="model-1",
        status=CallStatus.SUCCESS,
        requested_model="deepseek-v4-flash",
        returned_model="deepseek-v4-flash",
        system_fingerprint="fp_test",
        provider_request_id="req_1",
        request_messages=[ChatMessage(role="user", content="Research Mem0.")],
        response_content="Use search.",
        response_tool_calls=[
            ToolCallRequest(
                id="tool-1",
                name="read_source",
                arguments={"source_id": "src_mem0"},
            )
        ],
        usage=ModelUsage(
            input_tokens=1000,
            output_tokens=100,
            cache_hit_tokens=512,
            cache_miss_tokens=488,
        ),
        cost=CallCost(
            input_usd=Decimal("0.001"),
            output_usd=Decimal("0.002"),
        ),
        latency_ms=500,
    )
    tool = ToolExecutionEvent(
        run_id=run_id,
        sequence=2,
        timestamp=NOW,
        parent_event_id=model_call.event_id,
        tool_call_id="tool-1",
        tool_name="read_source",
        status=CallStatus.SUCCESS,
        arguments={"source_id": "src_mem0"},
        result={"text": "Mem0 stores memories."},
        latency_ms=20,
        cost_usd=Decimal("0.0001"),
        source_ids=["src_mem0"],
    )
    evidence = EvidenceRecordedEvent(
        run_id=run_id,
        sequence=3,
        timestamp=NOW,
        parent_event_id=tool.event_id,
        evidence_id=evidence_id,
        source_id="src_mem0",
    )
    checkpoint = CheckpointEvent(
        run_id=run_id,
        sequence=4,
        timestamp=NOW,
        checkpoint_id=uuid4(),
        state_hash=HASH,
        state=CheckpointState(
            plan=["search", "read", "write"],
            completed_steps=["search", "read"],
            unresolved_questions=[],
            evidence_ids=[evidence_id],
            source_to_claim={"src_mem0": ["Mem0 stores memories."]},
            user_constraints=["cite sources"],
            permission_decision_event_ids=[],
            failures=[],
            retry_state={},
        ),
    )
    recovery = RecoveryEvent(
        run_id=run_id,
        sequence=5,
        timestamp=NOW,
        checkpoint_id=checkpoint.checkpoint_id,
        restored=True,
        repeated_gated_actions=0,
        completed_within_remaining_budget=True,
    )
    final_report = FinalReportEvent(
        run_id=run_id,
        sequence=6,
        timestamp=NOW,
        artifact_path="results/raw/report.md",
        content_hash=HASH,
        cited_evidence_ids=[evidence_id],
        produced_within_budget=True,
    )
    evaluation = EvaluationEvent(
        run_id=run_id,
        sequence=7,
        timestamp=NOW,
        status=EvaluationStatus.EVAL_VALID,
        included_in_egtsr_denominator=True,
        task_success=True,
        required_claims_total=2,
        supported_required_claims=2,
        citations_total=2,
        supported_citations=2,
        entailed_citations=2,
        factual_correctness_passed=True,
        critical_policy_violations=0,
        final_artifact_within_budget=True,
        unsupported_claim_count=0,
    )
    ended = RunEndedEvent(
        run_id=run_id,
        sequence=8,
        timestamp=NOW,
        status=EvaluationStatus.EVAL_VALID,
        duration_ms=1000,
    )
    return [
        start,
        model_call,
        tool,
        evidence,
        checkpoint,
        recovery,
        final_report,
        evaluation,
        ended,
    ]


class EvidenceStoreTests(TestCase):
    def test_evidence_round_trip_and_duplicate_rejection(self) -> None:
        run_id = uuid4()
        record = EvidenceRecord(
            run_id=run_id,
            claim="Mem0 stores memories.",
            source_id="src_mem0",
            source_url="https://example.com/mem0",
            retrieved_at=NOW,
            evidence_excerpt="Mem0 stores memories in a vector store.",
            source_date=date(2026, 6, 1),
            confidence=0.9,
            source_content_hash=HASH,
        )
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.jsonl"
            store = EvidenceStore(path)
            store.append(record)
            reopened = EvidenceStore(path)
            self.assertEqual(reopened.get(record.evidence_id), record)
            with self.assertRaises(EvidenceStoreError):
                reopened.append(record)

    def test_naive_retrieval_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceRecord(
                run_id=uuid4(),
                claim="claim",
                source_id="source",
                source_url="https://example.com",
                retrieved_at=datetime(2026, 6, 21),
                evidence_excerpt="excerpt",
                source_date=None,
                confidence=0.5,
            )


class TraceStoreTests(TestCase):
    def test_append_reopen_and_sequence_enforcement(self) -> None:
        run_id = uuid4()
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "trace.jsonl"
            writer = TraceWriter(path, run_id)
            writer.append(run_started(run_id))
            reopened = TraceWriter(path, run_id)
            self.assertEqual(reopened.next_sequence, 1)
            with self.assertRaises(TraceStoreError):
                reopened.append(run_started(run_id, sequence=2))


class TraceValidationTests(TestCase):
    def test_complete_trace_and_evidence_store_validate(self) -> None:
        run_id = uuid4()
        evidence_id = uuid4()
        events = successful_trace(run_id, evidence_id)
        record = EvidenceRecord(
            evidence_id=evidence_id,
            run_id=run_id,
            claim="Mem0 stores memories.",
            source_id="src_mem0",
            source_url="https://example.com/mem0",
            retrieved_at=NOW,
            evidence_excerpt="Mem0 stores memories.",
            source_date=None,
            confidence=0.9,
        )
        with TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir) / "evidence.jsonl")
            store.append(record)
            self.assertEqual(validate_trace(events, store), events)

    def test_missing_cited_evidence_is_rejected(self) -> None:
        run_id = uuid4()
        events = successful_trace(run_id, uuid4())
        events[6] = events[6].model_copy(update={"cited_evidence_ids": [uuid4()]})
        with self.assertRaisesRegex(TraceValidationError, "before it was recorded"):
            validate_trace(events)

    def test_sequence_gap_is_rejected(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[2] = events[2].model_copy(update={"sequence": 99})
        with self.assertRaisesRegex(TraceValidationError, "contiguous"):
            validate_trace(events)

    def test_unseen_parent_is_rejected(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[1] = events[1].model_copy(update={"parent_event_id": uuid4()})
        with self.assertRaisesRegex(TraceValidationError, "unseen parent"):
            validate_trace(events)

    def test_unknown_tool_call_id_is_rejected(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[2] = events[2].model_copy(update={"tool_call_id": "invented"})
        with self.assertRaisesRegex(TraceValidationError, "unknown tool_call_id"):
            validate_trace(events)

    def test_tool_name_must_match_emitted_request(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[2] = events[2].model_copy(update={"tool_name": "search_sources"})
        with self.assertRaisesRegex(TraceValidationError, "does not match emitted name"):
            validate_trace(events)

    def test_tool_arguments_must_match_emitted_request(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[2] = events[2].model_copy(
            update={"arguments": {"source_id": "different-source"}}
        )
        with self.assertRaisesRegex(TraceValidationError, "arguments do not match"):
            validate_trace(events)

    def test_unknown_checkpoint_is_rejected(self) -> None:
        events = successful_trace(uuid4(), uuid4())
        events[5] = events[5].model_copy(update={"checkpoint_id": uuid4()})
        with self.assertRaisesRegex(TraceValidationError, "unknown checkpoint_id"):
            validate_trace(events)

    def test_evidence_created_event_must_match_trace(self) -> None:
        run_id = uuid4()
        evidence_id = uuid4()
        events = successful_trace(run_id, evidence_id)
        record = EvidenceRecord(
            evidence_id=evidence_id,
            run_id=run_id,
            claim="Mem0 stores memories.",
            source_id="src_mem0",
            source_url="https://example.com/mem0",
            retrieved_at=NOW,
            evidence_excerpt="Mem0 stores memories.",
            source_date=None,
            confidence=0.9,
            created_event_id=uuid4(),
        )
        with TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir) / "evidence.jsonl")
            store.append(record)
            with self.assertRaisesRegex(TraceValidationError, "does not match trace"):
                validate_trace(events, store)

    def test_orphan_evidence_store_record_is_rejected(self) -> None:
        run_id = uuid4()
        evidence_id = uuid4()
        events = successful_trace(run_id, evidence_id)
        with TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir) / "evidence.jsonl")
            for current_id in (evidence_id, uuid4()):
                store.append(
                    EvidenceRecord(
                        evidence_id=current_id,
                        run_id=run_id,
                        claim="Mem0 stores memories.",
                        source_id="src_mem0",
                        source_url="https://example.com/mem0",
                        retrieved_at=NOW,
                        evidence_excerpt="Mem0 stores memories.",
                        source_date=None,
                        confidence=0.9,
                    )
                )
            with self.assertRaisesRegex(
                TraceValidationError,
                "evidence store IDs missing from trace",
            ):
                validate_trace(events, store)


class MetricsTests(TestCase):
    def test_six_primary_metrics_are_computable(self) -> None:
        metrics = compute_run_metrics(successful_trace(uuid4(), uuid4()))
        self.assertTrue(metrics.task_success)
        self.assertEqual(metrics.total_cost_usd, Decimal("0.0031"))
        self.assertEqual(metrics.cost_per_success_usd, Decimal("0.0031"))
        self.assertEqual(metrics.citation_precision, 1.0)
        self.assertEqual(metrics.required_claim_coverage, 1.0)
        self.assertEqual(metrics.peak_active_context_tokens, 1000)
        self.assertTrue(metrics.recovery_success)

    def test_aggregate_egtsr_and_cost_per_success(self) -> None:
        success = compute_run_metrics(successful_trace(uuid4(), uuid4()))
        failed_events = successful_trace(uuid4(), uuid4())
        failed_events[7] = failed_events[7].model_copy(update={"task_success": False})
        failed = compute_run_metrics(failed_events)
        aggregate = aggregate_run_metrics([success, failed])
        self.assertEqual(aggregate.eval_valid_runs, 2)
        self.assertEqual(aggregate.successful_runs, 1)
        self.assertEqual(aggregate.egtsr, 0.5)
        self.assertEqual(aggregate.cost_per_success_usd, Decimal("0.0062"))

    def test_mixed_evaluation_scopes_cannot_be_aggregated(self) -> None:
        primary = compute_run_metrics(successful_trace(uuid4(), uuid4()))
        fixture_events = successful_trace(uuid4(), uuid4())
        fixture_events[0] = fixture_events[0].model_copy(
            update={"evaluation_scope": EvaluationScope.FIXTURE}
        )
        fixture = compute_run_metrics(fixture_events)
        with self.assertRaisesRegex(ValueError, "mixed evaluation scopes"):
            aggregate_run_metrics([primary, fixture])


class SchemaAndCliTests(TestCase):
    def test_exported_schemas_are_valid_draft_2020_12(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in (
            "trace-event.schema.json",
            "evidence-record.schema.json",
            "snapshot-manifest.schema.json",
            "benchmark-task.schema.json",
            "token-pricing.schema.json",
        ):
            schema = json.loads((root / "schemas" / filename).read_text())
            Draft202012Validator.check_schema(schema)

    def test_cli_validates_and_reproduces_metrics(self) -> None:
        run_id = uuid4()
        evidence_id = uuid4()
        events = successful_trace(run_id, evidence_id)
        with TemporaryDirectory() as temp_dir:
            trace_path = Path(temp_dir) / "trace.jsonl"
            writer = TraceWriter(trace_path, run_id)
            for event in events:
                writer.append(event)
            evidence_store = EvidenceStore(Path(temp_dir) / "evidence.jsonl")
            evidence_store.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    run_id=run_id,
                    claim="Mem0 stores memories.",
                    source_id="src_mem0",
                    source_url="https://example.com/mem0",
                    retrieved_at=NOW,
                    evidence_excerpt="Mem0 stores memories.",
                    source_date=None,
                    confidence=0.9,
                    created_event_id=events[3].event_id,
                )
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = trace_cli(["metrics", str(trace_path)])
            payload = json.loads(output.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["aggregate"]["egtsr"], 1.0)
            self.assertEqual(
                payload["aggregate"]["cost_per_success_usd"],
                "0.0031",
            )
