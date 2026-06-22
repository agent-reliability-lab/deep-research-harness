"""Frozen snapshot integrity, BM25 retrieval, and tool-runtime bridge tests."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from urllib.parse import urlparse
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

from src.evidence import EvidenceStore
from src.probes.deepseek_g3_tools import TOOLS as PROBE_TOOLS
from src.snapshots import (
    RedistributionPolicy,
    SnapshotCorpus,
    SnapshotError,
    SnapshotManifest,
    SourceManifestEntry,
    SourceType,
)
from src.snapshots.cli import add_source
from src.snapshots.corpus import content_hash
from src.snapshots.markdown import clean_markdown
from src.snapshots.models import CachedSource
from src.tools import TOOL_SCHEMAS, ToolExecutionError, ToolRuntime
from src.tools.bm25 import BM25Index, tokenize
from src.tools.runtime import locate_grounded_excerpt
from src.trace.models import (
    CallStatus,
    ChatMessage,
    Configuration,
    ModelCallEvent,
    ModelUsage,
    RunBudget,
    RunStartedEvent,
    ToolCallRequest,
    ToolExecutionEvent,
)
from src.trace.store import TraceReader, TraceWriter
from src.trace.validate import validate_trace

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
MODEL = "deepseek-v4-flash"
REPO_ROOT = Path(__file__).resolve().parents[1]
MVP2_MANIFEST = (
    REPO_ROOT
    / "data"
    / "source_snapshots"
    / "memory-architecture-mvp2-2026-06-22-v1"
    / "manifest.json"
)


def create_snapshot(root: Path) -> Path:
    texts = {
        "mem0-docs": (
            "Mem0 stores memories in a vector database. "
            "It supports long-term memory for agents."
        ),
        "letta-docs": "Letta uses memory blocks to maintain agent state.",
    }
    entries = []
    cache_dir = root / "cache"
    cache_dir.mkdir(parents=True)
    for source_id, text in texts.items():
        (cache_dir / f"{source_id}.json").write_text(
            CachedSource(source_id=source_id, cleaned_text=text).model_dump_json(
                indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        entries.append(
            SourceManifestEntry(
                source_id=source_id,
                title=source_id.replace("-", " ").title(),
                canonical_url=f"https://example.com/{source_id}",
                retrieved_at=NOW,
                content_hash=content_hash(text),
                excerpt=text,
                source_type=SourceType.OFFICIAL_DOCS,
                version_or_pub_date="2026-06-21",
                redistribution_policy=RedistributionPolicy.CACHE_ONLY,
                cache_relpath=f"cache/{source_id}.json",
            )
        )
    manifest = SnapshotManifest(
        snapshot_id="test-snapshot",
        created_at=NOW,
        sources=entries,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def append_run_start(writer: TraceWriter, run_id: UUID) -> RunStartedEvent:
    event = RunStartedEvent(
        run_id=run_id,
        sequence=writer.next_sequence,
        timestamp=NOW,
        run_group_id="test-run-group",
        task_id="test-task",
        task_version="1.0.0",
        rubric_version="1.0.0",
        configuration=Configuration.C0,
        provider="deepseek",
        endpoint_class="openai-compatible",
        requested_model=MODEL,
        model_parameters={"temperature": 0},
        source_snapshot_id="test-snapshot",
        pricing_version="test-pricing",
        budget=RunBudget(
            max_model_calls=20,
            max_tool_calls=40,
            max_input_tokens=100_000,
            max_output_tokens=20_000,
            max_cost_usd=Decimal("5"),
            max_duration_ms=600_000,
        ),
    )
    writer.append(event)
    return event


def append_model_call(
    writer: TraceWriter,
    run_id: UUID,
    *,
    call_index: int,
    tool_name: str,
    arguments: dict,
    parent_event_id: UUID,
) -> ModelCallEvent:
    event = ModelCallEvent(
        run_id=run_id,
        sequence=writer.next_sequence,
        timestamp=NOW,
        parent_event_id=parent_event_id,
        call_id=f"model-{call_index}",
        status=CallStatus.SUCCESS,
        requested_model=MODEL,
        returned_model=MODEL,
        provider_request_id=f"request-{call_index}",
        request_messages=[ChatMessage(role="user", content="Continue research.")],
        tool_schemas=TOOL_SCHEMAS,
        response_tool_calls=[
            ToolCallRequest(
                id=f"tool-{call_index}",
                name=tool_name,
                arguments=arguments,
            )
        ],
        usage=ModelUsage(input_tokens=100, output_tokens=20),
        latency_ms=10,
    )
    writer.append(event)
    return event


class SnapshotCorpusTests(TestCase):
    def test_mvp2_public_manifest_is_versioned_and_official(self) -> None:
        manifest = SnapshotManifest.model_validate_json(
            MVP2_MANIFEST.read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest.snapshot_id,
            "memory-architecture-mvp2-2026-06-22-v1",
        )
        self.assertEqual(len(manifest.sources), 17)
        self.assertEqual(
            {source.source_type for source in manifest.sources},
            {SourceType.OFFICIAL_DOCS, SourceType.RESEARCH_PAPER},
        )
        self.assertTrue(
            all(
                source.redistribution_policy
                is RedistributionPolicy.CACHE_ONLY
                for source in manifest.sources
            )
        )
        self.assertEqual(
            {
                urlparse(str(source.canonical_url)).hostname
                for source in manifest.sources
            },
            {
                "arxiv.org",
                "docs.cognee.ai",
                "docs.letta.com",
                "docs.mem0.ai",
                "help.getzep.com",
            },
        )

    def test_markdown_cleaner_preserves_visible_text_and_is_idempotent(
        self,
    ) -> None:
        raw = """---
