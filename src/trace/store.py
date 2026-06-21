"""Append-only JSONL trace storage."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from .models import TRACE_EVENT_ADAPTER, TraceEvent


class TraceStoreError(ValueError):
    """Trace persistence or parsing failure."""


class TraceWriter:
    def __init__(self, path: str | Path, run_id: UUID) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._next_sequence = 0
        if self.path.exists():
            events = TraceReader(self.path).read_all()
            if events:
                run_ids = {event.run_id for event in events}
                if run_ids != {run_id}:
                    raise TraceStoreError(
                        f"trace contains run_ids={sorted(map(str, run_ids))}, not run_id={run_id}"
                    )
                self._next_sequence = events[-1].sequence + 1

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    def append(self, event: TraceEvent) -> None:
        if event.run_id != self.run_id:
            raise TraceStoreError(
                f"event run_id={event.run_id} does not match writer run_id={self.run_id}"
            )
        if event.sequence != self._next_sequence:
            raise TraceStoreError(f"expected sequence {self._next_sequence}, got {event.sequence}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = TRACE_EVENT_ADAPTER.dump_json(event).decode("utf-8")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._next_sequence += 1


class TraceReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def iter_events(self) -> Iterable[TraceEvent]:
        if not self.path.exists():
            raise TraceStoreError(f"trace does not exist: {self.path}")
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    yield TRACE_EVENT_ADAPTER.validate_json(line)
                except Exception as exc:
                    raise TraceStoreError(
                        f"{self.path}:{line_number}: invalid trace event: {exc}"
                    ) from exc

    def read_all(self) -> list[TraceEvent]:
        return list(self.iter_events())
