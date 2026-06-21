"""Official DeepSeek OpenAI-compatible adapter with explicit pricing."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field
from pydantic.main import BaseModel

from src.probes.common import (
    DEEPSEEK_NON_THINKING,
    DEFAULT_DEEPSEEK_MODEL,
    deepseek_client,
    usage_dict,
)
from src.trace.models import (
    CallCost,
    ChatMessage,
    ModelUsage,
    ToolCallRequest,
)

from .provider import ModelCompletion, ModelProtocolError

MILLION = Decimal("1000000")


class TokenPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pricing_version: str = Field(min_length=1)
    uncached_input_usd_per_million: Decimal = Field(ge=0)
    cache_hit_input_usd_per_million: Decimal = Field(ge=0)
    output_usd_per_million: Decimal = Field(ge=0)


def load_pricing(path: str | Path) -> TokenPricing:
    return TokenPricing.model_validate_json(Path(path).read_text(encoding="utf-8"))


class DeepSeekProvider:
    provider_name = "deepseek"
    endpoint_class = "openai-compatible"

    def __init__(
        self,
        *,
        pricing: TokenPricing,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        client=None,
        temperature: float = 0,
        max_tokens: int = 4096,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.pricing = pricing
        self.pricing_version = pricing.pricing_version
        self.model = model
        self.client = client or deepseek_client()
        self.model_parameters = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": "disabled",
        }

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        *,
        max_output_tokens: int,
    ) -> ModelCompletion:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        started = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[self._message_payload(message) for message in messages],
            tools=tools,
            tool_choice="auto",
            temperature=self.model_parameters["temperature"],
            max_tokens=min(
                self.model_parameters["max_tokens"],
                max_output_tokens,
            ),
            extra_body=DEEPSEEK_NON_THINKING,
        )
        choice = response.choices[0]
        message = choice.message
        usage_payload = usage_dict(response.usage)
        input_tokens = int(usage_payload.get("prompt_tokens") or 0)
        output_tokens = int(usage_payload.get("completion_tokens") or 0)
        cache_hit_tokens = int(
            usage_payload.get("prompt_cache_hit_tokens")
            or usage_payload.get("cache_hit_tokens")
            or 0
        )
        cache_miss_tokens = int(
            usage_payload.get("prompt_cache_miss_tokens")
            or usage_payload.get("cache_miss_tokens")
            or 0
        )
        cache_fields_reported = any(
            key in usage_payload
            for key in (
                "prompt_cache_hit_tokens",
                "cache_hit_tokens",
                "prompt_cache_miss_tokens",
                "cache_miss_tokens",
            )
        )
        usage = ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )
        uncached_tokens = max(0, input_tokens - cache_hit_tokens)
        cost = CallCost(
            input_usd=(
                Decimal(uncached_tokens)
                * self.pricing.uncached_input_usd_per_million
                / MILLION
            ),
            cache_usd=(
                Decimal(cache_hit_tokens)
                * self.pricing.cache_hit_input_usd_per_million
                / MILLION
            ),
            output_usd=(
                Decimal(output_tokens)
                * self.pricing.output_usd_per_million
                / MILLION
            ),
        )
        request_id = getattr(response, "_request_id", None) or getattr(
            response,
            "id",
            None,
        )
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        partial = ModelCompletion(
            returned_model=response.model,
            provider_request_id=request_id or "",
            system_fingerprint=getattr(response, "system_fingerprint", None),
            content=message.content,
            tool_calls=[],
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
        )
        if not request_id:
            raise ModelProtocolError(
                "provider response did not include a request ID",
                partial_completion=partial,
            )
        if cache_hit_tokens > input_tokens:
            raise ModelProtocolError(
                "reported cache-hit tokens exceed total prompt tokens",
                partial_completion=partial,
            )
        if (
            cache_fields_reported
            and cache_hit_tokens + cache_miss_tokens != input_tokens
        ):
            raise ModelProtocolError(
                "cache-hit plus cache-miss tokens do not reconcile to prompt tokens",
                partial_completion=partial,
            )
        tool_calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ModelProtocolError(
                    f"tool {call.function.name} returned invalid JSON arguments: {exc}",
                    partial_completion=partial,
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelProtocolError(
                    f"tool {call.function.name} arguments must decode to an object",
                    partial_completion=partial,
                )
            tool_calls.append(
                ToolCallRequest(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
        return ModelCompletion(
            returned_model=response.model,
            provider_request_id=request_id,
            system_fingerprint=getattr(response, "system_fingerprint", None),
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        return payload
