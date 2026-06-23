"""C1 trigger, tool-result clearing, checkpoint, and retention tests."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.agent.c1 import C1Runner
from src.agent.compaction import (
    CompactionConfig,
    build_tool_result_clearing_plan,
    load_compaction_config,
    should_compact,
)
from src.agent.fixture import FixtureProvider
from src.evidence import EvidenceStore
from src.snapshots import SnapshotCorpus
from src.tasks.load import load_task
from src.trace.models import (
    ChatMessage,
    CheckpointEvent,
    CompactionEvent,
    Configuration,
    ModelCallEvent,
    ModelUsage,
    RunBudget,
    ToolCallRequest,
)
from src.trace.store import TraceReader
from src.trace.validate import TraceValidationError, validate_trace

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "data" / "fixtures" / "tasks" / "mem0-architecture.json"
MANIFEST_PATH = (
    ROOT / "data" / "fixtures" / "source_snapshots" / "manifest.json"
)
CONFIG_PATH = ROOT / "configs" / "compaction.yaml"


class ContextPressureFixtureProvider(FixtureProvider):
    """Fixture protocol with deterministic input-token pressure."""

    def complete(self, messages, tools, *, max_output_tokens):
        completion = super().complete(
            messages,
            tools,
            max_output_tokens=max_output_tokens,
        )
        forced_input_tokens = {
            1: 20,
            2: 60,
            3: 20,
        }.get(self.call_count, 20)
        return replace(
            completion,
            usage=ModelUsage(
                input_tokens=forced_input_tokens,
                output_tokens=completion.usage.output_tokens,
                cache_hit_tokens=0,
                cache_miss_tokens=forced_input_tokens,
            ),
        )


def c1_budget() -> RunBudget:
    return RunBudget(
        max_model_calls=10,
        max_tool_calls=20,
        max_active_context_tokens=100,
        max_uncached_input_tokens=1_000,
        max_output_tokens=20_000,
        max_cost_usd=Decimal("5"),
        max_duration_ms=60_000,
    )


class CompactionUnitTests(TestCase):
    def test_config_uses_sixty_percent_active_context_trigger(self) -> None:
        config = load_compaction_config(CONFIG_PATH)
        self.assertEqual(config.trigger_tokens(100_000), 60_000)
        self.assertFalse(
            should_compact(
                config=config,
                max_active_context_tokens=100_000,
                last_model_input_tokens=59_999,
                completed_compactions=0,
            )
        )
        self.assertTrue(
            should_compact(
                config=config,
                max_active_context_tokens=100_000,
                last_model_input_tokens=60_000,
                completed_compactions=0,
            )
        )

    def test_tool_result_clearing_preserves_reload_pointer(self) -> None:
        messages = [
            ChatMessage(role="system", content="system"),
            ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCallRequest(
                        id="read-1",
                        name="read_source",
                        arguments={"source_id": "src_mem0_docs"},
                    )
                ],
            ),
            ChatMessage(
                role="tool",
                tool_call_id="read-1",
                content=json.dumps(
                    {
                        "source_id": "src_mem0_docs",
                        "cleaned_text": "large frozen source text",
                    }
                ),
            ),
        ]
        with TemporaryDirectory() as temp_dir:
            plan = build_tool_result_clearing_plan(
                messages=messages,
                evidence=EvidenceStore(Path(temp_dir) / "evidence.jsonl"),
                input_tokens=60_000,
                task_prompt="compare memory systems",
                compaction_index=1,
            )
        self.assertIsNotNone(plan)
        assert plan is not None
        payload = json.loads(str(plan.messages[-1].content))
        self.assertEqual(
            payload,
            {
                "compacted": True,
                "reload_with": "read_source",
                "source_id": "src_mem0_docs",
            },
        )
        self.assertNotIn("large frozen source text", str(plan.messages))
        self.assertEqual(
            plan.required_fact_ids,
            ["source:src_mem0_docs"],
        )
        self.assertEqual(plan.preserved_fact_ids, plan.required_fact_ids)

    def test_later_compaction_retains_existing_reload_pointers(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCallRequest(
                        id="old-read",
                        name="read_source",
                        arguments={"source_id": "old-source"},
                    ),
                    ToolCallRequest(
                        id="new-read",
                        name="read_source",
                        arguments={"source_id": "new-source"},
                    ),
                ],
            ),
            ChatMessage(
                role="tool",
                tool_call_id="old-read",
                content=json.dumps(
                    {
                        "compacted": True,
                        "reload_with": "read_source",
                        "source_id": "old-source",
                    }
                ),
            ),
            ChatMessage(
                role="tool",
                tool_call_id="new-read",
                content=json.dumps(
                    {
                        "source_id": "new-source",
                        "cleaned_text": "new source text",
                    }
                ),
            ),
        ]
        with TemporaryDirectory() as temp_dir:
            plan = build_tool_result_clearing_plan(
                messages=messages,
                evidence=EvidenceStore(Path(temp_dir) / "evidence.jsonl"),
                input_tokens=70_000,
                task_prompt="research",
                compaction_index=2,
            )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            plan.required_fact_ids,
            ["source:new-source", "source:old-source"],
        )
        self.assertEqual(
            plan.checkpoint_state.retry_state["reloadable_source_ids"],
            ["new-source", "old-source"],
        )


class C1RunnerTests(TestCase):
    def test_c1_emits_measured_compaction_and_retains_reloadable_fact(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            outcome = C1Runner(
                task=load_task(TASK_PATH),
                corpus=SnapshotCorpus(MANIFEST_PATH),
                provider=ContextPressureFixtureProvider(),
                budget=c1_budget(),
                max_iterations=10,
                output_dir=Path(temp_dir) / "run",
                run_group_id="test-c1",
                compaction_config=CompactionConfig(
                    trigger_fraction=0.5,
                    max_compactions=1,
                ),
            ).run()
            events = TraceReader(outcome.trace_path).read_all()
            validate_trace(events, outcome.evidence_path)

        self.assertEqual(events[0].configuration, Configuration.C1)
        checkpoints = [
            event for event in events if isinstance(event, CheckpointEvent)
        ]
        compactions = [
            event for event in events if isinstance(event, CompactionEvent)
        ]
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(len(compactions), 1)
        compaction = compactions[0]
        self.assertEqual(compaction.checkpoint_id, checkpoints[0].checkpoint_id)
        self.assertEqual(compaction.strategy, "tool_result_clearing")
        self.assertEqual(compaction.input_tokens, 60)
        self.assertEqual(compaction.output_tokens, 20)
        self.assertLess(compaction.output_tokens, compaction.input_tokens)
        self.assertEqual(
            compaction.preserved_fact_ids,
            compaction.required_fact_ids,
        )
        self.assertEqual(outcome.metrics.compaction_ratio, 1 / 3)
        self.assertEqual(outcome.metrics.critical_fact_retention, 1.0)
        self.assertEqual(compaction.summary_cost_usd, Decimal("0"))
        self.assertIsNone(compaction.summary_model_call_id)

        post_checkpoint_call = next(
            event
            for event in events
            if isinstance(event, ModelCallEvent)
            and event.sequence > checkpoints[0].sequence
        )
        compacted_tool_message = next(
            message
            for message in post_checkpoint_call.request_messages
            if message.role == "tool"
            and isinstance(message.content, str)
            and '"compacted": true' in message.content
        )
        self.assertEqual(
            json.loads(str(compacted_tool_message.content)),
            {
                "compacted": True,
                "reload_with": "read_source",
                "source_id": "src_mem0_docs",
            },
        )
        self.assertNotIn(
            "Mem0 stores memories in a vector store.",
            str(compacted_tool_message.content),
        )

        invalid_events = list(events)
        index = invalid_events.index(compaction)
        invalid_events[index] = compaction.model_copy(
            update={"summary_model_call_id": "missing-summary-call"}
        )
        with self.assertRaisesRegex(
            TraceValidationError,
            "unknown summary model call_id",
        ):
            validate_trace(invalid_events, outcome.evidence_path)

        mismatched_cost_events = list(events)
        mismatched_cost_events[index] = compaction.model_copy(
            update={
                "summary_model_call_id": "model-1",
                "summary_cost_usd": Decimal("1"),
            }
        )
        with self.assertRaisesRegex(
            TraceValidationError,
            "summary cost does not match model call",
        ):
            validate_trace(mismatched_cost_events, outcome.evidence_path)

    def test_c1_requires_active_context_budget(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                ValueError,
                "max_active_context_tokens",
            ):
                C1Runner(
                    task=load_task(TASK_PATH),
                    corpus=SnapshotCorpus(MANIFEST_PATH),
                    provider=FixtureProvider(),
                    budget=c1_budget().model_copy(
                        update={"max_active_context_tokens": None}
                    ),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="test-c1-no-context-budget",
                    compaction_config=CompactionConfig(),
                )
