"""Versioned benchmark task and rubric schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.main import BaseModel


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
    JUDGE_REQUIRED = "judge_required"


class RequiredClaim(StrictModel):
    claim_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str = Field(min_length=1)
    acceptable_source_ids: list[str] = Field(min_length=1)
    evidence_patterns: list[str] = Field(min_length=1)
    answer_patterns: list[str] = Field(min_length=1)

    @field_validator(
        "acceptable_source_ids",
        "evidence_patterns",
        "answer_patterns",
    )
    @classmethod
    def require_unique_nonempty_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be blank")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned


class CitationExpectations(StrictModel):
    minimum_citations: int = Field(ge=1)
    minimum_unique_sources: int = Field(ge=1)


class TaskRubric(StrictModel):
    minimum_required_claim_coverage: float = Field(ge=0, le=1)
    minimum_citation_precision: float = Field(ge=0, le=1)
    minimum_citation_entailment: float = Field(ge=0, le=1)
    maximum_distractor_mentions: int = Field(default=0, ge=0)


class BenchmarkTask(StrictModel):
    schema_version: str = "0.1.0"
    task_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    family: TaskFamily
    prompt: str = Field(min_length=1)
    fixture_only: bool
    evaluation_mode: EvaluationMode
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
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("list values must not be blank")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_task_contract(self) -> BenchmarkTask:
        claim_ids = [claim.claim_id for claim in self.required_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("required claim IDs must be unique")
        allowed = set(self.acceptable_source_ids)
        for claim in self.required_claims:
            unknown = set(claim.acceptable_source_ids) - allowed
            if unknown:
                raise ValueError(
                    f"claim {claim.claim_id} references task-disallowed sources: "
                    f"{sorted(unknown)}"
                )
        if self.fixture_only and self.evaluation_mode is not EvaluationMode.DETERMINISTIC_FIXTURE:
            raise ValueError("fixture_only tasks require deterministic_fixture evaluation")
        if (
            not self.fixture_only
            and self.evaluation_mode is EvaluationMode.DETERMINISTIC_FIXTURE
        ):
            raise ValueError("deterministic_fixture evaluation is only valid for fixtures")
        return self
