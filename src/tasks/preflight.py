"""Exact live-text preflight for draft-task evidence patterns."""

from __future__ import annotations

import hashlib
from typing import Any

from .models import BenchmarkTask


def audit_evidence_patterns(
    tasks: list[BenchmarkTask],
    source_texts: dict[str, str],
) -> dict[str, Any]:
    """Check each evidence pattern against its claim-allowed source texts.

    Matching deliberately mirrors the deterministic evaluator: Unicode
    case-folding only. Hyphens, whitespace, and punctuation are not normalized.
    """

    tasks = [
        BenchmarkTask.model_validate(task.model_dump()) for task in tasks
    ]
    required_source_ids = {
        source_id
        for task in tasks
        for claim in task.required_claims
        for source_id in claim.acceptable_source_ids
    }
    missing_source_texts = sorted(required_source_ids - set(source_texts))
    if missing_source_texts:
        raise ValueError(
            f"missing source text for source IDs: {missing_source_texts}"
        )

    normalized_sources = {
        source_id: text.casefold() for source_id, text in source_texts.items()
    }
    findings: list[dict[str, Any]] = []
    claim_summaries: list[dict[str, Any]] = []
    for task in tasks:
        for claim in task.required_claims:
            claim_findings = []
            for pattern in claim.evidence_patterns:
                matched_source_ids = [
                    source_id
                    for source_id in claim.acceptable_source_ids
                    if pattern.casefold() in normalized_sources[source_id]
                ]
                finding = {
                    "task_id": task.task_id,
                    "claim_id": claim.claim_id,
                    "pattern": pattern,
                    "matched_source_ids": matched_source_ids,
                    "matched": bool(matched_source_ids),
                }
                findings.append(finding)
                claim_findings.append(finding)
            matched_patterns = sum(
                finding["matched"] for finding in claim_findings
            )
            claim_summaries.append(
                {
                    "task_id": task.task_id,
                    "claim_id": claim.claim_id,
                    "patterns": len(claim_findings),
                    "matched_patterns": matched_patterns,
                    "passed": matched_patterns == len(claim_findings),
                }
            )

    missing = [finding for finding in findings if not finding["matched"]]
    return {
        "passed": not missing,
        "tasks": len(tasks),
        "patterns": len(findings),
        "matched_patterns": len(findings) - len(missing),
        "missing_patterns": len(missing),
        "source_hashes": {
            source_id: "sha256:"
            + hashlib.sha256(text.encode("utf-8")).hexdigest()
            for source_id, text in sorted(source_texts.items())
        },
        "claims": claim_summaries,
        "missing": missing,
    }
