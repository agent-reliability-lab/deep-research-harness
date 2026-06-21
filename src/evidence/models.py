"""Evidence schema required by spec section 8."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import AnyUrl, ConfigDict, Field, field_validator
from pydantic.main import BaseModel


class EvidenceRecord(BaseModel):
    """One claim grounded in one frozen source.

    The seven spec-mandated fields are required. ``source_date`` is nullable
    because official documentation can be undated, but the key is never absent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1.0"
    evidence_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_url: AnyUrl
    retrieved_at: datetime
    evidence_excerpt: str = Field(min_length=1)
    source_date: date | None
    confidence: float = Field(ge=0.0, le=1.0)
    source_content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    created_event_id: UUID | None = None

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value
