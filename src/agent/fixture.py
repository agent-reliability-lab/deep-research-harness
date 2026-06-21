"""Deterministic provider used only for C0 integration fixtures."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from src.trace.models import (
    CallCost,
    ChatMessage,
    ModelUsage,
    ToolCallRequest,
)

from .provider import ModelCompletion


class FixtureProvider:
    provider_name = "fixture"
    endpoint_class = "offline-scripted"
    model = "fixture-react-v1"
    model_parameters = {"deterministic": True}
    pricing_version = "fixture-zero-cost-v1"

    def __init__(self) -> None:
        self.call_count = 0

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        *,
        max_output_tokens: int,
    ) -> ModelCompletion:
        del tools, max_output_tokens
        self.call_count += 1
        prior_tool_names = [
            call.name
            for message in messages
            if message.role == "assistant" and message.tool_calls
            for call in message.tool_calls
        ]
        tool_results = [
            json.loads(str(message.content))
            for message in messages
            if message.role == "tool"
        ]

        if not prior_tool_names:
            name = "search_sources"
            arguments = {"query": "Mem0 vector store optional graph", "max_results": 2}
        elif prior_tool_names == ["search_sources"]:
            name = "read_source"
            arguments = {"source_id": "src_mem0_docs"}
        elif prior_tool_names == ["search_sources", "read_source"]:
            name = "record_evidence"
            arguments = {
                "source_id": "src_mem0_docs",
                "claim": "Mem0 stores memories in a vector store.",
                "excerpt": "Mem0 stores memories in a vector store.",
                "confidence": 1.0,
            }
        elif prior_tool_names == [
            "search_sources",
            "read_source",
            "record_evidence",
        ]:
            name = "read_source"
            arguments = {"source_id": "src_mem0_paper"}
        elif prior_tool_names == [
            "search_sources",
            "read_source",
            "record_evidence",
            "read_source",
        ]:
            name = "record_evidence"
            arguments = {
                "source_id": "src_mem0_paper",
                "claim": "Mem0 can add an optional graph layer.",
                "excerpt": "Mem0 can add an optional graph layer.",
                "confidence": 1.0,
            }
        else:
            evidence_ids = [
                result["evidence_id"]
                for result in tool_results
                if isinstance(result, dict) and "evidence_id" in result
            ]
            for evidence_id in evidence_ids:
                UUID(evidence_id)
            name = "finalize"
            arguments = {
                "summary": (
                    "Mem0 stores memories in a vector store and can add an "
                    "optional graph layer."
                ),
                "evidence_ids": evidence_ids,
            }

        call = ToolCallRequest(
            id=f"fixture-tool-{self.call_count}",
            name=name,
            arguments=arguments,
        )
        input_tokens = max(1, sum(len(str(message.content or "")) for message in messages) // 4)
        return ModelCompletion(
            returned_model=self.model,
            provider_request_id=f"fixture-request-{self.call_count}",
            system_fingerprint="fixture-fingerprint-v1",
            content=None,
            tool_calls=[call],
            usage=ModelUsage(input_tokens=input_tokens, output_tokens=32),
            cost=CallCost(
                input_usd=Decimal("0"),
                output_usd=Decimal("0"),
            ),
            latency_ms=0,
        )
