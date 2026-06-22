"""Real benchmark task lifecycle and source-freeze plan tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from src.agent.fixture import FixtureProvider
from src.agent.runner import C0Runner
from src.evidence import EvidenceStore
from src.snapshots import SnapshotCorpus
from src.tasks.cli import build_freeze_plan, load_source_texts
from src.tasks.evaluate import evaluate_deterministic_run
from src.tasks.load import load_task
from src.tasks.models import (
    BenchmarkTask,
    ClaimScoringMethod,
    ClaimVerificationStatus,
    EvaluationMode,
    TaskLifecycle,
)
from src.tasks.preflight import audit_evidence_patterns
from src.trace.models import RunBudget

ROOT = Path(__file__).resolve().parents[1]
DRAFT_DIR = ROOT / "data" / "tasks" / "drafts"
FIXTURE_TASK = ROOT / "data" / "fixtures" / "tasks" / "mem0-architecture.json"
FIXTURE_MANIFEST = (
    ROOT / "data" / "fixtures" / "source_snapshots" / "manifest.json"
)


class TaskLifecycleTests(TestCase):
    def test_four_real_drafts_load_but_are_not_freeze_ready(self) -> None:
        tasks = [load_task(path) for path in sorted(DRAFT_DIR.glob("*.json"))]
        self.assertEqual(len(tasks), 4)
        self.assertTrue(
            all(task.lifecycle is TaskLifecycle.DRAFT for task in tasks)
        )
        self.assertTrue(
            all(task.source_snapshot_id is None for task in tasks)
        )
        self.assertTrue(
            all(
                claim.verification_status is ClaimVerificationStatus.DRAFT
                for task in tasks
                for claim in task.required_claims
            )
        )
        deterministic = [
            task
            for task in tasks
            if task.evaluation_mode is EvaluationMode.DETERMINISTIC_BENCHMARK
        ]
        self.assertEqual(len(deterministic), 2)
        self.assertTrue(
            all(
                claim.scoring_method is ClaimScoringMethod.PATTERN_CONTRACT
                for task in deterministic
                for claim in task.required_claims
            )
        )

    def test_draft_task_cannot_run_even_with_a_valid_corpus(self) -> None:
        task = load_task(
            DRAFT_DIR / "architecture-01-memory-placement.json"
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "refuses draft tasks"):
                C0Runner(
                    task=task,
                    corpus=SnapshotCorpus(FIXTURE_MANIFEST),
                    provider=FixtureProvider(),
                    budget=RunBudget(
                        max_model_calls=10,
                        max_tool_calls=20,
                        max_input_tokens=100_000,
                        max_output_tokens=20_000,
                        max_cost_usd=5,
                        max_duration_ms=60_000,
                    ),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="draft-must-not-run",
                )

    def test_frozen_task_requires_verified_claims_and_snapshot(self) -> None:
        payload = json.loads(
            (
                DRAFT_DIR / "architecture-01-memory-placement.json"
            ).read_text(encoding="utf-8")
        )
        payload["lifecycle"] = "frozen"
        with self.assertRaisesRegex(ValueError, "source_snapshot_id"):
            BenchmarkTask.model_validate(payload)

        payload["source_snapshot_id"] = "planned-snapshot"
        with self.assertRaisesRegex(ValueError, "unverified claims"):
            BenchmarkTask.model_validate(payload)

    def test_frozen_task_snapshot_must_match_loaded_corpus(self) -> None:
        task = load_task(FIXTURE_TASK).model_copy(
            update={"source_snapshot_id": "wrong-snapshot"}
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "does not match"):
                C0Runner(
                    task=task,
                    corpus=SnapshotCorpus(FIXTURE_MANIFEST),
                    provider=FixtureProvider(),
                    budget=RunBudget(
                        max_model_calls=10,
                        max_tool_calls=20,
                        max_input_tokens=100_000,
                        max_output_tokens=20_000,
                        max_cost_usd=5,
                        max_duration_ms=60_000,
                    ),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="snapshot-mismatch",
                )

    def test_source_requirements_must_match_acceptable_sources(self) -> None:
        payload = json.loads(FIXTURE_TASK.read_text(encoding="utf-8"))
        payload["source_requirements"].pop()
        with self.assertRaisesRegex(ValueError, "must exactly match"):
            BenchmarkTask.model_validate(payload)

    def test_runner_revalidates_programmatically_modified_task(self) -> None:
        task = load_task(FIXTURE_TASK).model_copy(
            update={
                "fixture_only": False,
                "evaluation_mode": EvaluationMode.JUDGE_REQUIRED,
            }
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "wrong scoring_method"):
                C0Runner(
                    task=task,
                    corpus=SnapshotCorpus(FIXTURE_MANIFEST),
                    provider=FixtureProvider(),
                    budget=RunBudget(
                        max_model_calls=10,
                        max_tool_calls=20,
                        max_input_tokens=100_000,
                        max_output_tokens=20_000,
                        max_cost_usd=5,
                        max_duration_ms=60_000,
                    ),
                    max_iterations=10,
                    output_dir=Path(temp_dir) / "run",
                    run_group_id="revalidate-task",
                )

    def test_judge_task_cannot_use_deterministic_evaluator(self) -> None:
        task = load_task(
            DRAFT_DIR / "architecture-01-memory-placement.json"
        )
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "judge_required"):
                evaluate_deterministic_run(
                    task=task,
                    events=[],
                    evidence=EvidenceStore(Path(temp_dir) / "evidence.jsonl"),
                    sequence=0,
                    timestamp=datetime(2026, 6, 22, tzinfo=UTC),
                    parent_event_id=uuid4(),
                )


class FreezePlanTests(TestCase):
    def setUp(self) -> None:
        self.tasks = [
            load_task(path) for path in sorted(DRAFT_DIR.glob("*.json"))
        ]

    def test_plan_deduplicates_three_sources_across_four_tasks(self) -> None:
        plan = build_freeze_plan(self.tasks)
        self.assertEqual(plan["task_count"], 4)
        self.assertEqual(plan["source_count"], 3)
        self.assertFalse(plan["all_tasks_frozen"])
        source_ids = {source["source_id"] for source in plan["sources"]}
        self.assertEqual(
            source_ids,
            {
                "mem0-memory-evaluation",
                "letta-memory-blocks",
                "letta-archival-memory",
            },
        )
        self.assertTrue(
            all(
                len(source["referenced_by_tasks"]) == 3
                for source in plan["sources"]
            )
        )

    def test_conflicting_url_for_same_source_id_is_rejected(self) -> None:
        second = self.tasks[1]
        conflicting_requirement = second.source_requirements[0].model_copy(
            update={"canonical_url": "https://example.com/conflict"}
        )
        conflicting = second.model_copy(
            update={
                "source_requirements": [
                    conflicting_requirement,
                    *second.source_requirements[1:],
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "conflicting canonical_url"):
            build_freeze_plan([self.tasks[0], conflicting])


class EvidencePatternPreflightTests(TestCase):
    def setUp(self) -> None:
        self.task = load_task(FIXTURE_TASK)

    def test_matching_is_casefolded_but_hyphen_sensitive(self) -> None:
        first_claim = self.task.required_claims[0].model_copy(
            update={"evidence_patterns": ["ADD-only", "in context"]}
        )
        task = self.task.model_copy(
            update={
                "required_claims": [
                    first_claim,
                    *self.task.required_claims[1:],
                ]
            }
        )
        report = audit_evidence_patterns(
            [task],
            {
                "src_mem0_docs": "add-ONLY and in-context",
                "src_mem0_paper": "can add an optional graph layer",
            },
        )
        self.assertFalse(report["passed"])
        self.assertEqual(report["patterns"], 3)
        self.assertEqual(report["missing_patterns"], 1)
        self.assertEqual(report["missing"][0]["pattern"], "in context")
        self.assertFalse(report["claims"][0]["passed"])
        self.assertTrue(report["claims"][1]["passed"])
        self.assertRegex(
            report["source_hashes"]["src_mem0_docs"],
            r"^sha256:[0-9a-f]{64}$",
        )

    def test_missing_source_text_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "src_mem0_paper"):
            audit_evidence_patterns(
                [self.task],
                {"src_mem0_docs": "stores memories in a vector store"},
            )

    def test_source_mapping_parser_rejects_duplicates_and_bad_specs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.md"
            source_path.write_text("source text", encoding="utf-8")
            spec = f"source={source_path}"
            self.assertEqual(load_source_texts([spec]), {"source": "source text"})
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_source_texts([spec, spec])
            with self.assertRaisesRegex(ValueError, "expected SOURCE_ID=PATH"):
                load_source_texts(["not-a-mapping"])
