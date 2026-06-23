"""C1 active-context compaction with reloadable frozen-source pointers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import ConfigDict, Field
from pydantic.main import BaseModel

from src.evidence import EvidenceStore
from src.trace.models import (
    ChatMessage,
    CheckpointState,
)


class CompactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1.0"
    enabled: bool = True
    trigger_fraction: float = Field(default=0.60, gt=0, lt=1)
    strategy: Literal["tool_result_clearing"] = "tool_result_clearing"
    max_compactions: int = Field(default=4, ge=1)

    def trigger_tokens(self, max_active_context_tokens: int | None) -> int:
        if max_active_context_tokens is None:
            raise ValueError(
                "C1 compaction requires max_active_context_tokens"
            )
        return max(1, int(max_active_context_tokens * self.trigger_fraction))


def load_compaction_config(path: str | Path) -> CompactionConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("compaction config must contain a YAML mapping")
    return CompactionConfig.model_validate(payload)


@dataclass(frozen=True)
class CompactionPlan:
    messages: list[ChatMessage]
    checkpoint_state: CheckpointState
    state_hash: str
    input_tokens: int
    required_fact_ids: list[str]
    preserved_fact_ids: list[str]
    cleared_source_ids: list[str]


@dataclass(frozen=True)
class PendingCompaction:
    checkpoint_id: UUID
    input_tokens: int
    required_fact_ids: list[str]
    preserved_fact_ids: list[str]
    strategy: str


def should_compact(
    *,
    config: CompactionConfig,
    max_active_context_tokens: int | None,
    last_model_input_tokens: int | None,
    completed_compactions: int,
) -> bool:
    if not config.enabled or last_model_input_tokens is None:
        return False
    if completed_compactions >= config.max_compactions:
        return False
    return last_model_input_tokens >= config.trigger_tokens(
        max_active_context_tokens
    )


def build_tool_result_clearing_plan(
    *,
    messages: list[ChatMessage],
    evidence: EvidenceStore,
    input_tokens: int,
    task_prompt: str,
    compaction_index: int,
) -> CompactionPlan | None:
    tool_names = _tool_names_by_call_id(messages)
    compacted: list[ChatMessage] = []
    cleared_source_ids: list[str] = []
    reloadable_source_ids: list[str] = []
    for message in messages:
        replacement = message
        if (
            message.role == "tool"
            and message.tool_call_id
            and tool_names.get(message.tool_call_id) == "read_source"
        ):
            payload = _object_payload(message.content)
            source_id = payload.get("source_id")
            if source_id:
                reloadable_source_ids.append(str(source_id))
                if "cleaned_text" in payload:
                    cleared_source_ids.append(str(source_id))
                    replacement = message.model_copy(
                        update={
                            "content": json.dumps(
                                {
                                    "source_id": source_id,
                                    "compacted": True,
                                    "reload_with": "read_source",
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        }
                    )
        compacted.append(replacement)

    if not cleared_source_ids:
        return None

    records = evidence.all()
    evidence_ids = sorted(
        (record.evidence_id for record in records),
        key=str,
    )
    source_to_claim: dict[str, list[str]] = {}
    for record in records:
        source_to_claim.setdefault(record.source_id, []).append(record.claim)
    source_facts = {
        f"source:{source_id}" for source_id in reloadable_source_ids
    }
    evidence_facts = {
        f"evidence:{evidence_id}" for evidence_id in evidence_ids
    }
    required_fact_ids = sorted(source_facts | evidence_facts)
    checkpoint_state = CheckpointState(
        plan=["research frozen sources", "record evidence", "finalize report"],
        completed_steps=[
            f"cleared {len(source_facts)} read_source result(s)"
        ],
        unresolved_questions=[],
        evidence_ids=evidence_ids,
        source_to_claim={
            source_id: sorted(claims)
            for source_id, claims in sorted(source_to_claim.items())
        },
        user_constraints=[task_prompt],
        permission_decision_event_ids=[],
        failures=[],
        retry_state={
            "compaction_index": compaction_index,
            "reloadable_source_ids": sorted(set(reloadable_source_ids)),
        },
    )
    canonical_state = json.dumps(
        checkpoint_state.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    state_hash = "sha256:" + hashlib.sha256(
        canonical_state.encode("utf-8")
    ).hexdigest()
    return CompactionPlan(
        messages=compacted,
        checkpoint_state=checkpoint_state,
        state_hash=state_hash,
        input_tokens=input_tokens,
        required_fact_ids=required_fact_ids,
        preserved_fact_ids=required_fact_ids,
        cleared_source_ids=sorted(set(cleared_source_ids)),
    )


def _tool_names_by_call_id(
    messages: list[ChatMessage],
) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        for call in message.tool_calls or []:
            names[call.id] = call.name
    return names


def _object_payload(
    content: str | list[dict] | None,
) -> dict:
    if not isinstance(content, str):
        return {}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
