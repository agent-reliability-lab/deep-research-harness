"""C0 single-agent ReAct loop with full trace and budget accounting."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from src.evidence import EvidenceStore
from src.snapshots import SnapshotCorpus
from src.tasks import BenchmarkTask, evaluate_deterministic_run
from src.tasks.models import EvaluationMode, TaskLifecycle
from src.tools import TOOL_SCHEMAS, ToolExecutionError, ToolRuntime
from src.trace.metrics import RunMetrics, compute_run_metrics
from src.trace.models import (
    CallCost,
    CallStatus,
    ChatMessage,
    Configuration,
    EvaluationEvent,
    EvaluationScope,
    EvaluationStatus,
    ModelCallEvent,
    RunBudget,
    RunEndedEvent,
    RunStartedEvent,
)
from src.trace.store import TraceReader, TraceWriter
from src.trace.validate import validate_trace

from .budget import BudgetExceeded, BudgetTracker
from .provider import ModelCompletion, ModelProtocolError, ModelProvider

SYSTEM_PROMPT = """You are an evidence-grounded research agent.
Use only the supplied frozen-corpus tools. Search before reading, record each
claim as evidence, and finish by calling finalize with the evidence UUIDs.
Never invent source IDs, evidence IDs, or claims not present in source text.
Call at most {max_tool_calls_per_turn} tools in one assistant response. If more
work remains, continue it in the next model turn instead of emitting a larger
parallel batch."""


@dataclass(frozen=True)
class RunOutcome:
    run_id: UUID
    status: EvaluationStatus
    failure_label: str | None
    trace_path: Path
    evidence_path: Path
    report_path: Path | None
    metrics: RunMetrics


class C0Runner:
    configuration = Configuration.C0

    def __init__(
        self,
        *,
        task: BenchmarkTask,
        corpus: SnapshotCorpus,
        provider: ModelProvider,
        budget: RunBudget,
        max_iterations: int,
        output_dir: str | Path,
        run_group_id: str,
        max_tool_calls_per_turn: int = 4,
    ) -> None:
        task = BenchmarkTask.model_validate(task.model_dump())
        self.task = task
        self.corpus = corpus
        self.provider = provider
        self.budget = budget
        self.max_iterations = max_iterations
        self.output_dir = Path(output_dir)
        self.run_group_id = run_group_id
        if max_tool_calls_per_turn < 1:
            raise ValueError("max_tool_calls_per_turn must be at least 1")
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        if task.lifecycle is not TaskLifecycle.FROZEN:
            raise ValueError("C0 runner refuses draft tasks; freeze and verify first")
        if task.source_snapshot_id != corpus.manifest.snapshot_id:
            raise ValueError(
                "task source_snapshot_id does not match loaded corpus: "
                f"task={task.source_snapshot_id} "
                f"corpus={corpus.manifest.snapshot_id}"
            )
        corpus_source_ids = {entry.source_id for entry in corpus.entries()}
        missing_sources = set(task.acceptable_source_ids) - corpus_source_ids
        if missing_sources:
            raise ValueError(
                "task references sources missing from snapshot: "
                f"{sorted(missing_sources)}"
            )

    def run(self) -> RunOutcome:
        trace_path = self.output_dir / "trace.jsonl"
        evidence_path = self.output_dir / "evidence.jsonl"
        if trace_path.exists() or evidence_path.exists():
            raise FileExistsError(
                f"refusing to append a new run into existing output: {self.output_dir}"
            )

        run_id = uuid4()
        writer = TraceWriter(trace_path, run_id)
        evidence = EvidenceStore(evidence_path)
        tracker = BudgetTracker(
            self.budget,
            max_iterations=self.max_iterations,
        )
        start = RunStartedEvent(
            run_id=run_id,
            sequence=writer.next_sequence,
            timestamp=self._now(),
            run_group_id=self.run_group_id,
            task_id=self.task.task_id,
            task_version=self.task.task_version,
            rubric_version=self.task.rubric_version,
            configuration=self.configuration,
            evaluation_scope=(
                EvaluationScope.FIXTURE
                if self.task.fixture_only
                else EvaluationScope.DEVELOPMENT
            ),
            provider=self.provider.provider_name,
            endpoint_class=self.provider.endpoint_class,
            requested_model=self.provider.model,
            model_parameters={
                **self.provider.model_parameters,
                "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            },
            source_snapshot_id=self.corpus.manifest.snapshot_id,
            pricing_version=self.provider.pricing_version,
            budget=self.budget,
        )
        writer.append(start)
        runtime = ToolRuntime(
            run_id=run_id,
            corpus=self.corpus,
            trace=writer,
            evidence=evidence,
            artifact_dir=self.output_dir / "artifacts",
            within_budget=tracker.within_limits,
        )
        messages = [
            ChatMessage(
                role="system",
                content=SYSTEM_PROMPT.format(
                    max_tool_calls_per_turn=self.max_tool_calls_per_turn,
                ),
            ),
            ChatMessage(role="user", content=self.task.prompt),
        ]
        last_model_input_tokens: int | None = None

        while True:
            try:
                tracker.before_model_call()
            except BudgetExceeded as exc:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label=f"budget_exhausted:{exc.limit}",
                    included_in_denominator=True,
                )
            messages, pending_control = self._prepare_messages_for_model_call(
                messages=messages,
                writer=writer,
                evidence=evidence,
                tracker=tracker,
                last_model_input_tokens=last_model_input_tokens,
            )

            parent_event_id = self._require_last_event_id(writer)
            call_id = f"model-{tracker.model_calls}"
            requested_messages = [message.model_copy(deep=True) for message in messages]
            started = time.perf_counter()
            try:
                completion = self.provider.complete(
                    messages,
                    TOOL_SCHEMAS,
                    max_output_tokens=tracker.remaining_output_tokens,
                )
            except ModelProtocolError as exc:
                partial = exc.partial_completion
                writer.append(
                    ModelCallEvent(
                        run_id=run_id,
                        sequence=writer.next_sequence,
                        timestamp=self._now(),
                        parent_event_id=parent_event_id,
                        call_id=call_id,
                        status=CallStatus.ERROR,
                        requested_model=self.provider.model,
                        returned_model=partial.returned_model if partial else None,
                        system_fingerprint=(
                            partial.system_fingerprint if partial else None
                        ),
                        provider_request_id=(
                            partial.provider_request_id if partial else None
                        ),
                        request_messages=requested_messages,
                        tool_schemas=TOOL_SCHEMAS,
                        response_content=partial.content if partial else None,
                        response_tool_calls=partial.tool_calls if partial else [],
                        usage=partial.usage if partial else None,
                        cost=partial.cost if partial else CallCost(),
                        latency_ms=(
                            partial.latency_ms
                            if partial
                            else self._latency_ms(started)
                        ),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                if partial:
                    try:
                        tracker.after_model_call(partial.usage, partial.cost)
                    except BudgetExceeded:
                        pass
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label=f"model_protocol_error:{type(exc).__name__}",
                    included_in_denominator=True,
                )
            except Exception as exc:
                writer.append(
                    ModelCallEvent(
                        run_id=run_id,
                        sequence=writer.next_sequence,
                        timestamp=self._now(),
                        parent_event_id=parent_event_id,
                        call_id=call_id,
                        status=CallStatus.ERROR,
                        requested_model=self.provider.model,
                        request_messages=requested_messages,
                        tool_schemas=TOOL_SCHEMAS,
                        latency_ms=self._latency_ms(started),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.INFRA_API_FAILED,
                    failure_label=f"provider_error:{type(exc).__name__}",
                    included_in_denominator=False,
                )

            if completion.returned_model != self.provider.model:
                self._append_identity_failure(
                    writer=writer,
                    parent_event_id=parent_event_id,
                    call_id=call_id,
                    messages=requested_messages,
                    completion=completion,
                )
                try:
                    tracker.after_model_call(completion.usage, completion.cost)
                except BudgetExceeded:
                    pass
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.INFRA_API_FAILED,
                    failure_label="model_identity_mismatch",
                    included_in_denominator=False,
                )

            model_event = ModelCallEvent(
                run_id=run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=parent_event_id,
                call_id=call_id,
                status=CallStatus.SUCCESS,
                requested_model=self.provider.model,
                returned_model=completion.returned_model,
                system_fingerprint=completion.system_fingerprint,
                provider_request_id=completion.provider_request_id,
                request_messages=requested_messages,
                tool_schemas=TOOL_SCHEMAS,
                response_content=completion.content,
                response_tool_calls=completion.tool_calls,
                usage=completion.usage,
                cost=completion.cost,
                latency_ms=completion.latency_ms,
            )
            writer.append(model_event)
            self._append_post_model_call_events(
                pending_control=pending_control,
                completion=completion,
                model_event=model_event,
                writer=writer,
            )
            last_model_input_tokens = completion.usage.input_tokens
            try:
                tracker.after_model_call(completion.usage, completion.cost)
            except BudgetExceeded as exc:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label=f"budget_exhausted:{exc.limit}",
                    included_in_denominator=True,
                )

            if not completion.tool_calls:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label="output_format_failure:missing_tool_call",
                    included_in_denominator=True,
                )
            if len(completion.tool_calls) > self.max_tool_calls_per_turn:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label=(
                        "output_format_failure:"
                        "too_many_tool_calls_per_turn"
                    ),
                    included_in_denominator=True,
                )
            finalize_calls = [
                call for call in completion.tool_calls if call.name == "finalize"
            ]
            if finalize_calls and len(completion.tool_calls) != 1:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label="output_format_failure:finalize_must_be_only_call",
                    included_in_denominator=True,
                )

            try:
                tracker.before_tool_calls(len(completion.tool_calls))
            except BudgetExceeded as exc:
                return self._finish_failure(
                    writer=writer,
                    evidence=evidence,
                    tracker=tracker,
                    status=EvaluationStatus.AGENT_FAILED,
                    failure_label=f"budget_exhausted:{exc.limit}",
                    included_in_denominator=True,
                )

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=completion.content,
                    tool_calls=completion.tool_calls,
                )
            )
            for call in completion.tool_calls:
                try:
                    tracker.record_tool_call()
                    result = runtime.execute(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        parent_event_id=model_event.event_id,
                    )
                    tracker.check_duration()
                except BudgetExceeded as exc:
                    return self._finish_failure(
                        writer=writer,
                        evidence=evidence,
                        tracker=tracker,
                        status=EvaluationStatus.AGENT_FAILED,
                        failure_label=f"budget_exhausted:{exc.limit}",
                        included_in_denominator=True,
                    )
                except ToolExecutionError as exc:
                    return self._finish_failure(
                        writer=writer,
                        evidence=evidence,
                        tracker=tracker,
                        status=EvaluationStatus.AGENT_FAILED,
                        failure_label=f"tool_execution_failed:{type(exc.__cause__).__name__}",
                        included_in_denominator=True,
                    )
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call.id,
                        content=json.dumps(result, ensure_ascii=False, sort_keys=True),
                    )
                )

                if call.name == "finalize":
                    return self._finish_success(
                        writer=writer,
                        evidence=evidence,
                        tracker=tracker,
                    )

    def _prepare_messages_for_model_call(
        self,
        *,
        messages: list[ChatMessage],
        writer: TraceWriter,
        evidence: EvidenceStore,
        tracker: BudgetTracker,
        last_model_input_tokens: int | None,
    ) -> tuple[list[ChatMessage], object | None]:
        del writer, evidence, tracker, last_model_input_tokens
        return messages, None

    def _append_post_model_call_events(
        self,
        *,
        pending_control: object | None,
        completion: ModelCompletion,
        model_event: ModelCallEvent,
        writer: TraceWriter,
    ) -> None:
        del pending_control, completion, model_event, writer

    def _finish_success(
        self,
        *,
        writer: TraceWriter,
        evidence: EvidenceStore,
        tracker: BudgetTracker,
    ) -> RunOutcome:
        events = TraceReader(writer.path).read_all()
        parent_event_id = self._require_last_event_id(writer)
        if self.task.evaluation_mode in {
            EvaluationMode.DETERMINISTIC_FIXTURE,
            EvaluationMode.DETERMINISTIC_BENCHMARK,
        }:
            evaluation = evaluate_deterministic_run(
                task=self.task,
                events=events,
                evidence=evidence,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=parent_event_id,
            )
            failure_label = None
            if not evaluation.task_success:
                failure_label = (
                    "fixture_rubric_failed"
                    if self.task.fixture_only
                    else "deterministic_rubric_failed"
                )
        else:
            final_report = next(
                event
                for event in reversed(events)
                if event.event_type == "final_report"
            )
            evaluation = EvaluationEvent(
                run_id=writer.run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=parent_event_id,
                status=EvaluationStatus.JUDGE_REQUIRED,
                included_in_egtsr_denominator=False,
                task_success=None,
                required_claims_total=len(self.task.required_claims),
                supported_required_claims=0,
                citations_total=len(final_report.cited_evidence_ids),
                supported_citations=0,
                entailed_citations=0,
                factual_correctness_passed=None,
                critical_policy_violations=0,
                final_artifact_within_budget=final_report.produced_within_budget,
                unsupported_claim_count=0,
            )
            failure_label = "judge_pending"
        writer.append(evaluation)
        writer.append(
            RunEndedEvent(
                run_id=writer.run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=evaluation.event_id,
                status=evaluation.status,
                duration_ms=tracker.duration_ms,
                failure_label=failure_label,
            )
        )
        return self._outcome(
            writer=writer,
            evidence=evidence,
            status=evaluation.status,
            failure_label=failure_label,
        )

    def _finish_failure(
        self,
        *,
        writer: TraceWriter,
        evidence: EvidenceStore,
        tracker: BudgetTracker,
        status: EvaluationStatus,
        failure_label: str,
        included_in_denominator: bool,
    ) -> RunOutcome:
        parent_event_id = self._require_last_event_id(writer)
        evaluation = EvaluationEvent(
            run_id=writer.run_id,
            sequence=writer.next_sequence,
            timestamp=self._now(),
            parent_event_id=parent_event_id,
            status=status,
            included_in_egtsr_denominator=included_in_denominator,
            task_success=False if included_in_denominator else None,
            required_claims_total=len(self.task.required_claims),
            supported_required_claims=0,
            citations_total=0,
            supported_citations=0,
            entailed_citations=0,
            factual_correctness_passed=False if included_in_denominator else None,
            critical_policy_violations=0,
            final_artifact_within_budget=False,
            unsupported_claim_count=0,
        )
        writer.append(evaluation)
        writer.append(
            RunEndedEvent(
                run_id=writer.run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=evaluation.event_id,
                status=status,
                duration_ms=tracker.duration_ms,
                failure_label=failure_label,
            )
        )
        return self._outcome(
            writer=writer,
            evidence=evidence,
            status=status,
            failure_label=failure_label,
        )

    def _outcome(
        self,
        *,
        writer: TraceWriter,
        evidence: EvidenceStore,
        status: EvaluationStatus,
        failure_label: str | None,
    ) -> RunOutcome:
        events = validate_trace(writer.path, evidence)
        metrics = compute_run_metrics(events)
        report_path = self.output_dir / "artifacts" / "final-report.md"
        return RunOutcome(
            run_id=writer.run_id,
            status=status,
            failure_label=failure_label,
            trace_path=writer.path,
            evidence_path=evidence.path,
            report_path=report_path if report_path.exists() else None,
            metrics=metrics,
        )

    def _append_identity_failure(
        self,
        *,
        writer: TraceWriter,
        parent_event_id: UUID,
        call_id: str,
        messages: list[ChatMessage],
        completion: ModelCompletion,
    ) -> None:
        writer.append(
            ModelCallEvent(
                run_id=writer.run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=parent_event_id,
                call_id=call_id,
                status=CallStatus.ERROR,
                requested_model=self.provider.model,
                returned_model=completion.returned_model,
                system_fingerprint=completion.system_fingerprint,
                provider_request_id=completion.provider_request_id,
                request_messages=messages,
                tool_schemas=TOOL_SCHEMAS,
                response_content=completion.content,
                response_tool_calls=completion.tool_calls,
                usage=completion.usage,
                cost=completion.cost,
                latency_ms=completion.latency_ms,
                error_type="ModelIdentityError",
                error_message=(
                    f"requested={self.provider.model} "
                    f"returned={completion.returned_model}"
                ),
            )
        )

    @staticmethod
    def _require_last_event_id(writer: TraceWriter) -> UUID:
        if writer.last_event_id is None:
            raise RuntimeError("trace has no parent event")
        return writer.last_event_id

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
