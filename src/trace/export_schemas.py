"""Export canonical JSON Schemas used by external tooling."""

from __future__ import annotations

import json
from pathlib import Path

from src.evidence.models import EvidenceRecord
from src.snapshots.models import SnapshotManifest

from .models import TRACE_EVENT_ADAPTER

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    schemas = {
        "trace-event.schema.json": TRACE_EVENT_ADAPTER.json_schema(),
        "evidence-record.schema.json": EvidenceRecord.model_json_schema(),
        "snapshot-manifest.schema.json": SnapshotManifest.model_json_schema(),
    }
    for filename, schema in schemas.items():
        path = SCHEMA_DIR / filename
        path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(path.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
