"""Versioned event schemas shared by C0-C3."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic.main import BaseModel


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Configuration(StrEnum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"


class EvaluationScope(StrEnum):
    PRIMARY = "primary"
    EXTERNAL_VALIDITY = "external_validity"
    DEVELOPMENT = "development"
    FIXTURE = "fixture"


class EvaluationStatus(StrEnum):
    EVAL_VALID = "eval_valid"
    AGENT_FAILED = "agent_failed"
    POLICY_BLOCKED_EXPECTED = "policy_blocked_expected"
    INFRA_API_FAILED = "infra_api_failed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    JUDGE_REQUIRED = "judge_required"


class CallStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class PermissionAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionVerdict(StrEnum):
    CORRECT_ALLOW = "correct_allow"
    CORRECT_ASK = "correct_ask"
    CORRECT_DENY = "correct_deny"
    OVER_BLOCK = "over_block"
    UNDER_BLOCK = "under_block"


class RunBudget(StrictModel):
    max_model_calls: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_cost_usd: Decimal = Field(ge=0)
    max_duration_ms: int = Field(gt=0)


class ToolCallRequest(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallRequest] | None = None


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(default=0, ge=0)
    cache_miss_tokens: int = Field(default=0, ge=0)


class CallCost(StrictModel):
    input_usd: Decimal = Field(default=Decimal("0"), ge=0)
    output_usd: Decimal = Field(default=Decimal("0"), ge=0)
    cache_usd: Decimal = Field(default=Decimal("0"), ge=0)

    @property
    def total_usd(self) -> Decimal:
        return self.input_usd + self.output_usd + self.cache_usd


class EventBase(StrictModel):
    schema_version: str = "0.1.0"
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(ge=0)
    timestamp: datetime
    actor_id: str = Field(default="main", min_length=1)
    parent_event_id: UUID | None = None

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class RunStartedEvent(EventBase):
    event_type: Literal["run_started"] = "run_started"
    run_group_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    configuration: Configuration
    evaluation_scope: EvaluationScope = EvaluationScope.PRIMARY
    provider: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    model_parameters: dict[str, Any]
    source_snapshot_id: str = Field(min_length=1)
    pricing_version: str = Field(min_length=1)
    budget: RunBudget
    resumed_from_checkpoint_id: UUID | None = None


class ModelCallEvent(EventBase):
    event_type: Literal["model_call"] = "model_call"
    call_id: str = Field(min_length=1)
    status: CallStatus
    requested_model: str = Field(min_length=1)
    returned_model: str | None = None
    system_fingerprint: str | None = None
    provider_request_id: str | None = None
    request_messages: list[ChatMessage]
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    response_content: str | list[dict[str, Any]] | None = None
    response_tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    usage: ModelUsage | None = None
    cost: CallCost = Field(default_factory=CallCost)
    latency_ms: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_call_result(self) -> ModelCallEvent:
        if self.status is CallStatus.SUCCESS:
            if not self.returned_model or self.usage is None or not self.provider_request_id:
                raise ValueError(
                    "successful model call requires returned_model, usage, and provider_request_id"
                )
        elif not self.error_type:
            raise ValueError("failed model call requires error_type")
        return self


class ToolExecutionEvent(EventBase):
    event_type: Literal["tool_execution"] = "tool_execution"
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: CallStatus
    arguments: dict[str, Any]
    result: Any | None = None
    latency_ms: int = Field(ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    source_ids: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_tool_result(self) -> ToolExecutionEvent:
        if self.status is CallStatus.ERROR and not self.error_type:
            raise ValueError("failed tool execution requires error_type")
        return self


class EvidenceRecordedEvent(EventBase):
    event_type: Literal["evidence_recorded"] = "evidence_recorded"
    evidence_id: UUID
    source_id: str = Field(min_length=1)


class PermissionDecisionEvent(EventBase):
    event_type: Literal["permission_decision"] = "permission_decision"
    action_name: str = Field(min_length=1)
    requested_action: PermissionAction
    policy_action: PermissionAction
    verdict: PermissionVerdict
    user_approved: bool | None = None


class CompactionEvent(EventBase):
    event_type: Literal["compaction"] = "compaction"
    input_tokens: int = Field(gt=0)
    output_tokens: int = Field(ge=0)
    preserved_fact_ids: list[str] = Field(default_factory=list)
    required_fact_ids: list[str] = Field(default_factory=list)


class SubagentHandoffEvent(EventBase):
    event_type: Literal["subagent_handoff"] = "subagent_handoff"
    subagent_id: str = Field(min_length=1)
    assigned_question: str = Field(min_length=1)
    conclusion: str
    evidence_ids: list[UUID]
    confidence: float = Field(ge=0.0, le=1.0)
    contradictions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    failed_actions: list[str] = Field(default_factory=list)
    serialized_bytes: int = Field(ge=0)


class CheckpointState(StrictModel):
    plan: list[str]
    completed_steps: list[str]
    unresolved_questions: list[str]
    evidence_ids: list[UUID]
    source_to_claim: dict[str, list[str]]
    user_constraints: list[str]
    permission_decision_event_ids: list[UUID]
    failures: list[str]
    retry_state: dict[str, Any]


class CheckpointEvent(EventBase):
    event_type: Literal["checkpoint"] = "checkpoint"
    checkpoint_id: UUID = Field(default_factory=uuid4)
    state_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: CheckpointState


class RecoveryEvent(EventBase):
    event_type: Literal["recovery"] = "recovery"
    checkpoint_id: UUID
    restored: bool
    repeated_gated_actions: int = Field(ge=0)
    completed_within_remaining_budget: bool


class FinalReportEvent(EventBase):
    event_type: Literal["final_report"] = "final_report"
    artifact_path: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cited_evidence_ids: list[UUID]
    produced_within_budget: bool


class EvaluationEvent(EventBase):
    event_type: Literal["evaluation"] = "evaluation"
    status: EvaluationStatus
    included_in_egtsr_denominator: bool
    task_success: bool | None = None
    required_claims_total: int = Field(ge=0)
    supported_required_claims: int = Field(ge=0)
    citations_total: int = Field(ge=0)
    supported_citations: int = Field(ge=0)
    entailed_citations: int = Field(ge=0)
    factual_correctness_passed: bool | None = None
    critical_policy_violations: int = Field(ge=0)
    final_artifact_within_budget: bool
    unsupported_claim_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts_and_denominator(self) -> EvaluationEvent:
        if self.supported_required_claims > self.required_claims_total:
            raise ValueError("supported_required_claims exceeds required_claims_total")
        if self.supported_citations > self.citations_total:
            raise ValueError("supported_citations exceeds citations_total")
        if self.entailed_citations > self.citations_total:
            raise ValueError("entailed_citations exceeds citations_total")
        if self.included_in_egtsr_denominator and self.task_success is None:
            raise ValueError("denominator-included evaluation requires task_success")
        if self.task_success:
            if self.factual_correctness_passed is not True:
                raise ValueError("successful task requires factual_correctness_passed")
            if self.critical_policy_violations:
                raise ValueError("successful task cannot have critical policy violations")
            if not self.final_artifact_within_budget:
                raise ValueError("successful task requires final artifact within budget")
        return self


class RunEndedEvent(EventBase):
    event_type: Literal["run_ended"] = "run_ended"
    status: EvaluationStatus
    duration_ms: int = Field(ge=0)
    failure_label: str | None = None


TraceEvent = Annotated[
    RunStartedEvent
    | ModelCallEvent
    | ToolExecutionEvent
    | EvidenceRecordedEvent
    | PermissionDecisionEvent
    | CompactionEvent
    | SubagentHandoffEvent
    | CheckpointEvent
    | RecoveryEvent
    | FinalReportEvent
    | EvaluationEvent
    | RunEndedEvent,
    Field(discriminator="event_type"),
]

TRACE_EVENT_ADAPTER = TypeAdapter(TraceEvent)
