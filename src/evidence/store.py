"""Append-only JSONL evidence store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

from .models import EvidenceRecord


class EvidenceStoreError(ValueError):
    """Evidence-store validation or consistency failure."""


class EvidenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: dict[UUID, EvidenceRecord] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                record = EvidenceRecord.model_validate_json(line)
            except Exception as exc:
                raise EvidenceStoreError(
                    f"{self.path}:{line_number}: invalid evidence record: {exc}"
                ) from exc
            if record.evidence_id in self._records:
                raise EvidenceStoreError(
                    f"{self.path}:{line_number}: duplicate evidence_id {record.evidence_id}"
                )
            self._records[record.evidence_id] = record

    def append(self, record: EvidenceRecord) -> None:
        if record.evidence_id in self._records:
            raise EvidenceStoreError(f"duplicate evidence_id {record.evidence_id}")
        if self._records:
            run_ids = {item.run_id for item in self._records.values()}
            if run_ids != {record.run_id}:
                raise EvidenceStoreError(
                    f"evidence store contains run_ids={sorted(map(str, run_ids))}; "
                    f"cannot append run_id={record.run_id}"
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.model_dump_json()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records[record.evidence_id] = record

    def get(self, evidence_id: UUID) -> EvidenceRecord:
        try:
            return self._records[evidence_id]
        except KeyError as exc:
            raise EvidenceStoreError(f"unknown evidence_id {evidence_id}") from exc

    def all(self) -> list[EvidenceRecord]:
        return list(self._records.values())

    def ids(self) -> set[UUID]:
        return set(self._records)

    def validate_run(self, run_id: UUID) -> None:
        wrong = [record.evidence_id for record in self._records.values() if record.run_id != run_id]
        if wrong:
            raise EvidenceStoreError(
                f"evidence records belong to another run: {list(map(str, wrong))}"
            )

    def to_json(self) -> str:
        return json.dumps(
            [record.model_dump(mode="json") for record in self.all()],
            indent=2,
            ensure_ascii=False,
        )
