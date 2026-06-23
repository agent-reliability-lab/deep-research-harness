"""Cross-event trace invariants."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import UUID

from src.evidence.store import EvidenceStore

from .models import (
    CheckpointEvent,
    EvaluationEvent,
    EvidenceRecordedEvent,
    FinalReportEvent,
    ModelCallEvent,
    RunEndedEvent,
    RunStartedEvent,
    SubagentHandoffEvent,
    ToolExecutionEvent,
    TraceEvent,
)
from .store import TraceReader


class TraceValidationError(ValueError):
    """One or more trace invariants failed."""


def validate_trace(
    trace: str | Path | list[TraceEvent],
    evidence: str | Path | EvidenceStore | None = None,
) -> list[TraceEvent]:
    events = TraceReader(trace).read_all() if isinstance(trace, (str, Path)) else trace
    problems: list[str] = []
    if not events:
        raise TraceValidationError("trace is empty")

    if not isinstance(events[0], RunStartedEvent):
        problems.append("first event must be run_started")
    start_count = sum(isinstance(event, RunStartedEvent) for event in events)
    if start_count != 1:
        problems.append(f"expected one run_started event, got {start_count}")

    sequences = [event.sequence for event in events]
    expected_sequences = list(range(len(events)))
    if sequences != expected_sequences:
        problems.append(f"sequences must be contiguous: got {sequences}")

    run_ids = {event.run_id for event in events}
    if len(run_ids) != 1:
        problems.append(f"trace contains multiple run_ids: {sorted(map(str, run_ids))}")

    event_ids = [event.event_id for event in events]
    duplicates = [str(item) for item, count in Counter(event_ids).items() if count > 1]
    if duplicates:
        problems.append(f"duplicate event_ids: {duplicates}")

    seen_event_ids: set[UUID] = set()
    for event in events:
        if event.parent_event_id and event.parent_event_id not in seen_event_ids:
            problems.append(
                f"event {event.event_id} references unseen parent {event.parent_event_id}"
            )
        seen_event_ids.add(event.event_id)

    ended = [event for event in events if isinstance(event, RunEndedEvent)]
    if len(ended) > 1:
        problems.append("trace has more than one run_ended event")
    if ended and events[-1] is not ended[0]:
        problems.append("run_ended must be the last event")

    evaluations = [event for event in events if isinstance(event, EvaluationEvent)]
    if len(evaluations) > 1:
        problems.append("trace has more than one evaluation event")

    recorded_ids: set[UUID] = set()
    evidence_event_ids: dict[UUID, UUID] = {}
    emitted_tool_calls: dict[str, tuple[UUID, str, dict]] = {}
    model_calls_by_id: dict[str, ModelCallEvent] = {}
    executed_tool_call_ids: set[str] = set()
    checkpoint_ids: set[UUID] = set()
    for event in events:
        if isinstance(event, ModelCallEvent):
            if event.call_id in model_calls_by_id:
                problems.append(f"duplicate model call_id {event.call_id}")
            model_calls_by_id[event.call_id] = event
            start = events[0]
            if isinstance(start, RunStartedEvent):
                if event.requested_model != start.requested_model:
                    problems.append(
                        f"model call requested {event.requested_model}, "
                        f"run pinned {start.requested_model}"
                    )
                if (
                    event.status.value == "success"
                    and event.returned_model is not None
                    and event.returned_model != start.requested_model
                ):
                    problems.append(
                        f"model call returned {event.returned_model}, "
                        f"run pinned {start.requested_model}"
                    )
            for call in event.response_tool_calls:
                if call.id in emitted_tool_calls:
                    problems.append(f"duplicate emitted tool_call_id {call.id}")
                emitted_tool_calls[call.id] = (
                    event.event_id,
                    call.name,
                    call.arguments,
                )
        if isinstance(event, ToolExecutionEvent):
            if event.tool_call_id not in emitted_tool_calls:
                problems.append(
                    f"tool execution references unknown tool_call_id {event.tool_call_id}"
                )
            else:
                emitter_id, emitted_name, emitted_arguments = emitted_tool_calls[
                    event.tool_call_id
                ]
                if event.parent_event_id != emitter_id:
                    problems.append(
                        f"tool execution {event.tool_call_id} parent_event_id="
                        f"{event.parent_event_id} does not match emitter {emitter_id}"
                    )
                if event.tool_name != emitted_name:
                    problems.append(
                        f"tool execution {event.tool_call_id} name={event.tool_name} "
                        f"does not match emitted name={emitted_name}"
                    )
                if event.arguments != emitted_arguments:
                    problems.append(
                        f"tool execution {event.tool_call_id} arguments do not match "
                        "the emitted request"
                    )
            if event.tool_call_id in executed_tool_call_ids:
                problems.append(f"tool_call_id executed twice: {event.tool_call_id}")
            executed_tool_call_ids.add(event.tool_call_id)
        if isinstance(event, EvidenceRecordedEvent):
            if event.evidence_id in recorded_ids:
                problems.append(f"duplicate evidence_recorded ID {event.evidence_id}")
            recorded_ids.add(event.evidence_id)
            evidence_event_ids[event.evidence_id] = event.event_id
        elif isinstance(event, FinalReportEvent):
            missing = set(event.cited_evidence_ids) - recorded_ids
            if missing:
                problems.append(
                    f"final report references evidence before it was recorded: "
                    f"{sorted(map(str, missing))}"
                )
        elif isinstance(event, SubagentHandoffEvent):
            missing = set(event.evidence_ids) - recorded_ids
            if missing:
                problems.append(
                    f"sub-agent handoff references evidence before it was recorded: "
                    f"{sorted(map(str, missing))}"
                )
        elif isinstance(event, CheckpointEvent):
            missing = set(event.state.evidence_ids) - recorded_ids
            if missing:
                problems.append(
                    f"checkpoint references evidence before it was recorded: "
                    f"{sorted(map(str, missing))}"
                )
            if event.checkpoint_id in checkpoint_ids:
                problems.append(f"duplicate checkpoint_id {event.checkpoint_id}")
            checkpoint_ids.add(event.checkpoint_id)
        elif event.event_type == "compaction":
            if (
                event.checkpoint_id is not None
                and event.checkpoint_id not in checkpoint_ids
            ):
                problems.append(
                    "compaction references unknown checkpoint_id "
                    f"{event.checkpoint_id}"
                )
            if event.summary_model_call_id is not None:
                summary_call = model_calls_by_id.get(
                    event.summary_model_call_id
                )
                if summary_call is None:
                    problems.append(
                        "compaction references unknown summary model call_id "
                        f"{event.summary_model_call_id}"
                    )
                elif summary_call.cost.total_usd != event.summary_cost_usd:
                    problems.append(
                        "compaction summary cost does not match model call: "
                        f"event={event.summary_cost_usd} "
                        f"model_call={summary_call.cost.total_usd}"
                    )
        elif event.event_type == "recovery" and event.checkpoint_id not in checkpoint_ids:
            problems.append(f"recovery references unknown checkpoint_id {event.checkpoint_id}")

    if evidence is not None:
        store = evidence if isinstance(evidence, EvidenceStore) else EvidenceStore(evidence)
        if len(run_ids) == 1:
            store.validate_run(next(iter(run_ids)))
        missing_store_ids = recorded_ids - store.ids()
        if missing_store_ids:
            problems.append(
                f"evidence_recorded IDs missing from store: {sorted(map(str, missing_store_ids))}"
            )
        orphan_store_ids = store.ids() - recorded_ids
        if orphan_store_ids:
            problems.append(
                "evidence store IDs missing from trace: "
                f"{sorted(map(str, orphan_store_ids))}"
            )
        for evidence_id, event_id in evidence_event_ids.items():
            if evidence_id not in store.ids():
                continue
            record = store.get(evidence_id)
            if record.created_event_id is not None and record.created_event_id != event_id:
                problems.append(
                    f"evidence {evidence_id} created_event_id={record.created_event_id} "
                    f"does not match trace event_id={event_id}"
                )

    if evaluations and ended and evaluations[0].status != ended[0].status:
        problems.append(
            f"evaluation status {evaluations[0].status} does not match "
            f"run_ended status {ended[0].status}"
        )
    final_reports = [event for event in events if isinstance(event, FinalReportEvent)]
    if evaluations and evaluations[0].task_success and not final_reports:
        problems.append("successful evaluation requires a final_report event")

    if problems:
        raise TraceValidationError("; ".join(problems))
    return events
