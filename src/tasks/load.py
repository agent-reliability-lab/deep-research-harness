"""Task loading helpers."""

from __future__ import annotations

from pathlib import Path

from .models import BenchmarkTask


def load_task(path: str | Path) -> BenchmarkTask:
    path = Path(path)
    return BenchmarkTask.model_validate_json(path.read_text(encoding="utf-8"))
