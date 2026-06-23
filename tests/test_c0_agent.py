"""C0 loop, budget termination, provider classification, and fixture EGTSR."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from src.agent.budget import BudgetExceeded, BudgetTracker
from src.agent.deepseek import DeepSeekProvider, TokenPricing, load_pricing
from src.agent.fixture import FixtureProvider
from src.agent.provider import ModelCompletion, ModelProtocolError
from src.agent.runner import C0Runner
from src.snapshots import SnapshotCorpus
from src.tasks.load import load_task
from src.tasks.models import (
    BenchmarkTask,
    ClaimScoringMethod,
    EvaluationMode,
)
from src.trace.models import (
    CallCost,
    CallStatus,
    ChatMessage,
    EvaluationStatus,
    ModelCallEvent,
    ModelUsage,
    RunBudget,
    ToolCallRequest,
)
from src.trace.store import TraceReader
from src.trace.validate import validate_trace

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "data" / "fixtures" / "tasks" / "mem0-architecture.json"
MANIFEST_PATH = (
    ROOT / "data" / "fixtures" / "source_snapshots" / "manifest.json"
)
DEEPSEEK_PRICING_PATH = (
    ROOT / "configs" / "pricing" / "deepseek-v4-flash-2026-06-22.json"
)


def budget(**overrides) -> RunBudget:
    values = {
        "max_model_calls": 10,
        "max_tool_calls": 20,
        "max_active_context_tokens": 100_000,
        "max_uncached_input_tokens": 100_000,
        "max_output_tokens": 20_000,
        "max_cost_usd": Decimal("5"),
        "max_duration_ms": 60_000,
    }
    values.update(overrides)
    return RunBudget(**values)


def completion(
    *,
    model: str = "fixture-react-v1",
    tool_calls: list[ToolCallRequest] | None = None,
    cost: Decimal = Decimal("0"),
) -> ModelCompletion:
    return ModelCompletion(
        returned_model=model,
        provider_request_id="request-1",
        system_fingerprint="fingerprint-1",
        content=None if tool_calls else "unstructured answer",
        tool_calls=tool_calls or [],
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        cost=CallCost(input_usd=cost),
        latency_ms=1,
    )


class StaticProvider:
    provider_name = "fixture"
    endpoint_class = "offline-test"
    model = "fixture-react-v1"
    model_parameters = {"deterministic": True}
    pricing_version = "fixture-test"

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def complete(self, messages, tools, *, max_output_tokens):
        del messages, tools, max_output_tokens
        if self.error:
            raise self.error
        return self.response


class BudgetTrackerTests(TestCase):
    def test_iteration_tool_token_cost_and_duration_limits_fail_closed(self) -> None:
        now = [0.0]
        tracker = BudgetTracker(
            budget(
                max_model_calls=2,
                max_tool_calls=1,
                max_active_context_tokens=10,
                max_uncached_input_tokens=10,
                max_output_tokens=5,
                max_cost_usd=Decimal("0.5"),
                max_duration_ms=100,
            ),
            max_iterations=1,
            clock=lambda: now[0],
        )
        tracker.before_model_call()
        tracker.after_model_call(
            ModelUsage(input_tokens=10, output_tokens=4),
            CallCost(input_usd=Decimal("0.5")),
        )
        tracker.before_tool_calls(1)
        tracker.record_tool_call()
        with self.assertRaisesRegex(BudgetExceeded, "max_iterations"):
            tracker.before_model_call()
        with self.assertRaisesRegex(BudgetExceeded, "max_tool_calls"):
            tracker.before_tool_calls(1)
        now[0] = 0.101
        with self.assertRaisesRegex(BudgetExceeded, "max_duration_ms"):
            tracker.check_duration()

    def test_post_call_token_and_cost_overages_raise(self) -> None:
        cases = [
            (
                "max_active_context_tokens",
                budget(max_active_context_tokens=9),
                ModelUsage(input_tokens=10, output_tokens=1),
                CallCost(),
            ),
            (
                "max_uncached_input_tokens",
                budget(max_uncached_input_tokens=9),
                ModelUsage(input_tokens=10, output_tokens=1),
                CallCost(),
            ),
            (
                "max_output_tokens",
                budget(max_output_tokens=4),
                ModelUsage(input_tokens=1, output_tokens=5),
                CallCost(),
            ),
            (
                "max_cost_usd",
                budget(max_cost_usd=Decimal("0.4")),
                ModelUsage(input_tokens=1, output_tokens=1),
                CallCost(input_usd=Decimal("0.5")),
            ),
        ]
        for limit, run_budget, usage, cost in cases:
            with self.subTest(limit=limit):
                tracker = BudgetTracker(run_budget, max_iterations=2)
                tracker.before_model_call()
                with self.assertRaisesRegex(BudgetExceeded, limit):
                    tracker.after_model_call(usage, cost)
                self.assertFalse(tracker.within_limits())

    def test_cached_resends_do_not_exhaust_uncached_input_budget(self) -> None:
        tracker = BudgetTracker(
            budget(
                max_active_context_tokens=100,
                max_uncached_input_tokens=20,
            ),
            max_iterations=2,
        )
        for _ in range(2):
            tracker.before_model_call()
            tracker.after_model_call(
                ModelUsage(
                    input_tokens=100,
                    output_tokens=1,
                    cache_hit_tokens=90,
                    cache_miss_tokens=10,
                ),
                CallCost(),
            )
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot.input_tokens, 200)
        self.assertEqual(snapshot.uncached_input_tokens, 20)
        self.assertEqual(snapshot.peak_active_context_tokens, 100)
        self.assertTrue(tracker.within_limits())

    def test_legacy_cumulative_input_guard_remains_fail_closed(self) -> None:
        tracker = BudgetTracker(
            budget(
                max_active_context_tokens=100,
                max_uncached_input_tokens=100,
                max_input_tokens=99,
            ),
            max_iterations=1,
        )
        tracker.before_model_call()
        with self.assertRaisesRegex(BudgetExceeded, "max_input_tokens"):
            tracker.after_model_call(
                ModelUsage(
                    input_tokens=100,
                    output_tokens=1,
                    cache_hit_tokens=90,
                    cache_miss_tokens=10,
                ),
                CallCost(),
            )

    def test_legacy_budget_does_not_invent_new_limits(self) -> None:
        legacy = RunBudget.model_validate(
            {
                "max_model_calls": 10,
                "max_tool_calls": 20,
                "max_input_tokens": 100_000,
                "max_output_tokens": 20_000,
                "max_cost_usd": 5,
                "max_duration_ms": 60_000,
            }
        )
        self.assertIsNone(legacy.max_active_context_tokens)
        self.assertIsNone(legacy.max_uncached_input_tokens)

    def test_model_call_limit_is_independent_from_iteration_limit(self) -> None:
        tracker = BudgetTracker(
            budget(max_model_calls=1),
            max_iterations=3,
        )
        tracker.before_model_call()
        tracker.after_model_call(
            ModelUsage(input_tokens=1, output_tokens=1),
            CallCost(),
        )
        with self.assertRaisesRegex(BudgetExceeded, "max_model_calls"):
            tracker.before_model_call()


class C0RunnerTests(TestCase):
    def setUp(self) -> None:
        self.task = load_task(TASK_PATH)
        self.corpus = SnapshotCorpus(MANIFEST_PATH)
        self.corpus.verify_all()

    def run_with(self, provider, *, run_budget=None, max_iterations=10):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        outcome = C0Runner(
            task=self.task,
            corpus=self.corpus,
            provider=provider,
            budget=run_budget or budget(),
            max_iterations=max_iterations,
            output_dir=Path(temp_dir.name) / "run",
            run_group_id="test-c0",
        ).run()
        return outcome, TraceReader(outcome.trace_path).read_all()

    def test_fixture_completes_end_to_end_and_reproduces_egtsr(self) -> None:
        outcome, events = self.run_with(FixtureProvider())
        validate_trace(outcome.trace_path, outcome.evidence_path)
        self.assertEqual(outcome.status, EvaluationStatus.EVAL_VALID)
        self.assertEqual(outcome.metrics.evaluation_scope.value, "fixture")
        self.assertTrue(outcome.metrics.task_success)
        self.assertEqual(outcome.metrics.required_claim_coverage, 1.0)
        self.assertEqual(outcome.metrics.citation_precision, 1.0)
        self.assertEqual(len(events), 18)
        self.assertEqual(events[0].task_version, self.task.task_version)
        self.assertEqual(events[0].rubric_version, self.task.rubric_version)
        self.assertEqual(
            sum(event.event_type == "model_call" for event in events),
            6,
        )
        self.assertEqual(
            sum(event.event_type == "tool_execution" for event in events),
            6,
        )
        start = events[0]
        self.assertEqual(start.model_parameters["max_tool_calls_per_turn"], 4)
        first_model_call = next(
            event for event in events if isinstance(event, ModelCallEvent)
        )
        self.assertIn(
            "Call at most 4 tools",
            str(first_model_call.request_messages[0].content),
        )
        self.assertIn(
            "give a short start_anchor and end_anchor",
            str(first_model_call.request_messages[0].content),
        )
        record_schema = next(
            tool["function"]["parameters"]
            for tool in first_model_call.tool_schemas
            if tool["function"]["name"] == "record_evidence"
        )
        self.assertIn(
            "contiguous substring copied exactly",
            record_schema["properties"]["excerpt"]["description"],
        )
        self.assertTrue(outcome.report_path and outcome.report_path.exists())

    def test_deterministic_answer_contract_requires_every_pattern(self) -> None:
        first_claim = self.task.required_claims[0].model_copy(
            update={
                "answer_pattern_groups": [
                    *self.task.required_claims[0].answer_pattern_groups,
                    ["missing answer token"],
                ]
            }
        )
        self.task = self.task.model_copy(
            update={
                "required_claims": [
                    first_claim,
                    *self.task.required_claims[1:],
                ]
            }
        )
        outcome, _ = self.run_with(FixtureProvider())
        self.assertEqual(outcome.status, EvaluationStatus.EVAL_VALID)
        self.assertFalse(outcome.metrics.task_success)
        self.assertEqual(outcome.metrics.required_claim_coverage, 0.5)
        self.assertEqual(outcome.failure_label, "fixture_rubric_failed")

    def test_answer_contract_accepts_one_explicit_variant_per_group(self) -> None:
        first_claim = self.task.required_claims[0].model_copy(
            update={
                "answer_pattern_groups": [
                    [
                        "missing variant",
                        *self.task.required_claims[0].answer_pattern_groups[0],
                    ]
                ]
            }
        )
        self.task = self.task.model_copy(
            update={
                "required_claims": [
                    first_claim,
                    *self.task.required_claims[1:],
                ]
            }
        )
        outcome, _ = self.run_with(FixtureProvider())
        self.assertTrue(outcome.metrics.task_success)

    def test_citation_precision_and_entailment_are_independent(self) -> None:
        first_claim = self.task.required_claims[0].model_copy(
            update={
                "evidence_patterns": [
                    *self.task.required_claims[0].evidence_patterns,
                    "missing evidence token",
                ]
            }
        )
        self.task = self.task.model_copy(
            update={
                "required_claims": [
                    first_claim,
                    *self.task.required_claims[1:],
                ]
            }
        )
        outcome, _ = self.run_with(FixtureProvider())
        self.assertEqual(outcome.metrics.citation_precision, 1.0)
        self.assertEqual(outcome.metrics.citation_entailment_rate, 0.5)
        self.assertFalse(outcome.metrics.task_success)

    def test_deterministic_benchmark_is_scored_in_development_scope(self) -> None:
        self.task = self.task.model_copy(
            update={
                "fixture_only": False,
                "evaluation_mode": EvaluationMode.DETERMINISTIC_BENCHMARK,
            }
        )
        outcome, _ = self.run_with(FixtureProvider())
        self.assertEqual(outcome.status, EvaluationStatus.EVAL_VALID)
        self.assertEqual(outcome.metrics.evaluation_scope.value, "development")
        self.assertTrue(outcome.metrics.included_in_egtsr_denominator)
        self.assertTrue(outcome.metrics.task_success)

    def test_iteration_budget_exhaustion_is_agent_failure_with_valid_trace(self) -> None:
        outcome, events = self.run_with(
            FixtureProvider(),
            run_budget=budget(max_model_calls=3),
            max_iterations=3,
        )
        validate_trace(outcome.trace_path, outcome.evidence_path)
        self.assertEqual(outcome.status, EvaluationStatus.AGENT_FAILED)
        self.assertEqual(outcome.failure_label, "budget_exhausted:max_iterations")
        self.assertTrue(outcome.metrics.included_in_egtsr_denominator)
        self.assertFalse(outcome.metrics.task_success)
        self.assertFalse(any(event.event_type == "final_report" for event in events))

    def test_missing_tool_call_is_scored_agent_failure(self) -> None:
        outcome, _ = self.run_with(StaticProvider(completion()))
        self.assertEqual(outcome.status, EvaluationStatus.AGENT_FAILED)
        self.assertEqual(
            outcome.failure_label,
            "output_format_failure:missing_tool_call",
        )
        self.assertTrue(outcome.metrics.included_in_egtsr_denominator)

    def test_provider_exception_is_excluded_infrastructure_failure(self) -> None:
        outcome, events = self.run_with(
            StaticProvider(error=ConnectionError("offline"))
        )
        self.assertEqual(outcome.status, EvaluationStatus.INFRA_API_FAILED)
        self.assertFalse(outcome.metrics.included_in_egtsr_denominator)
        model_event = next(
            event for event in events if isinstance(event, ModelCallEvent)
        )
        self.assertEqual(model_event.status, CallStatus.ERROR)
        self.assertEqual(model_event.error_type, "ConnectionError")

    def test_identity_mismatch_is_excluded_and_trace_remains_valid(self) -> None:
        outcome, events = self.run_with(
            StaticProvider(completion(model="silently-substituted-model"))
        )
        validate_trace(outcome.trace_path, outcome.evidence_path)
        self.assertEqual(outcome.status, EvaluationStatus.INFRA_API_FAILED)
        self.assertEqual(outcome.failure_label, "model_identity_mismatch")
        model_event = next(
            event for event in events if isinstance(event, ModelCallEvent)
        )
        self.assertEqual(model_event.returned_model, "silently-substituted-model")
        self.assertEqual(model_event.error_type, "ModelIdentityError")

    def test_protocol_failure_accounts_partial_usage_and_cost(self) -> None:
        partial = completion(cost=Decimal("0.25"))
        error = ModelProtocolError(
            "invalid tool JSON",
            partial_completion=partial,
        )
        outcome, events = self.run_with(StaticProvider(error=error))
        self.assertEqual(outcome.status, EvaluationStatus.AGENT_FAILED)
        self.assertEqual(outcome.metrics.total_cost_usd, Decimal("0.25"))
        model_event = next(
            event for event in events if isinstance(event, ModelCallEvent)
        )
        self.assertEqual(model_event.usage.input_tokens, 10)

    def test_finalize_must_be_the_only_tool_call(self) -> None:
        response = completion(
            tool_calls=[
                ToolCallRequest(
                    id="final",
                    name="finalize",
                    arguments={
                        "summary": "done",
                        "evidence_ids": [
                            "00000000-0000-0000-0000-000000000001"
                        ],
                    },
                ),
                ToolCallRequest(
                    id="search",
                    name="search_sources",
                    arguments={"query": "memory"},
                ),
            ]
        )
        outcome, _ = self.run_with(StaticProvider(response))
        self.assertEqual(
            outcome.failure_label,
            "output_format_failure:finalize_must_be_only_call",
        )

    def test_per_turn_tool_call_limit_fails_closed_before_execution(
        self,
    ) -> None:
        response = completion(
            tool_calls=[
                ToolCallRequest(
                    id=f"search-{index}",
                    name="search_sources",
                    arguments={"query": f"memory {index}"},
                )
                for index in range(5)
            ]
        )
        outcome, events = self.run_with(StaticProvider(response))
        self.assertEqual(
            outcome.failure_label,
            "output_format_failure:too_many_tool_calls_per_turn",
        )
        self.assertEqual(
            sum(event.event_type == "tool_execution" for event in events),
            0,
        )
        model_event = next(
            event for event in events if isinstance(event, ModelCallEvent)
        )
        self.assertEqual(len(model_event.response_tool_calls), 5)

    def test_per_turn_tool_call_limit_must_be_positive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                ValueError,
                "max_tool_calls_per_turn",
            ):
                C0Runner(
                    task=self.task,
                    corpus=self.corpus,
                    provider=FixtureProvider(),
                    budget=budget(),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="invalid-per-turn-limit",
                    max_tool_calls_per_turn=0,
                )

    def test_real_task_completion_is_judge_pending_not_scored_success(self) -> None:
        judge_claims = [
            claim.model_copy(
                update={"scoring_method": ClaimScoringMethod.LLM_JUDGE}
            )
            for claim in self.task.required_claims
        ]
        self.task = self.task.model_copy(
            update={
                "fixture_only": False,
                "evaluation_mode": EvaluationMode.JUDGE_REQUIRED,
                "required_claims": judge_claims,
            }
        )
        outcome, _ = self.run_with(FixtureProvider())
        self.assertEqual(outcome.status, EvaluationStatus.JUDGE_REQUIRED)
        self.assertEqual(outcome.metrics.evaluation_scope.value, "development")
        self.assertEqual(outcome.failure_label, "judge_pending")
        self.assertFalse(outcome.metrics.included_in_egtsr_denominator)
        self.assertIsNone(outcome.metrics.task_success)


class TaskContractTests(TestCase):
    def test_fixture_task_and_snapshot_are_valid(self) -> None:
        task = load_task(TASK_PATH)
        corpus = SnapshotCorpus(MANIFEST_PATH)
        corpus.verify_all()
        self.assertTrue(task.fixture_only)
        self.assertEqual(len(task.required_claims), 2)
        self.assertEqual(len(corpus.entries()), 2)

    def test_claim_cannot_reference_task_disallowed_source(self) -> None:
        payload = json.loads(TASK_PATH.read_text(encoding="utf-8"))
        payload["required_claims"][0]["acceptable_source_ids"] = ["invented"]
        with self.assertRaisesRegex(ValueError, "task-disallowed"):
            BenchmarkTask.model_validate(payload)

    def test_runner_rejects_task_source_missing_from_snapshot(self) -> None:
        base_task = load_task(TASK_PATH)
        missing_requirement = base_task.source_requirements[0].model_copy(
            update={"source_id": "missing-source"}
        )
        task = base_task.model_copy(
            update={
                "acceptable_source_ids": [
                    "src_mem0_docs",
                    "src_mem0_paper",
                    "missing-source",
                ],
                "source_requirements": [
                    *base_task.source_requirements,
                    missing_requirement,
                ],
            }
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "missing from snapshot"):
                C0Runner(
                    task=task,
                    corpus=SnapshotCorpus(MANIFEST_PATH),
                    provider=FixtureProvider(),
                    budget=budget(),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="test-c0",
                )


class DeepSeekAdapterTests(TestCase):
    def test_committed_deepseek_pricing_is_versioned_and_nonzero(self) -> None:
        pricing = load_pricing(DEEPSEEK_PRICING_PATH)
        self.assertEqual(
            pricing.pricing_version,
            "deepseek-official-deepseek-v4-flash-2026-06-22",
        )
        self.assertEqual(
            pricing.uncached_input_usd_per_million,
            Decimal("0.14"),
        )
        self.assertEqual(
            pricing.cache_hit_input_usd_per_million,
            Decimal("0.0028"),
        )
        self.assertEqual(pricing.output_usd_per_million, Decimal("0.28"))

    def test_usage_cache_and_versioned_pricing_are_normalized(self) -> None:
        response = SimpleNamespace(
            model="deepseek-v4-flash",
            id="response-1",
            _request_id="request-1",
            system_fingerprint="fp-test",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
            },
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="search_sources",
                                    arguments='{"query":"memory"}',
                                ),
                            )
                        ],
                    )
                )
            ],
        )
        requests = []

        def create(**kwargs):
            requests.append(kwargs)
            return response

        completions = SimpleNamespace(create=create)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = DeepSeekProvider(
            pricing=TokenPricing(
                pricing_version="official-2026-06-21",
                uncached_input_usd_per_million=Decimal("2"),
                cache_hit_input_usd_per_million=Decimal("1"),
                output_usd_per_million=Decimal("3"),
            ),
            client=client,
        )
        result = provider.complete(
            [ChatMessage(role="user", content="research memory")],
            [],
            max_output_tokens=10,
        )
        self.assertEqual(result.usage.cache_hit_tokens, 40)
        self.assertEqual(result.usage.uncached_input_tokens, 60)
        self.assertEqual(result.cost.input_usd, Decimal("0.00012"))
        self.assertEqual(result.cost.cache_usd, Decimal("0.00004"))
        self.assertEqual(result.cost.output_usd, Decimal("0.00006"))
        self.assertEqual(requests[0]["max_tokens"], 10)

    def test_bad_cache_accounting_is_a_protocol_failure_with_partial_usage(self) -> None:
        response = SimpleNamespace(
            model="deepseek-v4-flash",
            id="response-1",
            _request_id="request-1",
            system_fingerprint="fp-test",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 40,
            },
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="done", tool_calls=[])
                )
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )
        provider = DeepSeekProvider(
            pricing=TokenPricing(
                pricing_version="official-2026-06-21",
                uncached_input_usd_per_million=Decimal("2"),
                cache_hit_input_usd_per_million=Decimal("1"),
                output_usd_per_million=Decimal("3"),
            ),
            client=client,
        )
        with self.assertRaises(ModelProtocolError) as context:
            provider.complete(
                [ChatMessage(role="user", content="research memory")],
                [],
                max_output_tokens=10,
            )
        self.assertIsNotNone(context.exception.partial_completion)
        self.assertEqual(
            context.exception.partial_completion.usage.input_tokens,
            100,
        )
