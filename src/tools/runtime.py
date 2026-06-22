"""Tool runtime bridging frozen sources to trace and evidence storage."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker

from src.evidence import EvidenceRecord, EvidenceStore
from src.snapshots import SnapshotCorpus
from src.trace.models import (
    CallStatus,
    EvidenceRecordedEvent,
    FinalReportEvent,
    ToolExecutionEvent,
)
from src.trace.store import TraceWriter

from .bm25 import BM25Index
from .schemas import TOOL_SCHEMAS

_TYPOGRAPHY_TRANSLATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)


def locate_grounded_excerpt(source_text: str, proposed_excerpt: str) -> str:
    """Resolve exact source text, allowing only unique typography variants."""

    proposed = proposed_excerpt.strip()
    if proposed in source_text:
        return proposed
    normalized_source = source_text.translate(_TYPOGRAPHY_TRANSLATION)
    normalized_proposed = proposed.translate(_TYPOGRAPHY_TRANSLATION)
    first = normalized_source.find(normalized_proposed)
    if first < 0:
        raise ValueError(
            "evidence excerpt must appear verbatim or as a unique typography "
            "variant in the frozen source text"
        )
    second = normalized_source.find(normalized_proposed, first + 1)
    if second >= 0:
        raise ValueError(
            "typography-normalized evidence excerpt is ambiguous in the "
            "frozen source text"
        )
    return source_text[first : first + len(proposed)]


class ToolExecutionError(RuntimeError):
    """A tool failed after its failure event was written."""


class ToolRuntime:
    def __init__(
        self,
        *,
        run_id: UUID,
        corpus: SnapshotCorpus,
        trace: TraceWriter,
        evidence: EvidenceStore,
        artifact_dir: str | Path,
        within_budget: Callable[[], bool] | None = None,
    ) -> None:
        if trace.run_id != run_id:
            raise ValueError("trace writer run_id does not match tool runtime")
        self.run_id = run_id
        self.corpus = corpus
        self.trace = trace
        self.evidence = evidence
        self.artifact_dir = Path(artifact_dir)
        self.within_budget = within_budget or (lambda: True)
        documents = {
            entry.source_id: corpus.document(entry.source_id).cleaned_text
            for entry in corpus.entries()
        }
        self.index = BM25Index(documents)
        self.schemas = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in TOOL_SCHEMAS
        }

    def execute(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        parent_event_id: UUID,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            self._validate_arguments(tool_name, arguments)
            result, source_ids = self._dispatch(tool_name, arguments)
        except Exception as exc:
            event = ToolExecutionEvent(
                run_id=self.run_id,
                sequence=self.trace.next_sequence,
                timestamp=self._now(),
                parent_event_id=parent_event_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status=CallStatus.ERROR,
                arguments=arguments,
                latency_ms=self._latency_ms(started),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            self.trace.append(event)
            raise ToolExecutionError(str(exc)) from exc

        tool_event = ToolExecutionEvent(
            run_id=self.run_id,
            sequence=self.trace.next_sequence,
            timestamp=self._now(),
            parent_event_id=parent_event_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=CallStatus.SUCCESS,
            arguments=arguments,
            result=result,
            latency_ms=self._latency_ms(started),
            source_ids=source_ids,
        )
        self.trace.append(tool_event)
        self._append_followup_events(
            tool_name=tool_name,
            result=result,
            tool_event=tool_event,
        )
        return self._agent_facing_result(tool_name, result)

    @staticmethod
    def _agent_facing_result(
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name != "read_source":
            return result
        return {
            key: value
            for key, value in result.items()
            if key not in {"content_hash", "retrieved_at"}
        }

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> None:
        try:
            schema = self.schemas[tool_name]
        except KeyError as exc:
            raise ValueError(f"unknown tool {tool_name}") from exc
        errors = sorted(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(arguments),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            details = "; ".join(error.message for error in errors)
            raise ValueError(f"invalid arguments for {tool_name}: {details}")

    def _dispatch(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        if tool_name == "search_sources":
            return self._search_sources(**arguments)
        if tool_name == "read_source":
            return self._read_source(**arguments)
        if tool_name == "record_evidence":
            return self._prepare_evidence(**arguments)
        if tool_name == "check_contradiction":
            return self._check_contradiction(**arguments)
        if tool_name == "finalize":
            return self._prepare_final_report(**arguments)
        raise ValueError(f"unknown tool {tool_name}")

    def _search_sources(
        self,
        query: str,
        max_results: int = 5,
    ) -> tuple[dict[str, Any], list[str]]:
        results = self.index.search(query, max_results=max_results)
        payload = []
        for result in results:
            entry = self.corpus.entry(result.source_id)
            payload.append(
                {
                    "source_id": entry.source_id,
                    "title": entry.title,
                    "canonical_url": str(entry.canonical_url),
                    "score": round(result.score, 8),
                    "excerpt": entry.excerpt,
                }
            )
        source_ids = [item.source_id for item in results]
        return {
            "source_ids": source_ids,
            "results": payload,
        }, source_ids

    def _read_source(self, source_id: str) -> tuple[dict[str, Any], list[str]]:
        entry = self.corpus.entry(source_id)
        document = self.corpus.document(source_id)
        return (
            {
                "source_id": source_id,
                "title": entry.title,
                "canonical_url": str(entry.canonical_url),
                "retrieved_at": entry.retrieved_at.isoformat(),
                "content_hash": entry.content_hash,
                "cleaned_text": document.cleaned_text,
            },
            [source_id],
        )

    def _prepare_evidence(
        self,
        source_id: str,
        claim: str,
        excerpt: str | None = None,
        confidence: float = 0.5,
    ) -> tuple[dict[str, Any], list[str]]:
        entry = self.corpus.entry(source_id)
        text = self.corpus.document(source_id).cleaned_text
        grounded_excerpt = locate_grounded_excerpt(text, excerpt or claim)
        evidence_id = uuid4()
        evidence_event_id = uuid4()
        source_date = self._parse_source_date(entry.version_or_pub_date)
        record = EvidenceRecord(
            evidence_id=evidence_id,
            run_id=self.run_id,
            claim=claim,
            source_id=source_id,
            source_url=entry.canonical_url,
            retrieved_at=entry.retrieved_at,
            evidence_excerpt=grounded_excerpt,
            source_date=source_date,
            confidence=confidence,
            source_content_hash=entry.content_hash,
            created_event_id=evidence_event_id,
        )
        self.evidence.append(record)
        return (
            {
                "evidence_id": str(evidence_id),
                "evidence_event_id": str(evidence_event_id),
                "source_id": source_id,
            },
            [source_id],
        )

    def _check_contradiction(
        self,
        claim_a: str,
        claim_b: str,
    ) -> tuple[dict[str, Any], list[str]]:
        normalized_a = self._normalize_claim(claim_a)
        normalized_b = self._normalize_claim(claim_b)
        if normalized_a == normalized_b:
            return (
                {
                    "contradiction": False,
                    "judge_required": False,
                    "reason": "claims normalize to the same text",
                },
                [],
            )
        if normalized_a == f"not {normalized_b}" or normalized_b == f"not {normalized_a}":
            return (
                {
                    "contradiction": True,
                    "judge_required": False,
                    "reason": "one normalized claim explicitly negates the other",
                },
                [],
            )
        return (
            {
                "contradiction": None,
                "judge_required": True,
                "reason": "deterministic rules are insufficient",
            },
            [],
        )

    def _prepare_final_report(
        self,
        summary: str,
        evidence_ids: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        parsed_ids = [UUID(item) for item in evidence_ids]
        if len(parsed_ids) != len(set(parsed_ids)):
            raise ValueError("finalize evidence_ids must be unique")
        records = [self.evidence.get(evidence_id) for evidence_id in parsed_ids]
        lines = ["# Research report", "", summary, "", "## Evidence"]
        for record in records:
            lines.append(
                f"- [{record.evidence_id}] {record.claim} "
                f"([source]({record.source_url}))"
            )
        content = "\n".join(lines) + "\n"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / "final-report.md"
        path.write_text(content, encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        return (
            {
                "status": "ok",
                "artifact_path": str(path),
                "content_hash": digest,
                "evidence_ids": [str(item) for item in parsed_ids],
                "summary": summary,
            },
            [record.source_id for record in records],
        )

    def _append_followup_events(
        self,
        *,
        tool_name: str,
        result: dict[str, Any],
        tool_event: ToolExecutionEvent,
    ) -> None:
        if tool_name == "record_evidence":
            evidence_event = EvidenceRecordedEvent(
                event_id=UUID(result["evidence_event_id"]),
                run_id=self.run_id,
                sequence=self.trace.next_sequence,
                timestamp=self._now(),
                parent_event_id=tool_event.event_id,
                evidence_id=UUID(result["evidence_id"]),
                source_id=result["source_id"],
            )
            self.trace.append(evidence_event)
        elif tool_name == "finalize":
            final_event = FinalReportEvent(
                run_id=self.run_id,
                sequence=self.trace.next_sequence,
                timestamp=self._now(),
                parent_event_id=tool_event.event_id,
                artifact_path=result["artifact_path"],
                content_hash=result["content_hash"],
                cited_evidence_ids=[
                    UUID(item) for item in result["evidence_ids"]
                ],
                produced_within_budget=self.within_budget(),
            )
            self.trace.append(final_event)

    @staticmethod
    def _parse_source_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _normalize_claim(value: str) -> str:
        return " ".join(value.lower().strip().rstrip(".").split())

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
