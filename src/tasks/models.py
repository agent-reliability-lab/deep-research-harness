"""Versioned benchmark task and rubric schemas."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import AnyUrl, ConfigDict, Field, field_validator, model_validator
from pydantic.main import BaseModel

from src.snapshots.models import SourceType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskFamily(StrEnum):
    ARCHITECTURE_COMPARISON = "architecture_comparison"
    MEMORY_LIFECYCLE = "memory_lifecycle"
    RETRIEVAL_RANKING = "retrieval_ranking"
    OPERATIONS = "operations"
    PRODUCT_RECOMMENDATION = "product_recommendation"


class EvaluationMode(StrEnum):
    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    DETERMINISTIC_BENCHMARK = "deterministic_benchmark"
    JUDGE_REQUIRED = "judge_required"


class TaskLifecycle(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"


class ClaimVerificationStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"


class ClaimScoringMethod(StrEnum):
    PATTERN_CONTRACT = "pattern_contract"
    LLM_JUDGE = "llm_judge"


class SourceRequirement(StrictModel):
    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_type: SourceType
    canonical_url: AnyUrl
    url_checked_at: date
    required_topics: list[str] = Field(min_length=1)
    selection_rationale: str = Field(min_length=1)

    @field_validator("required_topics")
    @classmethod
    def require_unique_topics(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values)


class RequiredClaim(StrictModel):
    claim_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str = Field(min_length=1)
    verification_status: ClaimVerificationStatus
    scoring_method: ClaimScoringMethod
    acceptable_source_ids: list[str] = Field(min_length=1)
    evidence_patterns: list[str] = Field(min_length=1)
    answer_pattern_groups: list[list[str]] = Field(min_length=1)

    @field_validator(
        "acceptable_source_ids",
        "evidence_patterns",
    )
    @classmethod
    def require_unique_nonempty_values(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values)

    @field_validator("answer_pattern_groups")
    @classmethod
    def require_nonempty_answer_pattern_groups(
        cls,
        groups: list[list[str]],
    ) -> list[list[str]]:
        return [_unique_nonempty(group) for group in groups]


class CitationExpectations(StrictModel):
    minimum_citations: int = Field(ge=1)
    minimum_unique_sources: int = Field(ge=1)


class TaskRubric(StrictModel):
    minimum_required_claim_coverage: float = Field(ge=0, le=1)
    minimum_citation_precision: float = Field(ge=0, le=1)
    minimum_citation_entailment: float = Field(ge=0, le=1)
    maximum_distractor_mentions: int = Field(default=0, ge=0)


class BenchmarkTask(StrictModel):
    schema_version: str = "0.4.0"
    task_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    task_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    rubric_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    lifecycle: TaskLifecycle
    family: TaskFamily
    prompt: str = Field(min_length=1)
    fixture_only: bool
    evaluation_mode: EvaluationMode
    source_snapshot_id: str | None
    source_requirements: list[SourceRequirement] = Field(min_length=1)
    required_claims: list[RequiredClaim] = Field(min_length=1)
    acceptable_source_ids: list[str] = Field(min_length=1)
    known_distractors: list[str] = Field(default_factory=list)
    required_comparison_dimensions: list[str] = Field(default_factory=list)
    citation_expectations: CitationExpectations
    rubric: TaskRubric

    @field_validator(
        "acceptable_source_ids",
        "known_distractors",
        "required_comparison_dimensions",
    )
    @classmethod
    def require_unique_values(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values)

    @model_validator(mode="after")
    def validate_task_contract(self) -> BenchmarkTask:
        claim_ids = [claim.claim_id for claim in self.required_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("required claim IDs must be unique")
        requirement_ids = [
            requirement.source_id for requirement in self.source_requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("source requirement IDs must be unique")
        allowed = set(self.acceptable_source_ids)
        if set(requirement_ids) != allowed:
            raise ValueError(
                "source_requirements must exactly match acceptable_source_ids"
            )
        for claim in self.required_claims:
            unknown = set(claim.acceptable_source_ids) - allowed
            if unknown:
                raise ValueError(
                    f"claim {claim.claim_id} references task-disallowed sources: "
                    f"{sorted(unknown)}"
                )
        if (
            self.fixture_only
            and self.evaluation_mode is not EvaluationMode.DETERMINISTIC_FIXTURE
        ):
            raise ValueError(
                "fixture_only tasks require deterministic_fixture evaluation"
            )
        if (
            not self.fixture_only
            and self.evaluation_mode is EvaluationMode.DETERMINISTIC_FIXTURE
        ):
            raise ValueError(
                "deterministic_fixture evaluation is only valid for fixtures"
            )
        deterministic_modes = {
            EvaluationMode.DETERMINISTIC_FIXTURE,
            EvaluationMode.DETERMINISTIC_BENCHMARK,
        }
        expected_scoring_method = (
            ClaimScoringMethod.PATTERN_CONTRACT
            if self.evaluation_mode in deterministic_modes
            else ClaimScoringMethod.LLM_JUDGE
        )
        wrong_scoring = [
            claim.claim_id
            for claim in self.required_claims
            if claim.scoring_method is not expected_scoring_method
        ]
        if wrong_scoring:
            raise ValueError(
                f"{self.evaluation_mode.value} tasks contain claims with the "
                f"wrong scoring_method: {wrong_scoring}"
            )
        if self.lifecycle is TaskLifecycle.DRAFT:
            if self.source_snapshot_id is not None:
                raise ValueError("draft tasks cannot pin a source_snapshot_id")
            if self.fixture_only:
                raise ValueError("fixtures must be frozen, not draft")
        else:
            if not self.source_snapshot_id:
                raise ValueError("frozen tasks require source_snapshot_id")
            unverified = [
                claim.claim_id
                for claim in self.required_claims
                if claim.verification_status is not ClaimVerificationStatus.VERIFIED
            ]
            if unverified:
                raise ValueError(
                    f"frozen tasks contain unverified claims: {unverified}"
                )
        if (
            self.citation_expectations.minimum_unique_sources
            > len(self.acceptable_source_ids)
        ):
            raise ValueError(
                "minimum_unique_sources exceeds acceptable source count"
            )
        return self


def _unique_nonempty(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("list values must not be blank")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError("list values must be unique")
    return cleaned
