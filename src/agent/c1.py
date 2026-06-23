"""C1 runner: C0 plus deterministic active-context compaction."""

from __future__ import annotations

from src.evidence import EvidenceStore
from src.trace.models import (
    CheckpointEvent,
    CompactionEvent,
    Configuration,
    ModelCallEvent,
)
from src.trace.store import TraceWriter

from .budget import BudgetTracker
from .compaction import (
    CompactionConfig,
    PendingCompaction,
    build_tool_result_clearing_plan,
    should_compact,
)
from .provider import ModelCompletion
from .runner import C0Runner


class C1Runner(C0Runner):
    configuration = Configuration.C1

    def __init__(
        self,
        *,
        compaction_config: CompactionConfig,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if self.budget.max_active_context_tokens is None:
            raise ValueError(
                "C1 requires a max_active_context_tokens budget"
            )
        self.compaction_config = compaction_config
        self.completed_compactions = 0

    def _prepare_messages_for_model_call(
        self,
        *,
        messages,
        writer: TraceWriter,
        evidence: EvidenceStore,
        tracker: BudgetTracker,
        last_model_input_tokens: int | None,
    ):
        if not should_compact(
            config=self.compaction_config,
            max_active_context_tokens=self.budget.max_active_context_tokens,
            last_model_input_tokens=last_model_input_tokens,
            completed_compactions=self.completed_compactions,
        ):
            return messages, None
        plan = build_tool_result_clearing_plan(
            messages=messages,
            evidence=evidence,
            input_tokens=last_model_input_tokens,
            task_prompt=self.task.prompt,
            compaction_index=self.completed_compactions + 1,
        )
        if plan is None:
            return messages, None
        checkpoint = CheckpointEvent(
            run_id=writer.run_id,
            sequence=writer.next_sequence,
            timestamp=self._now(),
            parent_event_id=self._require_last_event_id(writer),
            state_hash=plan.state_hash,
            state=plan.checkpoint_state,
        )
        writer.append(checkpoint)
        self.completed_compactions += 1
        tracker.check_duration()
        return (
            plan.messages,
            PendingCompaction(
                checkpoint_id=checkpoint.checkpoint_id,
                input_tokens=plan.input_tokens,
                required_fact_ids=plan.required_fact_ids,
                preserved_fact_ids=plan.preserved_fact_ids,
                strategy=self.compaction_config.strategy,
            ),
        )

    def _append_post_model_call_events(
        self,
        *,
        pending_control: object | None,
        completion: ModelCompletion,
        model_event: ModelCallEvent,
        writer: TraceWriter,
    ) -> None:
        if not isinstance(pending_control, PendingCompaction):
            return
        writer.append(
            CompactionEvent(
                run_id=writer.run_id,
                sequence=writer.next_sequence,
                timestamp=self._now(),
                parent_event_id=model_event.event_id,
                strategy=pending_control.strategy,
                checkpoint_id=pending_control.checkpoint_id,
                input_tokens=pending_control.input_tokens,
                output_tokens=completion.usage.input_tokens,
                preserved_fact_ids=pending_control.preserved_fact_ids,
                required_fact_ids=pending_control.required_fact_ids,
            )
        )