title: Example
---
> ## Documentation Index
> Fetch the index.

# Memory

<Info>
The key is **ADD-only extraction** with [semantic search](https://example.com).
</Info>

Use `record_evidence` with in-context facts.
"""
        cleaned = clean_markdown(raw)
        self.assertEqual(
            cleaned,
            "Memory\n\n"
            "The key is ADD-only extraction with semantic search.\n\n"
            "Use record_evidence with in-context facts.\n",
        )
        self.assertEqual(clean_markdown(cleaned), cleaned)

    def test_add_source_accepts_explicit_public_excerpt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cleaned_text = root / "source.md"
            cleaned_text.write_text(
                "---\ntitle: metadata\n---\nUseful source body.",
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            with redirect_stdout(StringIO()):
                add_source(
                    SimpleNamespace(
                        manifest=manifest_path,
                        cleaned_text=cleaned_text,
                        source_id="official-source",
                        title="Official source",
                        url="https://example.com/official",
                        retrieved_at="2026-06-22T00:00:00Z",
                        source_type=SourceType.OFFICIAL_DOCS,
                        version_or_pub_date=None,
                        redistribution_policy=RedistributionPolicy.CACHE_ONLY,
                        language="en",
                        license=None,
                        excerpt="Useful source body.",
                        excerpt_chars=500,
                    )
                )
            manifest = SnapshotManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest.sources[0].excerpt, "Useful source body.")
            SnapshotCorpus(manifest_path).verify_all()

    def test_cache_hash_is_verified_and_tampering_fails_closed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = create_snapshot(root)
            corpus = SnapshotCorpus(manifest_path)
            corpus.verify_all()
            self.assertIn("vector database", corpus.document("mem0-docs").cleaned_text)

            cache_path = root / "cache" / "mem0-docs.json"
            cache_path.write_text(
                CachedSource(
                    source_id="mem0-docs",
                    cleaned_text="tampered text",
                ).model_dump_json(),
                encoding="utf-8",
            )
            reopened = SnapshotCorpus(manifest_path)
            with self.assertRaisesRegex(SnapshotError, "hash mismatch"):
                reopened.document("mem0-docs")

    def test_missing_cache_explains_hash_locked_refetch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = create_snapshot(root)
            (root / "cache" / "mem0-docs.json").unlink()
            with self.assertRaisesRegex(SnapshotError, "resulting hash matches"):
                SnapshotCorpus(manifest_path).document("mem0-docs")

    def test_manifest_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            SourceManifestEntry(
                source_id="bad-source",
                title="Bad source",
                canonical_url="https://example.com/bad",
                retrieved_at=NOW,
                content_hash="sha256:" + "a" * 64,
                excerpt="short excerpt",
                source_type=SourceType.OFFICIAL_DOCS,
                version_or_pub_date=None,
                redistribution_policy=RedistributionPolicy.CACHE_ONLY,
                cache_relpath="../outside.json",
            )


class SearchAndSchemaTests(TestCase):
    def test_bm25_ranks_matching_source_and_tokenizes_chinese(self) -> None:
        index = BM25Index(
            {
                "mem0": "Mem0 uses a vector database for agent memory.",
                "letta": "Letta uses memory blocks.",
            }
        )
        self.assertEqual(index.search("vector database")[0].source_id, "mem0")
        self.assertIn("长期", tokenize("长期记忆"))

    def test_probe_and_runtime_share_meta_valid_tool_schemas(self) -> None:
        self.assertIs(PROBE_TOOLS, TOOL_SCHEMAS)
        for tool in TOOL_SCHEMAS:
            Draft202012Validator.check_schema(tool["function"]["parameters"])


class ToolRuntimeTests(TestCase):
    def test_evidence_locator_accepts_only_unique_typography_variants(
        self,
    ) -> None:
        source = "The agent’s state is always visible - no retrieval needed."
        proposed = "The agent's state is always visible — no retrieval needed."
        self.assertEqual(locate_grounded_excerpt(source, proposed), source)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            locate_grounded_excerpt(
                "The agent’s state. The agent‘s state.",
                "The agent's state.",
            )
        with self.assertRaisesRegex(ValueError, "must appear"):
            locate_grounded_excerpt(source, "The agent state is sometimes visible.")

    def test_five_tools_emit_a_valid_trace_and_grounded_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = SnapshotCorpus(create_snapshot(root / "snapshot"))
            run_id = uuid4()
            trace_path = root / "run" / "trace.jsonl"
            evidence_path = root / "run" / "evidence.jsonl"
            writer = TraceWriter(trace_path, run_id)
            evidence = EvidenceStore(evidence_path)
            previous = append_run_start(writer, run_id)
            runtime = ToolRuntime(
                run_id=run_id,
                corpus=corpus,
                trace=writer,
                evidence=evidence,
                artifact_dir=root / "run" / "artifacts",
            )

            calls = [
                (
                    "search_sources",
                    {"query": "vector database", "max_results": 2},
                ),
                ("read_source", {"source_id": "mem0-docs"}),
                (
                    "record_evidence",
                    {
                        "source_id": "mem0-docs",
                        "claim": "Mem0 stores memories in a vector database.",
                        "excerpt": "Mem0 stores memories in a vector database.",
                    },
                ),
                (
                    "check_contradiction",
                    {
                        "claim_a": "Mem0 stores memories.",
                        "claim_b": "not Mem0 stores memories",
                    },
                ),
            ]
            results = []
            for index, (tool_name, arguments) in enumerate(calls, start=1):
                model_event = append_model_call(
                    writer,
                    run_id,
                    call_index=index,
                    tool_name=tool_name,
                    arguments=arguments,
                    parent_event_id=previous.event_id,
                )
                result = runtime.execute(
                    tool_call_id=f"tool-{index}",
                    tool_name=tool_name,
                    arguments=arguments,
                    parent_event_id=model_event.event_id,
                )
                results.append(result)
                previous = TraceReader(trace_path).read_all()[-1]

            evidence_id = results[2]["evidence_id"]
            finalize_arguments = {
                "summary": "Mem0 uses vector storage for agent memory.",
                "evidence_ids": [evidence_id],
            }
            model_event = append_model_call(
                writer,
                run_id,
                call_index=5,
                tool_name="finalize",
                arguments=finalize_arguments,
                parent_event_id=previous.event_id,
            )
            final_result = runtime.execute(
                tool_call_id="tool-5",
                tool_name="finalize",
                arguments=finalize_arguments,
                parent_event_id=model_event.event_id,
            )

            events = validate_trace(trace_path, evidence_path)
            self.assertEqual(results[0]["results"][0]["source_id"], "mem0-docs")
            self.assertEqual(results[0]["source_ids"][0], "mem0-docs")
            # Agent-facing read results exclude duplicate text and low-signal
            # provenance fields, while the trace retains audit metadata.
            self.assertNotIn("text", results[1])
            self.assertNotIn("content_hash", results[1])
            self.assertNotIn("retrieved_at", results[1])
            self.assertTrue(results[1]["cleaned_text"])
            read_event = next(
                event
                for event in events
                if isinstance(event, ToolExecutionEvent)
                and event.tool_name == "read_source"
            )
            self.assertEqual(
                read_event.result["content_hash"],
                corpus.entry("mem0-docs").content_hash,
            )
            self.assertEqual(
                read_event.result["retrieved_at"],
                corpus.entry("mem0-docs").retrieved_at.isoformat(),
            )
            self.assertTrue(results[3]["contradiction"])
            self.assertEqual(len(evidence.all()), 1)
            self.assertEqual(len(events), 13)
            self.assertEqual(final_result["status"], "ok")
            self.assertTrue(Path(final_result["artifact_path"]).exists())

    def test_ungrounded_excerpt_emits_error_trace_without_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = SnapshotCorpus(create_snapshot(root / "snapshot"))
            run_id = uuid4()
            trace_path = root / "trace.jsonl"
            writer = TraceWriter(trace_path, run_id)
            evidence = EvidenceStore(root / "evidence.jsonl")
            start = append_run_start(writer, run_id)
            arguments = {
                "source_id": "mem0-docs",
                "claim": "An unsupported claim.",
                "excerpt": "This sentence is not in the source.",
            }
            model_event = append_model_call(
                writer,
                run_id,
                call_index=1,
                tool_name="record_evidence",
                arguments=arguments,
                parent_event_id=start.event_id,
            )
            runtime = ToolRuntime(
                run_id=run_id,
                corpus=corpus,
                trace=writer,
                evidence=evidence,
                artifact_dir=root / "artifacts",
            )
            with self.assertRaisesRegex(ToolExecutionError, "appear verbatim"):
                runtime.execute(
                    tool_call_id="tool-1",
                    tool_name="record_evidence",
                    arguments=arguments,
                    parent_event_id=model_event.event_id,
                )

            events = validate_trace(trace_path, evidence)
            self.assertEqual(events[-1].status, CallStatus.ERROR)
            self.assertEqual(evidence.all(), [])

    def test_finalize_schema_rejects_duplicate_evidence_ids(self) -> None:
        schema = next(
            tool["function"]["parameters"]
            for tool in TOOL_SCHEMAS
            if tool["function"]["name"] == "finalize"
        )
        duplicate = "00000000-0000-0000-0000-000000000001"
        errors = list(
            Draft202012Validator(schema).iter_errors(
                {
                    "summary": "summary",
                    "evidence_ids": [duplicate, duplicate],
                }
            )
        )
        self.assertTrue(any("non-unique" in error.message for error in errors))
