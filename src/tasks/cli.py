"""Validate benchmark tasks and generate a deduplicated source-freeze plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .load import load_task
from .models import BenchmarkTask
from .preflight import audit_evidence_patterns


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "freeze-plan"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("tasks", nargs="+", type=Path)
    preflight_parser = subparsers.add_parser("preflight-patterns")
    preflight_parser.add_argument("tasks", nargs="+", type=Path)
    preflight_parser.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="SOURCE_ID=PATH",
        help="map a task source ID to fetched or frozen source text",
    )
    return parser


def build_freeze_plan(tasks: list[BenchmarkTask]) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for requirement in task.source_requirements:
            payload = {
                "source_id": requirement.source_id,
                "source_type": requirement.source_type,
                "canonical_url": str(requirement.canonical_url),
                "url_checked_at": requirement.url_checked_at.isoformat(),
            }
            current = sources.get(requirement.source_id)
            if current is not None:
                for field in (
                    "source_type",
                    "canonical_url",
                    "url_checked_at",
                ):
                    if current[field] != payload[field]:
                        raise ValueError(
                            f"conflicting {field} for {requirement.source_id}"
                        )
            else:
                current = {
                    **payload,
                    "required_topics": set(),
                    "referenced_by_tasks": set(),
                    "selection_rationales": set(),
                }
                sources[requirement.source_id] = current
            current["required_topics"].update(requirement.required_topics)
            current["referenced_by_tasks"].add(task.task_id)
            current["selection_rationales"].add(
                requirement.selection_rationale
            )

    serialized_sources = []
    for source_id in sorted(sources):
        source = sources[source_id]
        serialized_sources.append(
            {
                "source_id": source["source_id"],
                "source_type": source["source_type"],
                "canonical_url": source["canonical_url"],
                "url_checked_at": source["url_checked_at"],
                "required_topics": sorted(source["required_topics"]),
                "referenced_by_tasks": sorted(source["referenced_by_tasks"]),
                "selection_rationales": sorted(
                    source["selection_rationales"]
                ),
            }
        )
    return {
        "task_count": len(tasks),
        "source_count": len(serialized_sources),
        "all_tasks_frozen": all(task.lifecycle.value == "frozen" for task in tasks),
        "tasks": [
            {
                "task_id": task.task_id,
                "task_version": task.task_version,
                "lifecycle": task.lifecycle,
                "required_claims": len(task.required_claims),
            }
            for task in tasks
        ],
        "sources": serialized_sources,
    }


def load_source_texts(specs: list[str]) -> dict[str, str]:
    source_texts: dict[str, str] = {}
    for spec in specs:
        source_id, separator, raw_path = spec.partition("=")
        if not separator or not source_id or not raw_path:
            raise ValueError(
                f"invalid --source {spec!r}; expected SOURCE_ID=PATH"
            )
        if source_id in source_texts:
            raise ValueError(f"duplicate --source mapping for {source_id}")
        source_texts[source_id] = Path(raw_path).read_text(encoding="utf-8")
    return source_texts


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tasks = [load_task(path) for path in args.tasks]
    if args.command == "validate":
        payload = {
            "valid": True,
            "tasks": [
                {
                    "path": str(path),
                    "task_id": task.task_id,
                    "lifecycle": task.lifecycle,
                    "claims": len(task.required_claims),
                    "sources": len(task.source_requirements),
                }
                for path, task in zip(args.tasks, tasks, strict=True)
            ],
        }
    elif args.command == "freeze-plan":
        payload = build_freeze_plan(tasks)
    else:
        payload = audit_evidence_patterns(
            tasks,
            load_source_texts(args.source),
        )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
