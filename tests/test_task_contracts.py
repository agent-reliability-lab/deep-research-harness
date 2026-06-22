"""Real benchmark task lifecycle and source-freeze plan tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.agent.fixture import FixtureProvider
from src.agent.runner import C0Runner
from src.snapshots import SnapshotCorpus
from src.tasks.cli import build_freeze_plan
from src.tasks.load import load_task
from src.tasks.models import (
    BenchmarkTask,
    ClaimVerificationStatus,
    TaskLifecycle,
)
from src.trace.models import RunBudget

ROOT = Path(__file__).resolve().parents[1]
DRAFT_DIR = ROOT / "data" / "tasks" / "drafts"
FIXTURE_TASK = ROOT / "data" / "fixtures" / "tasks" / "mem0-architecture.json"
FIXTURE_MANIFEST = (
    ROOT / "data" / "fixtures" / "source_snapshots" / "manifest.json"
)


class TaskLifecycleTests(TestCase):
    def test_two_real_drafts_load_but_are_not_freeze_ready(self) -> None:
        tasks = [load_task(path) for path in sorted(DRAFT_DIR.glob("*.json"))]
        self.assertEqual(len(tasks), 2)
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


class FreezePlanTests(TestCase):
    def setUp(self) -> None:
        self.tasks = [
            load_task(path) for path in sorted(DRAFT_DIR.glob("*.json"))
        ]

    def test_plan_deduplicates_three_sources_across_two_tasks(self) -> None:
        plan = build_freeze_plan(self.tasks)
        self.assertEqual(plan["task_count"], 2)
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
                len(source["referenced_by_tasks"]) == 2
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
