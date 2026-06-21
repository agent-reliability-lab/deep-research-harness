"""Public snapshot manifest and private-cache record schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AnyUrl, ConfigDict, Field, field_validator
from pydantic.main import BaseModel


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    OFFICIAL_DOCS = "official_docs"
    OFFICIAL_REPOSITORY = "official_repository"
    RESEARCH_PAPER = "research_paper"
    THIRD_PARTY_ANALYSIS = "third_party_analysis"


class RedistributionPolicy(StrEnum):
    CACHE_ONLY = "cache_only"
    REDISTRIBUTABLE = "redistributable"


class SourceManifestEntry(StrictModel):
    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=1)
    canonical_url: AnyUrl
    retrieved_at: datetime
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    excerpt: str = Field(min_length=1, max_length=1200)
    source_type: SourceType
    version_or_pub_date: str | None
    redistribution_policy: RedistributionPolicy
    cache_relpath: str = Field(min_length=1)
    language: str = Field(default="en", min_length=2)
    license: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value

    @field_validator("cache_relpath")
    @classmethod
    def require_relative_safe_path(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("cache_relpath must be a safe relative path")
        return value


class SnapshotManifest(StrictModel):
    schema_version: str = "0.1.0"
    snapshot_id: str = Field(min_length=1)
    created_at: datetime
    sources: list[SourceManifestEntry]

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("sources")
    @classmethod
    def require_unique_source_ids(
        cls,
        sources: list[SourceManifestEntry],
    ) -> list[SourceManifestEntry]:
        ids = [source.source_id for source in sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return sources


class CachedSource(StrictModel):
    schema_version: str = "0.1.0"
    source_id: str = Field(min_length=1)
    cleaned_text: str = Field(min_length=1)
