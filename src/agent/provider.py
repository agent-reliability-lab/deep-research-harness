"""Normalized model-provider boundary for C0-C3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.trace.models import (
    CallCost,
    ChatMessage,
    ModelUsage,
    ToolCallRequest,
)


class ModelProtocolError(ValueError):
    """The provider returned a response that cannot enter the tool protocol."""

    def __init__(
        self,
        message: str,
        *,
        partial_completion: ModelCompletion | None = None,
    ) -> None:
        self.partial_completion = partial_completion
        super().__init__(message)


@dataclass(frozen=True)
class ModelCompletion:
    returned_model: str
    provider_request_id: str
    system_fingerprint: str | None
    content: str | list[dict] | None
    tool_calls: list[ToolCallRequest]
    usage: ModelUsage
    cost: CallCost
    latency_ms: int


class ModelProvider(Protocol):
    provider_name: str
    endpoint_class: str
    model: str
    model_parameters: dict
    pricing_version: str

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        *,
        max_output_tokens: int,
    ) -> ModelCompletion:
        """Return one normalized completion or raise a provider exception."""
